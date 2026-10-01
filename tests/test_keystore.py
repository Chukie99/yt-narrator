"""Tests for the API key store (load, save, rotate, redact)."""

import json
import pytest
from pathlib import Path

from backend.keystore import KeyStore, redact


def test_missing_file_returns_empty(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    assert store.all() == []


def test_add_and_read_back(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_aaa", "hf_bbb"])
    assert [k["token"] for k in store.all()] == ["hf_aaa", "hf_bbb"]
    assert [k["label"] for k in store.all()] == ["Kunci 1", "Kunci 2"]


def test_tokens_persist_across_new_instance(tmp_path):
    path = tmp_path / "keys.json"
    KeyStore(path).set_tokens(["hf_aaa"])
    assert [k["token"] for k in KeyStore(path).all()] == ["hf_aaa"]


def test_whitespace_and_blank_lines_ignored(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["  hf_aaa  ", "", "   ", "hf_bbb\n"])
    assert [k["token"] for k in store.all()] == ["hf_aaa", "hf_bbb"]


def test_duplicates_dropped_keeping_order(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_aaa", "hf_bbb", "hf_aaa"])
    assert [k["token"] for k in store.all()] == ["hf_aaa", "hf_bbb"]


def test_replace_drops_previous_tokens(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_aaa", "hf_bbb"])
    store.set_tokens(["hf_ccc"])
    assert [k["token"] for k in store.all()] == ["hf_ccc"]


def test_rotate_cycles_through_every_key(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a", "hf_b", "hf_c"])
    seen = [store.next_token() for _ in range(4)]
    assert seen == ["hf_a", "hf_b", "hf_c", "hf_a"]


def test_rotate_skips_disabled(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a", "hf_b"])
    store.set_enabled(1, False)
    # Index 1 is hf_b, so only hf_a is left in rotation.
    assert store.next_token() == "hf_a"
    assert store.next_token() == "hf_a"


def test_rotate_raises_when_all_disabled(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a"])
    store.set_enabled(0, False)
    with pytest.raises(RuntimeError, match="nonaktif"):
        store.next_token()


def test_rotate_raises_when_empty(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    with pytest.raises(RuntimeError):
        store.next_token()


def test_rotation_picks_up_new_keys_without_restart(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a"])
    assert store.next_token() == "hf_a"
    # A key added while running is usable immediately, no restart needed.
    store.set_tokens(["hf_a", "hf_b"])
    assert sorted([store.next_token(), store.next_token()]) == ["hf_a", "hf_b"]


def test_delete_by_index(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a", "hf_b", "hf_c"])
    store.delete(1)
    assert [k["token"] for k in store.all()] == ["hf_a", "hf_c"]


def test_delete_out_of_range_raises(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_a"])
    with pytest.raises(IndexError):
        store.delete(9)


def test_redact_hides_all_but_last_four():
    assert redact("hf_abcdefghijklmnop") == "hf_***mnop"


def test_redact_of_short_token_hides_everything():
    assert redact("hf_ab") == "***"


def test_listing_never_exposes_full_token(tmp_path):
    store = KeyStore(tmp_path / "keys.json")
    store.set_tokens(["hf_abcdefghijklmnop"])
    listing = store.listing()
    assert listing[0]["token"] == "hf_***mnop"
    assert "abcdefghij" not in json.dumps(listing)


def test_corrupt_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert KeyStore(path).all() == []


def test_written_file_is_valid_json(tmp_path):
    path = tmp_path / "keys.json"
    store = KeyStore(path)
    store.set_tokens(["hf_a", "hf_b"])
    store.set_enabled(0, False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["keys"][0]["enabled"] is False
    assert data["keys"][0]["token"] == "hf_a"
