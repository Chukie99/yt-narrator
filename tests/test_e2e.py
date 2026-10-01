"""Integration checks that the app boots and serves the real flow.

The full pipeline (LLM -> TTS -> image -> motion -> compile) is covered by
spike.py, which needs live API keys. These tests stay offline and check the
wiring instead: the app imports, the key routes work, and a submitted job
lands in the database.
"""

import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    import backend.providers.hf_inference as hf

    monkeypatch.setattr(hf.key_store, "path", tmpdir / "keys.json")
    monkeypatch.setattr("backend.main.key_store", hf.key_store)

    from backend.main import app

    return TestClient(app)


def test_app_imports_and_serves(client):
    assert client.get("/keys").status_code == 200


def test_index_page_is_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "YT Narrator" in r.text
    assert "Kunci API" in r.text


def test_submit_creates_pending_job(client):
    topic = "Sejarahnya insula Hasta"
    r = client.post("/job/submit", json={"topic": topic})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    uuid.UUID(job_id)  # must be a real UUID

    status = client.get(f"/job/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "pending"


def test_submit_rejects_short_topic(client):
    assert client.post("/job/submit", json={"topic": "ab"}).status_code == 422


def test_submit_rejects_blank_topic(client):
    assert client.post("/job/submit", json={"topic": "   "}).status_code == 422


def test_unknown_job_is_404(client):
    assert client.get(f"/job/{uuid.uuid4()}").status_code == 404


def test_malformed_job_id_is_400(client):
    assert client.get("/job/not-a-uuid").status_code == 400


def test_video_download_before_done_is_rejected(client):
    """A finished-only route must refuse an unfinished job, not 500."""
    job_id = client.post("/job/submit", json={"topic": "Awal mula metallurgy"}).json()["job_id"]
    assert client.get(f"/job/{job_id}/video").status_code == 400


def test_cancel_from_pending_is_rejected(client):
    """A job that never started cannot be cancelled."""
    job_id = client.post("/job/submit", json={"topic": "Awal mula agriculture"}).json()["job_id"]
    assert client.post(f"/job/{job_id}/cancel").status_code == 409
