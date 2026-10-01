"""HF Inference Image provider (FLUX.1 Schnell, multi-key rotation)."""

import hashlib
from pathlib import Path
from typing import Optional
from backend.providers.base import ImageProvider
from backend.config import IMAGE_MODEL, CACHE_DIR, DATA_DIR
from backend.keystore import KeyStore

# One shared store: the provider and the API routes must see the same pool.
key_store = KeyStore(DATA_DIR / "keys.json")


class HFInferenceImage(ImageProvider):
    """Hugging Face Inference API provider with FLUX.1 Schnell."""

    def __init__(self, model: str = None, store: KeyStore = None):
        self.model = model or IMAGE_MODEL
        self.store = store or key_store

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
        """Generate image via HF Inference API."""
        from huggingface_hub import InferenceClient

        if out_path is None:
            cache_key = self._cache_key(prompt, aspect_ratio)
            out_path = CACHE_DIR / f"{cache_key}.png"

        if out_path.exists():
            return out_path

        # Read fresh on every call so keys added mid-run are used without a
        # restart, and so disabling a key takes effect immediately.
        api_key = self.store.next_token()
        client = InferenceClient(api_key=api_key)

        if aspect_ratio == "9:16":
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        try:
            image = client.text_to_image(
                prompt=prompt,
                model=self.model,
                height=height,
                width=width,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # text_to_image returns a PIL Image, not raw bytes.
            image.save(out_path)
            return out_path
        except Exception as e:
            raise RuntimeError(f"HF image generation failed: {e}")
