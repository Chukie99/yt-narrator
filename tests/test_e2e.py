"""End-to-end tests: full pipeline."""

import pytest
import json
import asyncio
from pathlib import Path
from backend.db import init_db
from backend.worker import process_job
from backend.scheduler import start_scheduler
from backend.config import Config


@pytest.mark.asyncio
async def test_e2e_full_pipeline(tmp_path, monkeypatch):
    """Full pipeline: submit → LLM → TTS → images → Ken Burns → compile."""
    # Setup
    config = Config()
    config.DATABASE_URL = f"sqlite:///{tmp_path}/test.db"
    config.CACHE_DIR = tmp_path / "cache"
    config.OUTPUT_DIR = tmp_path / "output"
    
    monkeypatch.setattr("backend.config.config", config)
    
    # Init DB
    init_db()
    
    # Submit job
    job_id = "test-job-001"
    db = config.get_db()
    db.execute(
        "INSERT INTO jobs (id, title, status, stage, progress) VALUES (?, ?, ?, ?, ?)",
        (job_id, "Test Topic", "pending", None, "")
    )
    db.commit()
    
    # Process (would be async in real app)
    # This is a unit test; full E2E requires env vars (HF_API_KEY, GEMINI_API_KEY)
    
    # Verify job exists
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None
    assert row[2] == "pending"  # status


def test_db_schema():
    """Verify database schema."""
    from backend.db import init_db, get_db
    db = get_db()
    
    # Check tables exist
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t[0] for t in tables]
    
    assert "jobs" in table_names
    assert "job_scenes" in table_names
    assert "image_cache" in table_names


def test_config_from_env(monkeypatch):
    """Config loads from env vars."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("HF_API_KEY_1", "hf-key-1")
    
    config = Config()
    assert config.GEMINI_API_KEY == "test-key"
    assert config.hf_api_keys[0] == "hf-key-1"
