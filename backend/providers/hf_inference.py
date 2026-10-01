"""HF Inference Image provider (FLUX.1 Schnell + multi-key APIRoller)."""

import hashlib
from pathlib import Path
from typing import Optional, Dict
from backend.providers.base import ImageProvider
from backend.config import IMAGE_MODEL, HF_API_KEYS, CACHE_DIR, FPS


class HFInferenceImage(ImageProvider):
    """Hugging Face Inference API provider with FLUX.1 Schnell."""

    def __init__(self, model: str = None, api_keys: Dict[int, str] = None):
        self.model = model or IMAGE_MODEL
        self.api_keys = api_keys or HF_API_KEYS
        self.current_key_idx = 0
        if not self.api_keys:
            raise ValueError("No HF_API_KEY_* env vars found")

    def _get_next_key(self) -> str:
        """Round-robin next API key."""
        keys_list = list(self.api_keys.values())
        key = keys_list[self.current_key_idx % len(keys_list)]
        self.current_key_idx += 1
        return key

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

        # Check cache
        if out_path.exists():
            return out_path

        # Generate via HF Inference
        api_key = self._get_next_key()
        client = InferenceClient(api_key=api_key)

        # Map aspect ratio to dimensions
        if aspect_ratio == "16:9":
            width, height = 1920, 1080
        elif aspect_ratio == "9:16":
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        try:
            image_bytes = client.text_to_image(
                prompt=prompt,
                model=self.model,
                height=height,
                width=width,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(image_bytes)
            return out_path
        except Exception as e:
            raise RuntimeError(f"HF image generation failed: {e}")
