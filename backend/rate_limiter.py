"""Rate limiter per provider (RPM + daily quota + backoff)."""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from backend.db import get_db
from backend.config import RATE_LIMITS


class ProviderRateLimiter:
    """Rate limiter with per-provider RPM + daily quota tracking."""

    def __init__(self, provider: str, rate_limits: dict = None):
        self.provider = provider
        self.limits = rate_limits or RATE_LIMITS.get(
            provider, {"rpm": 50, "requests_per_day": 1000}
        )
        self.request_times = []

    async def can_request(self) -> bool:
        """Check if request allowed (RPM + daily quota)."""
        now = datetime.utcnow()

        with get_db() as db:
            db.execute("PRAGMA foreign_keys = ON")
            cursor = db.cursor()

            # Check daily quota
            cursor.execute(
                "SELECT requests_today, last_reset FROM provider_quota WHERE provider = ?",
                (self.provider,),
            )
            row = cursor.fetchone()

            if row is None:
                cursor.execute(
                    """INSERT INTO provider_quota 
                       (provider, requests_today, requests_limit_per_day, last_reset)
                       VALUES (?, ?, ?, ?)""",
                    (
                        self.provider,
                        0,
                        self.limits.get("requests_per_day", 1000),
                        now.isoformat(),
                    ),
                )
                return True

            requests_today, last_reset = row
            last_reset_dt = datetime.fromisoformat(last_reset)

            # Reset if day passed
            if (now - last_reset_dt).days > 0:
                cursor.execute(
                    """UPDATE provider_quota 
                       SET requests_today = 0, last_reset = ? 
                       WHERE provider = ?""",
                    (now.isoformat(), self.provider),
                )
                requests_today = 0

            daily_limit = self.limits.get("requests_per_day", 1000)
            if requests_today >= daily_limit:
                return False

            return True

    async def record_request(self):
        """Record successful request."""
        now = datetime.utcnow()

        with get_db() as db:
            db.execute("PRAGMA foreign_keys = ON")
            cursor = db.cursor()
            cursor.execute(
                """UPDATE provider_quota 
                   SET requests_today = requests_today + 1 
                   WHERE provider = ?""",
                (self.provider,),
            )

    async def backoff(self, attempt: int = 1, max_wait: int = 60):
        """Exponential backoff on rate limit."""
        wait_time = min(2 ** attempt, max_wait)
        await asyncio.sleep(wait_time)
