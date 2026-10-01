"""End-to-end test against the live APIs. Not part of the normal suite.

Run explicitly:  python -m pytest tests/test_full_live.py -v -s

This spends real Gemini tokens and real HF image credits, so it is skipped by
default. It exists because every bug fixed so far was invisible in unit tests
and only appeared once the real providers answered.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def _has_key(name: str) -> bool:
    import os

    return bool(os.getenv(name))


pytestmark = [
    pytest.mark.skipif(
        not _has_key("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set",
    ),
    pytest.mark.skipif(
        not (ROOT / "data" / "keys.json").exists(),
        reason="no HF keys in data/keys.json",
    ),
]


@pytest.mark.asyncio
async def test_live_gemini_produces_usable_scenes(tmp_path):
    """A real narration call must yield scenes with both fields filled."""
    from backend.providers.gemini_llm import GeminiLLM

    llm = GeminiLLM()
    out = await llm.generate_narasi("Kenapa biskuit dulu mahal banget?")

    scenes = out["scenes"]
    assert len(scenes) >= 3, f"only {len(scenes)} scenes"

    for i, scene in enumerate(scenes, 1):
        narration = scene.get("narration_text", "")
        prompt = scene.get("t2i_prompt", "")
        assert len(narration) > 40, f"scene {i} narration too short: {narration!r}"
        assert len(prompt) > 20, f"scene {i} t2i_prompt too short: {prompt!r}"
        # The narration is Indonesian; the image prompt is English.
        assert not any(c in narration for c in "，。"), "narration has CJK punctuation"

    (tmp_path / "narasi.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_live_style_bible_has_a_style_prefix(tmp_path):
    from backend.providers.gemini_llm import GeminiLLM

    llm = GeminiLLM()
    bible = await llm.generate_style_bible(
        "Penjelasan tentang sejarah biskuit dan overpriced snacks di abad ke-19."
    )

    assert "style_prefix" in bible, f"missing style_prefix, got {list(bible)}"
    assert bible["style_prefix"], "style_prefix is empty"


@pytest.mark.asyncio
async def test_live_flux_generates_a_1920x1080_image(tmp_path):
    """The resolution the pipeline actually requests."""
    from backend.keystore import KeyStore
    from backend.providers.hf_inference import HFInferenceImage

    store = KeyStore(ROOT / "data" / "keys.json")
    provider = HFInferenceImage(store=store)

    out = await provider.generate(
        "ink sketch of an ancient Roman apartment building, sepia ink on parchment",
        out_path=tmp_path / "live.png",
    )

    assert out.exists()
    size = out.stat().st_size
    assert size > 50_000, f"image suspiciously small: {size} bytes"

    from PIL import Image

    with Image.open(out) as im:
        assert im.size == (1920, 1080), f"wrong size: {im.size}"


@pytest.mark.asyncio
async def test_live_tts_produces_indonesian_audio(tmp_path):
    from backend.providers.edge_tts import EdgeTTSProvider

    tts = EdgeTTSProvider()
    path, duration = await tts.synthesize(
        "Biskuit pada zaman dulu dibuat untuk bangsawan, bukan untuk anak-anak.",
        tmp_path / "live.wav",
    )

    assert path.exists()
    assert 1.0 < duration < 60.0, f"implausible duration: {duration}"

    import wave

    with wave.open(str(path)) as w:
        # edge-tts serves 24kHz mono; the worker resamples to 44100 mono at
        # the concat stage, so either rate is correct here.
        assert w.getframerate() in (24000, 44100), f"unexpected rate: {w.getframerate()}"
        assert w.getnchannels() == 1, f"expected mono, got {w.getnchannels()} channels"


@pytest.mark.asyncio
async def test_live_pipeline_reaches_done(tmp_path, monkeypatch):
    """The whole 8 stages against real providers, DB and paths in tmp."""
    import backend.worker as worker
    from backend import config as cfg
    from backend import db as db_module
    from backend.db import get_db, init_db

    monkeypatch.setattr(worker, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path / "cache")

    db_file = tmp_path / "live.db"
    init_db(db_file)
    db_module.set_db_path(db_file)

    job_id = "live-test-job"
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id, topic, status, revision, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (job_id, "Kenapa biskuit dulu mahal banget?", "pending", 1),
        )

    await worker.process_job(job_id)

    with get_db() as db:
        status, stage, error = db.execute(
            "SELECT status, stage, error_msg FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    assert status == "done", f"failed at {stage}: {error}"

    final = tmp_path / "outputs" / job_id / "r1" / "final.mp4"
    assert final.exists(), "final.mp4 missing"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "json", str(final)],
        capture_output=True, text=True,
    )
    info = json.loads(probe.stdout or "{}")
    durations = [float(s["duration"]) for s in info.get("streams", [])]
    assert len(durations) == 2, f"expected video+audio, got {info.get('streams')}"
    assert abs(durations[0] - durations[1]) < 0.15, f"drift: {durations}"

    db_module.set_db_path(cfg.DB_PATH)