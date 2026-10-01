"""Pytest fixtures."""

import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import config as cfg
from backend.db import get_db, init_db
from backend.keystore import KeyStore


@pytest.fixture
def test_db():
    """Fixture providing a fresh test database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_narrator.db"
        init_db(db_path)
        yield db_path

        # Close any thread-local connections
        import backend.db as db_module
        if hasattr(db_module, '_local'):
            if hasattr(db_module._local, 'connection'):
                db_module._local.connection.close()
                delattr(db_module._local, 'connection')


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
    from backend import db as db_module

    monkeypatch.setattr(worker, "GeminiLLM", StubLLM)
    monkeypatch.setattr(worker, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path / "cache")

    # Own database file: without this these tests insert real jobs into the
    # production DB and lock it out from a running server.
    db_file = tmp_path / "narrator.db"
    init_db(db_file)
    db_module.set_db_path(db_file)

    job_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id, topic, status, revision, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (job_id, "Sejarahnya insula Hasta", "pending", 1),
        )

    yield job_id, tmp_path

    db_module.set_db_path(cfg.DB_PATH)


@pytest.fixture
def hf_keys_available():
    """Skip when no HF key is configured.

    The pipeline tests render images for real (that is the point of them), so
    they need a working key in data/keys.json. Everything else in the suite
    runs without one.
    """
    store = KeyStore(cfg.DATA_DIR / "keys.json")
    try:
        keys = [k for k in store.all() if k["enabled"] and k["token"]]
    except Exception:
        keys = []
    if not keys:
        pytest.skip(
            "no HF key in data/keys.json - add one in the UI (Kunci API panel) "
            "or run `pytest tests/ --ignore=tests/test_pipeline.py`"
        )


@pytest.fixture
def job_state():
    """Return a callable reading a job's (status, stage, error_msg)."""
    def _read(job_id):
        with get_db() as db:
            return db.execute(
                "SELECT status, stage, error_msg FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

    return _read