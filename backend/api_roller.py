"""Multi-key API rotation (round-robin, exhaustion tracking)."""

from backend.db import get_db
from backend.config import HF_API_KEYS
from datetime import datetime, timedelta


class APIRoller:
    """Round-robin API key rotation with exhaustion tracking."""

    def __init__(self, service: str = "hf_inference", api_keys: dict = None):
        self.service = service
        self.api_keys = api_keys or HF_API_KEYS
        self.current_idx = 0
        if not self.api_keys:
            raise ValueError(f"No API keys for {service}")

    async def get_key(self) -> str:
        """Get next active key (round-robin)."""
        if not self.api_keys:
            raise RuntimeError("All API keys exhausted")

        keys_list = list(self.api_keys.values())
        attempts = 0

        while attempts < len(keys_list):
            idx = self.current_idx % len(keys_list)
            self.current_idx = (self.current_idx + 1) % len(keys_list)

            key_id = list(self.api_keys.keys())[idx]
            key = keys_list[idx]

            # Check if key is active in DB
            with get_db() as db:
                db.execute("PRAGMA foreign_keys = ON")
                cursor = db.cursor()
                cursor.execute(
                    "SELECT status FROM api_keys WHERE service = ? AND env_var_name = ?",
                    (self.service, f"HF_API_KEY_{key_id}"),
                )
                row = cursor.fetchone()

                if row is None or row[0] == "active":
                    return key

            attempts += 1

        raise RuntimeError(f"All {self.service} keys exhausted")

    async def mark_exhausted(self, key: str, retry_after_sec: int = 3600):
        """Mark key as exhausted (e.g., on 429 response)."""
        # Find key ID
        for key_id, stored_key in self.api_keys.items():
            if stored_key == key:
                resume_at = datetime.utcnow() + timedelta(seconds=retry_after_sec)

                with get_db() as db:
                    db.execute("PRAGMA foreign_keys = ON")
                    cursor = db.cursor()
                    cursor.execute(
                        """INSERT OR REPLACE INTO api_keys 
                           (service, env_var_name, status, last_reset)
                           VALUES (?, ?, ?, ?)""",
                        (
                            self.service,
                            f"HF_API_KEY_{key_id}",
                            "exhausted",
                            resume_at.isoformat(),
                        ),
                    )
                break
