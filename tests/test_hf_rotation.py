"""Image generation must survive one exhausted key by trying the next.

Regression: a 402 (out of credits) on the first key killed the whole job even
though other keys in the pool were fine.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.keystore import KeyStore
from backend.providers import hf_inference
from backend.providers.hf_inference import HFInferenceImage


class FakeImage:
    def __init__(self):
        self.saved_to = None

    def save(self, path):
        self.saved_to = path
        Path(path).write_bytes(b"PNG" + b"0" * 2000)


class FakeClient:
    """Raises per-token errors from a table keyed by token value."""

    failures = {}
    calls = []
    kwargs = []

    def __init__(self, api_key=None):
        self.api_key = api_key

    def text_to_image(self, prompt=None, model=None, height=None, width=None, extra_body=None):
        FakeClient.calls.append(self.api_key)
        FakeClient.kwargs.append(extra_body)
        err = FakeClient.failures.get(self.api_key)
        if err:
            raise err
        return FakeImage()


@pytest.fixture
def fake_hf(monkeypatch, tmp_path):
    FakeClient.failures = {}
    FakeClient.calls = []
    FakeClient.kwargs = []
    fake_mod = type("H", (), {"InferenceClient": FakeClient})
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)

    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["key_depleted", "key_good", "key_spare"])
    return store


@pytest.mark.asyncio
async def test_uses_next_key_when_one_is_out_of_credits(fake_hf, tmp_path):
    FakeClient.failures["key_depleted"] = RuntimeError(
        "Client error '402 Payment Required': depleted monthly credits"
    )
    provider = HFInferenceImage(store=fake_hf)

    out = await provider.generate("an ink sketch", out_path=tmp_path / "a.png")

    assert out.exists()
    assert FakeClient.calls == ["key_depleted", "key_good"]
    assert out.stat().st_size > 1000


@pytest.mark.asyncio
async def test_tries_every_key_before_giving_up(fake_hf, tmp_path):
    for token in ("key_depleted", "key_good", "key_spare"):
        FakeClient.failures[token] = RuntimeError("429 Too Many Requests")

    provider = HFInferenceImage(store=fake_hf)
    with pytest.raises(RuntimeError, match="Semua 3 kunci HF gagal"):
        await provider.generate("x", out_path=tmp_path / "b.png")

    assert len(FakeClient.calls) == 3


@pytest.mark.asyncio
async def test_non_key_error_does_not_burn_the_pool(fake_hf, tmp_path):
    """A bad prompt fails on every key, so only the first should be tried."""
    FakeClient.failures["key_depleted"] = RuntimeError("401 Unauthorized")

    provider = HFInferenceImage(store=fake_hf)
    with pytest.raises(RuntimeError):
        await provider.generate("x", out_path=tmp_path / "c.png")

    assert FakeClient.calls == ["key_depleted"], "should not rotate on a non-quota error"


@pytest.mark.asyncio
async def test_cached_image_skips_the_api_entirely(fake_hf, tmp_path):
    out = tmp_path / "cached.png"
    out.write_bytes(b"PNG" + b"0" * 2000)

    provider = HFInferenceImage(store=fake_hf)
    result = await provider.generate("same prompt", out_path=out)

    assert result == out
    assert FakeClient.calls == [], "cache hit still called the API"


@pytest.mark.asyncio
async def test_free_provider_is_pinned_not_left_to_the_router(fake_hf, tmp_path):
    """The router defaults to nscale, which 402s on free accounts.

    Regression guard: without pinning, image generation fails on a key that
    works perfectly well when the provider is specified.
    """
    provider = HFInferenceImage(store=fake_hf)
    await provider.generate("x", out_path=tmp_path / "e.png")

    assert FakeClient.kwargs[0] == {"provider": "fal-ai"}


@pytest.mark.asyncio
async def test_disabled_keys_are_skipped(fake_hf, tmp_path):
    fake_hf.set_enabled(0, False)  # key_depleted off
    FakeClient.failures["key_good"] = RuntimeError("429 Too Many Requests")

    provider = HFInferenceImage(store=fake_hf)
    out = await provider.generate("x", out_path=tmp_path / "d.png")

    assert out.exists()
    assert "key_depleted" not in FakeClient.calls