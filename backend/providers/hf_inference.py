"""HF Inference Image provider (FLUX.1 Schnell, multi-key rotation)."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional
from backend.providers.base import ImageProvider
from backend.config import IMAGE_MODEL, CACHE_DIR, DATA_DIR
from backend.keystore import KeyStore

logger = logging.getLogger(__name__)

# The router defaults to nscale for FLUX.1-schnell, which answers 402 for
# accounts without pre-paid credits. fal-ai serves the same model on the free
# included quota, so the provider is pinned rather than left to chance.
DEFAULT_INFERENCE_PROVIDER = os.getenv("HF_INFERENCE_PROVIDER", "fal-ai")

# One shared store: the provider and the API routes must see the same pool.
key_store = KeyStore(DATA_DIR / "keys.json")


def _is_key_problem(exc: Exception) -> bool:
    """True when this specific key is the reason for the failure.

    402 is out of credits, 429 is a rate limit: another key in the pool can
    still work. Anything else (a malformed prompt, a model that does not
    exist) will fail identically on every key, so it must not burn the pool.
    """
    text = str(exc).upper()
    return any(marker in text for marker in ("402", "429", "PAYMENT REQUIRED", "RATE LIMIT"))


class HFInferenceImage(ImageProvider):
    """Hugging Face Inference API provider with FLUX.1 Schnell."""

    def __init__(self, model: str = None, store: KeyStore = None, provider: str = None):
        self.model = model or IMAGE_MODEL
        self.store = store or key_store
        self.provider = DEFAULT_INFERENCE_PROVIDER if provider is None else provider

    def _cache_key(self, prompt: str, aspect_ratio: str = "16:9") -> str:
        """Generate cache key from prompt hash."""
        combined = f"{self.model}|{aspect_ratio}|{prompt}"
        return hashlib.sha256(combined.encode()).hexdigest()

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        attempt: int = 1,
        out_path: Optional[Path] = None,
    ) -> Path:
        """Generate an image, trying each enabled key until one works."""
        from huggingface_hub import InferenceClient

        if out_path is None:
            cache_key = self._cache_key(prompt, aspect_ratio)
            out_path = CACHE_DIR / f"{cache_key}.png"

        if out_path.exists():
            return out_path

        if aspect_ratio == "9:16":
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        # Read fresh on every call so keys added mid-run are used without a
        # restart, and so disabling a key takes effect immediately.
        tokens = self.store.enabled_tokens()

        last_error: Optional[Exception] = None
        for token in tokens:
            client = InferenceClient(api_key=token)
            try:
                image = client.text_to_image(
                    prompt=prompt,
                    model=self.model,
                    height=height,
                    width=width,
                    extra_body={"provider": self.provider} if self.provider else None,
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # text_to_image returns a PIL Image, not raw bytes.
                image.save(out_path)
                return out_path
            except Exception as exc:
                last_error = exc
                if not _is_key_problem(exc):
                    raise RuntimeError(f"HF image generation failed: {exc}") from exc
                logger.warning(
                    "HF key rejected (%s), trying next key", str(exc)[:120]
                )

        # Every key was out of credits or rate-limited.
        raise RuntimeError(
            f"Semua {len(tokens)} kunci HF gagal (habis kredit atau kena limit). "
            f"Kesalahan terakhir: {last_error}"
        )