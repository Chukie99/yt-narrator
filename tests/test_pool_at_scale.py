"""Does the pool actually work at 20 accounts?

The user plans to run 20 HuggingFace accounts, so 20 has to be a tested size
rather than an assumed one. Nothing in the code caps the pool, but "no limit
found in a grep" is not the same as "verified".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.keystore import KeyStore

ACCOUNTS = 20


def tokens(n=ACCOUNTS):
    return [f"hf_account{i:02d}" for i in range(1, n + 1)]


def test_twenty_keys_round_trip_through_the_api(monkeypatch, tmp_path):
    """PUT /keys with 20 tokens, then GET them back redacted."""
    from fastapi.testclient import TestClient
    from backend import config as cfg
    from backend.main import app
    import backend.main as main_module

    # The routes read a module-level KeyStore built from DATA_DIR. Redirect it
    # to tmp, otherwise this test overwrites the real data/keys.json.
    test_store = KeyStore(tmp_path / "keys.json")
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    for module in (main_module,):
        if hasattr(module, "key_store"):
            monkeypatch.setattr(module, "key_store", test_store)

    secrets = [f"hf_account{i:02d}_secret{i:04d}" for i in range(1, ACCOUNTS + 1)]

    with TestClient(app) as client:
        res = client.put("/keys", json={"text": "\n".join(secrets)})
        assert res.status_code == 200, res.text
        assert res.json()["saved"] == ACCOUNTS

        listing = client.get("/keys").json()["keys"]
        assert len(listing) == ACCOUNTS

        # No token may reach the browser whole.
        body = res.text + client.get("/keys").text
        for token in secrets:
            assert token not in body, f"token leaked to the browser: {token}"


def test_twenty_keys_all_usable_and_unique(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(tokens())

    assert len(store.all()) == ACCOUNTS
    got = store.enabled_tokens()
    assert len(got) == ACCOUNTS
    assert len(set(got)) == ACCOUNTS, "rotation handed out a duplicate"


def test_twenty_keys_survive_being_disabled(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(tokens())

    store.set_enabled(0, False)
    store.set_enabled(10, False)

    enabled = store.enabled_tokens()
    assert len(enabled) == ACCOUNTS - 2
    assert "hf_account01" not in enabled
    assert "hf_account11" not in enabled


def test_whitespace_and_duplicates_are_cleaned_at_scale(tmp_path):
    """A real paste from a text file has blank lines and repeats."""
    store = KeyStore(tmp_path / "keys.json")
    lines = []
    for i in range(1, ACCOUNTS + 1):
        lines.append(f"  hf_account{i:02d}  ")
        lines.append("")
        if i % 3 == 0:
            lines.append(f"hf_account{i:02d}")  # duplicate
    store.set_tokens(lines)

    assert len(store.all()) == ACCOUNTS, "blanks or duplicates survived"


def test_deleting_from_twenty_renumbers_cleanly(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(tokens())

    store.delete(0)
    remaining = store.listing()
    assert len(remaining) == ACCOUNTS - 1
    assert [k["index"] for k in remaining] == list(range(ACCOUNTS - 1))
    assert all(k["label"] == f"Kunci {k['index'] + 1}" for k in remaining)


def test_pool_survives_a_restart(tmp_path):
    """Keys must persist to disk, not live in memory.

    The app has no reload of data/keys.json on startup beyond reading it, so a
    persisted pool is what makes keys survive a server restart.
    """
    path = tmp_path / "keys.json"
    KeyStore(path).set_tokens(tokens())

    # A brand-new store object, as a fresh process would create.
    reread = KeyStore(path)
    assert len(reread.all()) == ACCOUNTS
    assert reread.all()[0]["token"] == "hf_account01"