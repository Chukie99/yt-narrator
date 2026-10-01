"""Gemini LLM provider implementation.

Two things matter for reliability on the free tier:

- The Flash models answer 503 (high demand) intermittently, and which model is
  up changes minute to minute, so every call retries with backoff and then
  walks a fallback list instead of failing the job on the first hiccup.
- Calls go through client.aio. The sync client blocks the event loop, which
  stalls every other job the scheduler is running.
"""

import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

from backend.providers.base import LLMProvider
from backend.config import LLM_MODEL, GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Tried in order. Availability is not stable minute to minute: the same model
# answers 'OK' and then 503s on the next call, and longer prompts (the full
# narration script) fail on models that handle short ones. So this list mixes
# several live models and each gets retried before moving on.
#
# Verified live against the API: 3.5-flash-lite and 3.1-flash-lite-preview
# answer the long narration prompt reliably; 3.7-flash is erratic; the
# preview models disappear without warning.
FALLBACK_MODELS = [
    LLM_MODEL,
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.7-flash",
    "gemini-flash-latest",
]

RETRYABLE = (503, 429, 500, 502, 504, "UNAVAILABLE", "RESOURCE_EXHAUSTED")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _EmptyResponse):
        return True
    text = str(exc).upper()
    return any(str(code) in text for code in RETRYABLE) or "UNAVAILABLE" in text


class _EmptyResponse(Exception):
    """Raised when the model returns no text at all.

    Usually a truncation or a safety block, both of which can differ on the
    next attempt, so it is worth retrying rather than moving straight to
    another model.
    """


def _strip_fence(text: str) -> str:
    """Return just the JSON body of a possibly fenced model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _first_json_object(text: str) -> Dict[str, Any]:
    """Parse JSON, tolerating prose or a stray trailing fence around it."""
    cleaned = _strip_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"model returned no JSON object: {text[:200]}")
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(cleaned[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])
    raise ValueError(f"model returned truncated JSON: {text[:200]}")


class GeminiLLM(LLMProvider):
    """Gemini LLM provider using google-genai SDK."""

    def __init__(self, api_key: str = None, model: str = None, max_attempts: int = 4):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or LLM_MODEL
        # Four attempts across five models, since a 503 streak on the
        # preferred model is common on the free tier.
        self.max_attempts = max_attempts
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")

    async def _generate(
        self,
        prompt: str,
        max_output_tokens: int = 65536,
        temperature: float = 1.0,
    ) -> str:
        """Call Gemini, retrying transient errors and falling back models."""
        from google import genai

        client = genai.Client(api_key=self.api_key)
        # Dedupe while preserving order: the configured model is usually also
        # in the fallback list, and retrying it twice wastes the budget.
        models: List[str] = list(
            dict.fromkeys([self.model] + [m for m in FALLBACK_MODELS if m != self.model])
        )

        last_error: Optional[Exception] = None
        for model in models:
            for attempt in range(self.max_attempts):
                try:
                    response = await client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config={
                            "max_output_tokens": max_output_tokens,
                            "temperature": temperature,
                        },
                    )
                    text = (response.text or "").strip()
                    if not text:
                        raise _EmptyResponse(f"{model} returned an empty response")
                    return text
                except Exception as exc:
                    if not _is_retryable(exc):
                        # A 404 or a bad key will not fix itself on retry, but
                        # another model might still work.
                        last_error = exc
                        logger.warning("gemini %s hard failure: %s", model, exc)
                        break
                    last_error = exc
                    # Capped so the worst case stays under ~10s per model
                    # instead of sleeping 1+2+4+8s across four attempts.
                    wait = min(2 ** attempt, 8) + random.random()
                    logger.warning(
                        "gemini %s attempt %d/%d failed (%s); retrying in %.1fs",
                        model, attempt + 1, self.max_attempts, exc, wait,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"all Gemini models failed, last error: {last_error}"
        ) from last_error

    async def generate_narasi(self, topic: str) -> Dict[str, Any]:
        """Generate full narration from topic."""
        prompt = f"""Generate a comprehensive narration script for an educational video about: {topic}

Requirements:
1. Write full narration (500-2000 words) suitable for a 5-15 minute video
2. Break narration into scenes (6-12 scenes typical)
3. Each scene should be 30-120 seconds when spoken naturally
4. For each scene, provide:
   - narration_text: the exact words to speak (30-150 words per scene)
   - t2i_prompt: detailed visual description in ENGLISH for AI image generation (must match the narration content)

Output format (valid JSON only, no markdown):
{{
  "full_narasi": "...",
  "scenes": [
    {{
      "scene_num": 1,
      "narration_text": "...",
      "t2i_prompt": "ink sketch style, ...",
      "duration_est": 45
    }},
    ...
  ]
}}

Important: Output ONLY valid JSON, no code blocks or explanations."""

        output = _first_json_object(await self._generate(prompt))
        scenes = output.get("scenes") or []
        if not scenes:
            raise ValueError("model returned no scenes")
        return output

    async def breakdown_scenes(
        self, narasi: str, style_bible: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Break narration into scenes with style applied."""
        # MVP keeps the scenes generated alongside the narration; re-segmenting
        # is only needed for the regenerate-a-scene flow.
        return {"scenes": []}

    async def generate_metadata(self, narasi: str) -> Dict[str, Any]:
        """Generate metadata (title, description, hashtags)."""
        prompt = f"""Based on this narration script, generate YouTube metadata.

Script (first 1500 characters):
{narasi[:1500]}

The narration is in Indonesian. Write the title and description in Indonesian too.

Provide JSON:
{{
  "title": "...",
  "description": "...",
  "hashtags": ["tag1", "tag2", ...]
}}

Output ONLY valid JSON."""

        return _first_json_object(await self._generate(prompt, max_output_tokens=8192))

    async def generate_style_bible(self, narasi: str) -> Dict[str, Any]:
        """Extract a visual style guide from the narration."""
        prompt = f"""You are an art director for an ink-sketch explainer channel.

Narration (first 2000 characters):
{narasi[:2000]}

Return a JSON style bible that every image prompt in this video will follow.
Be specific enough to keep 8 images visually consistent with each other.

Provide JSON:
{{
  "visual_themes": ["...", "..."],
  "color_palette": ["...", "..."],
  "mood": "...",
  "character_style": "...",
  "backgrounds": "...",
  "artistic_technique": "...",
  "style_prefix": "a short English prefix prepended to every image prompt"
}}

Output ONLY valid JSON."""

        return _first_json_object(await self._generate(prompt, max_output_tokens=8192))