"""Pollinations provider: keyless image generation.

The measured behaviour this has to survive: back-to-back requests alternate
between a real image and HTTP 402, and the default model routes somewhere
that fails while model=flux works.
"""
import asyncio
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.providers.pollinations import (
    FREE_MAX_HEIGHT,
    FREE_MAX_WIDTH,
    PollinationsImage,
)

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 5000


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_http(monkeypatch):
    """Queue of outcomes: bytes, or an exception to raise."""
    state = {"queue": [], "urls": []}

    def fake_urlopen(request, timeout=None):
        state["urls"].append(request.full_url)
        outcome = state["queue"].pop(0) if state["queue"] else FAKE_JPEG
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    import backend.providers.pollinations as pol

    monkeypatch.setattr(pol.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pol.asyncio, "sleep", _no_sleep)
    return state


async def _no_sleep(_s):
    return None


@pytest.mark.asyncio
async def test_generates_and_writes_a_file(fake_http, tmp_path):
    provider = PollinationsImage()
    out = await provider.generate("an ink sketch", out_path=tmp_path / "a.jpg")

    assert out.exists()
    assert out.stat().st_size == len(FAKE_JPEG)


@pytest.mark.asyncio
async def test_retries_through_402(fake_http, tmp_path):
    """The free tier alternates success and 402; one retry must be enough."""
    fake_http["queue"] = [
        Exception("<urlopen error 402 Payment Required>"),
        Exception("<urlopen error 402 Payment Required>"),
        FAKE_JPEG,
    ]
    provider = PollinationsImage()
    out = await provider.generate("x", out_path=tmp_path / "b.jpg")

    assert out.exists()
    assert len(fake_http["urls"]) == 3


@pytest.mark.asyncio
async def test_gives_up_with_a_readable_error(fake_http, tmp_path):
    fake_http["queue"] = [Exception("<urlopen error 402 Payment Required>")] * 10
    provider = PollinationsImage(max_attempts=3)

    with pytest.raises(RuntimeError, match="gagal setelah 3 percobaan"):
        await provider.generate("x", out_path=tmp_path / "c.jpg")

    assert len(fake_http["urls"]) == 3


@pytest.mark.asyncio
async def test_never_asks_for_more_than_the_free_tier(fake_http, tmp_path):
    """1920x1080 returns 402, so the request must be clamped."""
    provider = PollinationsImage(width=1920, height=1080)
    await provider.generate("x", out_path=tmp_path / "d.jpg")

    url = fake_http["urls"][0]
    assert f"width={FREE_MAX_WIDTH}" in url
    assert f"height={FREE_MAX_HEIGHT}" in url
    assert "1920" not in url


@pytest.mark.asyncio
async def test_pins_flux_model(fake_http, tmp_path):
    """Without model=flux the endpoint routes elsewhere and 402s."""
    provider = PollinationsImage()
    await provider.generate("x", out_path=tmp_path / "e.jpg")

    assert "model=flux" in fake_http["urls"][0]


@pytest.mark.asyncio
async def test_cache_hit_makes_no_request(fake_http, tmp_path):
    out = tmp_path / "cached.jpg"
    out.write_bytes(FAKE_JPEG)

    provider = PollinationsImage()
    result = await provider.generate("same prompt", out_path=out)

    assert result == out
    assert fake_http["urls"] == [], "cache hit still hit the network"


@pytest.mark.asyncio
async def test_same_prompt_hits_the_same_cache_entry(tmp_path):
    """Two scenes with the same prompt must not pay twice."""
    provider = PollinationsImage()
    a = provider._cache_key("same prompt")
    b = provider._cache_key("same prompt")
    c = provider._cache_key("different prompt")
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_tiny_response_is_rejected(fake_http, tmp_path):
    """A 200 with an error page must not be saved as if it were an image."""
    fake_http["queue"] = [b"too small"]
    provider = PollinationsImage(max_attempts=1)

    with pytest.raises(RuntimeError):
        await provider.generate("x", out_path=tmp_path / "f.jpg")

    assert not (tmp_path / "f.jpg").exists()


@pytest.mark.asyncio
async def test_seeds_differ_per_attempt(fake_http, tmp_path):
    """Retrying the identical request returns the identical 402."""
    fake_http["queue"] = [
        Exception("<urlopen error 402 Payment Required>"),
        FAKE_JPEG,
    ]
    provider = PollinationsImage()
    await provider.generate("x", out_path=tmp_path / "g.jpg")

    seeds = [u.split("seed=")[1].split("&")[0] for u in fake_http["urls"]]
    assert len(set(seeds)) == 2, f"retried with the same seed: {seeds}"