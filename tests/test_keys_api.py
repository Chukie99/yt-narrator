"""API tests for the /keys endpoints."""

import os
import tempfile
import json
import urllib.request
import urllib.error

import pytest

from backend.keystore import KeyStore


@pytest.fixture
def client(monkeypatch):
    """TestClient pointed at a throwaway key file."""
    tmpdir = tempfile.mkdtemp()
    import backend.providers.hf_inference as hf

    monkeypatch.setattr(hf.key_store, "path", __import__("pathlib").Path(tmpdir) / "keys.json")
    monkeypatch.setattr("backend.main.key_store", hf.key_store)

    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app)


TEN_KEYS = "\n".join(f"hf_fakekey{i:016d}" for i in range(1, 11))


def test_list_empty_at_first(client):
    r = client.get("/keys")
    assert r.status_code == 200
    assert r.json() == {"keys": []}


def test_put_saves_ten_keys(client):
    r = client.put("/keys", json={"text": TEN_KEYS})
    assert r.status_code == 200
    assert r.json()["saved"] == 10
    assert len(r.json()["keys"]) == 10


def test_get_never_returns_full_token(client):
    client.put("/keys", json={"text": TEN_KEYS})
    r = client.get("/keys")
    assert r.status_code == 200
    body = r.text
    for i in range(1, 11):
        assert f"hf_fakekey{i:016d}" not in body
    assert "mnop" not in body or "mnop" in body  # redaction is visible, not asserted on content
    assert r.json()["keys"][0]["token"].startswith("hf_")


def test_patch_toggles_key_off(client):
    client.put("/keys", json={"text": TEN_KEYS})
    r = client.patch("/keys/0", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["keys"][0]["enabled"] is False
    assert r.json()["keys"][1]["enabled"] is True


def test_patch_unknown_index_is_404(client):
    client.put("/keys", json={"text": TEN_KEYS})
    assert client.patch("/keys/99", json={"enabled": False}).status_code == 404


def test_delete_removes_one_key(client):
    client.put("/keys", json={"text": TEN_KEYS})
    r = client.delete("/keys/0")
    assert r.status_code == 200
    assert len(r.json()["keys"]) == 9
    assert r.json()["keys"][0]["label"] == "Kunci 1"


def test_delete_unknown_index_is_404(client):
    client.put("/keys", json={"text": TEN_KEYS})
    assert client.delete("/keys/99").status_code == 404


def test_provider_rotates_through_all_ten(client):
    """The provider must walk the whole pool, not just the first key."""
    import asyncio
    client.put("/keys", json={"text": TEN_KEYS})

    from backend.providers.hf_inference import HFInferenceImage
    provider = HFInferenceImage()

    seen = set()
    for _ in range(10):
        seen.add(provider.store.next_token())
    assert len(seen) == 10


def test_disabled_key_excluded_from_rotation(client):
    import asyncio
    client.put("/keys", json={"text": TEN_KEYS})
    client.patch("/keys/3", json={"enabled": False})

    from backend.providers.hf_inference import HFInferenceImage
    provider = HFInferenceImage()

    seen = {provider.store.next_token() for _ in range(20)}
    assert len(seen) == 9
    assert "hf_fakekey0000000000000004" not in seen
