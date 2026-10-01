"""Multi-account rotation: the scenario this app is built for.

With 10 HuggingFace accounts, the expected behaviour is that an exhausted
account is stepped over automatically and the job keeps running. These tests
simulate that without spending anything.

The real distinction that matters: 402 and 429 are per-account conditions, so
rotating helps. A 401 or a missing model is not, and burning ten keys on it
would turn one mistake into a much slower failure.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.keystore import KeyStore
from backend.providers.hf_inference import HFInferenceImage


class FakeImage:
    def save(self, path):
        Path(path).write_bytes(b"PNG" + b"0" * 2000)


class FakeClient:
    """Each token has its own failure mode, like separate real accounts."""

    behaviour = {}
    calls = []

    def __init__(self, api_key=None):
        self.api_key = api_key

    def text_to_image(self, prompt=None, model=None, height=None, width=None, extra_body=None):
        FakeClient.calls.append(self.api_key)
        err = FakeClient.behaviour.get(self.api_key)
        if err:
            raise err
        return FakeImage()


@pytest.fixture
def ten_accounts(monkeypatch, tmp_path):
    FakeClient.behaviour = {}
    FakeClient.calls = []
    monkeypatch.setitem(
        sys.modules, "huggingface_hub",
        type("H", (), {"InferenceClient": FakeClient}),
    )

    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens([f"hf_account{i:02d}" for i in range(1, 11)])
    assert len(store.all()) == 10
    return store


# --- the core promise ----------------------------------------------------

@pytest.mark.asyncio
async def test_steps_over_the_first_three_exhausted_accounts(ten_accounts, tmp_path):
    """The realistic case: several accounts ran dry before others are tried."""
    for i in (1, 2, 3):
        FakeClient.behaviour[f"hf_account{i:02d}"] = RuntimeError(
            "402 Payment Required: depleted monthly included credits"
        )

    provider = HFInferenceImage(store=ten_accounts)
    out = await provider.generate("an ink sketch", out_path=tmp_path / "out.png")

    assert out.exists()
    assert FakeClient.calls == [
        "hf_account01", "hf_account02", "hf_account03", "hf_account04",
    ], f"unexpected call order: {FakeClient.calls}"


@pytest.mark.asyncio
async def test_survives_a_mix_of_402_and_429(ten_accounts, tmp_path):
    """Real accounts fail differently: one out of credits, one rate limited."""
    FakeClient.behaviour["hf_account01"] = RuntimeError("402 Payment Required")
    FakeClient.behaviour["hf_account02"] = RuntimeError("429 Too Many Requests")
    FakeClient.behaviour["hf_account05"] = RuntimeError("503 overloaded")

    provider = HFInferenceImage(store=ten_accounts)
    out = await provider.generate("x", out_path=tmp_path / "b.png")

    assert out.exists()
    assert "hf_account03" in FakeClient.calls


@pytest.mark.asyncio
async def test_tenth_account_is_reachable(ten_accounts, tmp_path):
    """All nine before it are dry: the last one must still be tried."""
    for i in range(1, 10):
        FakeClient.behaviour[f"hf_account{i:02d}"] = RuntimeError("402 Payment Required")

    provider = HFInferenceImage(store=ten_accounts)
    out = await provider.generate("x", out_path=tmp_path / "c.png")

    assert out.exists()
    assert len(FakeClient.calls) == 10
    assert FakeClient.calls[-1] == "hf_account10"


@pytest.mark.asyncio
async def test_error_when_all_ten_are_dry(ten_accounts, tmp_path):
    for i in range(1, 11):
        FakeClient.behaviour[f"hf_account{i:02d}"] = RuntimeError("402 Payment Required")

    provider = HFInferenceImage(store=ten_accounts)
    with pytest.raises(RuntimeError) as exc:
        await provider.generate("x", out_path=tmp_path / "d.png")

    assert "Semua 10 kunci HF gagal" in str(exc.value)
    assert len(FakeClient.calls) == 10


# --- the boundary that keeps rotation honest -----------------------------

@pytest.mark.asyncio
async def test_a_broken_key_does_not_consume_all_ten(ten_accounts, tmp_path):
    """401 is not an account-exhaustion problem.

    Rotating on it would turn one revoked key into ten wasted calls and the
    same failure, just slower and with a more confusing error.
    """
    FakeClient.behaviour["hf_account01"] = RuntimeError("401 Unauthorized: invalid token")

    provider = HFInferenceImage(store=ten_accounts)
    with pytest.raises(RuntimeError, match="HF image generation failed"):
        await provider.generate("x", out_path=tmp_path / "e.png")

    assert FakeClient.calls == ["hf_account01"], (
        f"rotated on a 401: {FakeClient.calls}"
    )


# --- pool management -----------------------------------------------------

@pytest.mark.asyncio
async def test_disabling_one_account_takes_it_out(ten_accounts, tmp_path):
    FakeClient.behaviour["hf_account04"] = RuntimeError("402 Payment Required")
    ten_accounts.set_enabled(3, False)  # account04 off

    provider = HFInferenceImage(store=ten_accounts)
    out = await provider.generate("x", out_path=tmp_path / "f.png")

    assert out.exists()
    assert "hf_account04" not in FakeClient.calls


@pytest.mark.asyncio
async def test_load_spreads_across_accounts(ten_accounts, tmp_path):
    """Ten accounts should share the work, not hammer the first one.

    Rotation only helps if it actually moves; a pool that always starts at
    account01 would hit its limit first every single time.
    """
    provider = HFInferenceImage(store=ten_accounts)
    for i in range(10):
        await provider.generate(f"prompt {i}", out_path=tmp_path / f"img{i}.png")

    used = FakeClient.calls
    assert len(set(used)) == 10, f"only {len(set(used))} distinct accounts used"
    assert used == sorted(used), f"rotation did not advance in order: {used}"


@pytest.mark.asyncio
async def test_cached_prompt_spends_no_credits(ten_accounts, tmp_path):
    """Re-running the same narration must not re-generate images."""
    provider = HFInferenceImage(store=ten_accounts)
    out = tmp_path / "cached.png"
    await provider.generate("same prompt", out_path=out)
    before = len(FakeClient.calls)

    await provider.generate("same prompt", out_path=out)
    assert len(FakeClient.calls) == before, "cache miss regenerated the image"