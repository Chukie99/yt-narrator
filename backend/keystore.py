"""HuggingFace API key store.

Keys live in a JSON file next to the database instead of .env, so the running
app can pick up new keys without a restart. Reads from disk on every access
because the UI edits this file while the pipeline is running.
"""

import json
import threading
from pathlib import Path
from typing import List, Optional


def redact(token: str) -> str:
    """Show only the tail of a token, so the browser never receives it whole."""
    # Below 9 chars the head and tail slices would overlap and reveal the
    # whole token, so short tokens get hidden outright.
    if not token or len(token) <= 8:
        return "***"
    return f"{token[:3]}***{token[-4:]}"


class KeyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rotation = 0

    def _read(self) -> List[dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, list):
            return []
        return [
            {
                "label": entry.get("label") or f"Kunci {i + 1}",
                "token": entry.get("token") or "",
                "enabled": entry.get("enabled", True),
            }
            for i, entry in enumerate(keys)
            if isinstance(entry, dict) and entry.get("token")
        ]

    def _write(self, keys: List[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-replace so a crash mid-write cannot corrupt the pool.
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"keys": keys}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(self.path)

    def all(self) -> List[dict]:
        with self._lock:
            return self._read()

    def set_tokens(self, tokens: List[str]) -> int:
        """Replace the whole pool. Whitespace, blanks, and duplicates dropped."""
        seen = set()
        cleaned = []
        for raw in tokens:
            token = (raw or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            cleaned.append(token)

        existing_enabled = {k["token"]: k["enabled"] for k in self.all()}
        keys = [
            {
                "label": f"Kunci {i + 1}",
                "token": token,
                # A key that was switched off stays off across a re-save.
                "enabled": existing_enabled.get(token, True),
            }
            for i, token in enumerate(cleaned)
        ]
        with self._lock:
            self._write(keys)
        return len(keys)

    def set_enabled(self, index: int, enabled: bool) -> dict:
        with self._lock:
            keys = self._read()
            if not 0 <= index < len(keys):
                raise IndexError(index)
            keys[index]["enabled"] = enabled
            self._write(keys)
            return keys[index]

    def delete(self, index: int) -> List[dict]:
        with self._lock:
            keys = self._read()
            if not 0 <= index < len(keys):
                raise IndexError(index)
            del keys[index]
            for i, entry in enumerate(keys):
                entry["label"] = f"Kunci {i + 1}"
            self._write(keys)
            return keys

    def listing(self) -> List[dict]:
        """Token-safe view for the browser: label, redacted token, enabled."""
        return [
            {
                "index": i,
                "label": entry["label"],
                "token": redact(entry["token"]),
                "enabled": entry["enabled"],
            }
            for i, entry in enumerate(self.all())
        ]

    def enabled_tokens(self) -> List[str]:
        """Every enabled token, starting from a rotating offset.

        Used when a call fails: the caller wants each key tried once before
        giving up, not just the next one in the pool."""
        with self._lock:
            keys = [k for k in self._read() if k["enabled"]]
            if not keys:
                raise RuntimeError(
                    "Semua kunci HF nonaktif atau belum diisi. Tambahkan di panel Kunci API."
                )
            start = self._rotation % len(keys)
            self._rotation = (start + 1) % len(keys)
            ordered = keys[start:] + keys[:start]
            return [k["token"] for k in ordered]

    def next_token(self) -> str:
        """Round-robin over enabled keys, reading from disk each call."""
        return self.enabled_tokens()[0]
