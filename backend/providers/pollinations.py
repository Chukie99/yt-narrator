"""Pollinations image provider.

Free and keyless, which is the point: the FLUX route on HuggingFace is either
out of credits or capped at zero, and Gemini's image models report a free-tier
limit of 0. This needs no account at all.

The catch, measured rather than assumed: the free endpoint is intermittent.
Back-to-back requests alternate between succeeding and returning 402, and the
resolution is capped at 1024x576 (higher asks for payment). So this provider
retries with backoff, pins model=flux because the default routes elsewhere and
fails, and renders at the size the free tier actually serves. Ken Burns scales
the result up to 1920x1080 during compile.
"""

import asyncio
import hashlib
import logging
import random
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from backend.providers.base import ImageProvider
from backend.config import CACHE_DIR, POLLINATIONS_MODEL

logger = logging.getLogger(__name__)

BASE = "https://image.pollinations.ai/prompt/"

# The free tier serves at most this, and asking for more returns 402.
FREE_MAX_WIDTH = 1024
FREE_MAX_HEIGHT = 576

RETRYABLE = ("402", "429", "500", "502", "503", "504")


def _is_retryable(exc: Exception) -> bool:
    text = str(exc)
    return any(code in text for code in RETRYABLE)


class PollinationsImage(ImageProvider):
    """Image generation via Pollinations, no API key required."""

    def __init__(self, model: str = None, width: int = FREE_MAX_WIDTH,
                 height: int = FREE_MAX_HEIGHT, max_attempts: int = 6):
        self.model = model or POLLINATIONS_MODEL
        self.width = min(width, FREE_MAX_WIDTH)
        self.height = min(height, FREE_MAX_HEIGHT)
        self.max_attempts = max_attempts

    def _cache_key(self, prompt: str, aspect_ratio: str = "16:9") -> str:
        combined = f"pollinations|{self.model}|{self.width}x{self.height}|{prompt}"
        return hashlib.sha256(combined.encode()).hexdigest()

    def _url(self, prompt: str, seed: int) -> str:
        params = urllib.parse.urlencode({
            "width": self.width,
            "height": self.height,
            "model": self.model,
            "nologo": "true",
            "seed": seed,
        })
        return f"{BASE}{urllib.parse.quote(prompt)}?{params}"

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        attempt: int = 1,
        out_path: Optional[Path] = None,
    ) -> Path:
        """Generate an image, retrying the intermittent free tier."""
        if out_path is None:
            out_path = CACHE_DIR / f"{self._cache_key(prompt, aspect_ratio)}.jpg"

        if out_path.exists():
            return out_path

        if aspect_ratio == "9:16":
            self.width, self.height = FREE_MAX_WIDTH, 1024

        seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
        last_error: Optional[Exception] = None

        for i in range(self.max_attempts):
            try:
                # Run the blocking fetch off the event loop so other jobs and
                # the UI keep responding while an image is downloading.
                data = await asyncio.to_thread(self._fetch, self._url(prompt, seed + i))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(data)
                return out_path
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc):
                    raise RuntimeError(f"Pollinations failed: {exc}") from exc
                wait = min(2 ** i, 8) + random.random()
                logger.warning(
                    "pollinations attempt %d/%d failed (%s); retrying in %.1fs",
                    i + 1, self.max_attempts, str(exc)[:80], wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(
            f"Pollinations gagal setelah {self.max_attempts} percobaan "
            f"({self.width}x{self.height}, model={self.model}). "
            f"Kesalahan terakhir: {last_error}"
        )

    def _fetch(self, url: str) -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": "yt-narrator/1.0 (+local use)"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
        if len(data) < 2000:
            raise RuntimeError(f"response too small to be an image: {len(data)}B")
        return data