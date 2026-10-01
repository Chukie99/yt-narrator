"""Shared fixtures: a job wired to tmp paths with a stubbed LLM.

The stub keeps pipeline tests free of a Gemini key; everything downstream of
the LLM (TTS, images, motion, compile) still runs for real.
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import config as cfg
from backend.db import get_db, init_db

NARASI = (
    "Insula adalah rumah tunggal di Romawi kuno. "
    "Bangunan ini punya satu lantai dan satu pintu menghadap jalan. "
    "Keluarga tinggal di dalam bersama hewan peliharaan mereka. "
    "Bahannya dari batu dan adobe, atapnya dari terra cotta."
)


class StubLLM:
    """Returns fixed narration so the rest of the pipeline can be tested."""

    def __init__(self, *a, **kw):
        pass

    async def generate_narasi(self, topic):
        scenes = []
        for i in range(1, 4):
            scenes.append(
                {
                    "narration_text": (
                        f"Bagian {i} dari penjelasan tentang {topic}. "
                        "Ini kalimat kedua untuk adegan ini agar durasinya cukup."
                    ),
                    "t2i_prompt": (
                        f"ink sketch of {topic}, part {i}, hand drawn, "
                        "sepia ink on parchment, wide establishing view"
                    ),
                }
            )
        return {
            "full_narasi": " ".join(s["narration_text"] for s in scenes),
            "scenes": scenes,
        }

    async def generate_style_bible(self, narasi):
        return {"mood": "dokumenter", "color_palette": ["sepia", "cream"]}

    async def generate_metadata(self, narasi):
        return {
            "title": "Judul Uji",
            "description": "Deskripsi uji.",
            "hashtags": ["uji", "sejarah"],
        }


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Point every path at tmp, stub the LLM, return (job_id, tmp_path)."""
    import backend.worker as worker

    monkeypatch.setattr(worker, "GeminiLLM", StubLLM)
    monkeypatch.setattr(worker, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path / "cache")

    init_db()
    job_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id, topic, status, revision, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (job_id, "Sejarahnya insula Hasta", "pending", 1),
        )
    return job_id, tmp_path


def job_state(job_id):
    with get_db() as db:
        return db.execute(
            "SELECT status, stage, error_msg FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()