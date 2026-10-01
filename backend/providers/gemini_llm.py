"""Gemini LLM provider implementation."""

import json
import asyncio
from typing import Any, Dict, Optional
from backend.providers.base import LLMProvider
from backend.config import LLM_MODEL, GEMINI_API_KEY


class GeminiLLM(LLMProvider):
    """Gemini LLM provider using google-genai SDK."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or LLM_MODEL
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")

    async def generate_narasi(self, topic: str) -> Dict[str, Any]:
        """Generate full narration from topic."""
        from google import genai

        client = genai.Client(api_key=self.api_key)

        prompt = f"""Generate a comprehensive narration script for an educational video about: {topic}

Requirements:
1. Write full narration (500-2000 words) suitable for a 5-15 minute video
2. Break narration into scenes (6-12 scenes typical)
3. Each scene should be 30-120 seconds when spoken naturally
4. For each scene, provide:
   - narration_text: the exact words to speak (30-150 words per scene)
   - t2i_prompt: detailed visual description in ENGLISH for AI image generation (must match the narration content)
   - duration_est: estimated duration in seconds

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

        response = client.models.generate_content(model=self.model, contents=prompt)
        text = response.text.strip()

        # Strip markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

        output = json.loads(text)
        return output

    async def breakdown_scenes(
        self, narasi: str, style_bible: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Break narration into scenes with style applied."""
        # For MVP, reuse generate_narasi output format
        # In full impl, would re-segment existing narasi with style_bible prefix
        return {"scenes": []}

    async def generate_metadata(self, narasi: str) -> Dict[str, Any]:
        """Generate metadata (title, description, hashtags)."""
        from google import genai

        client = genai.Client(api_key=self.api_key)

        prompt = f"""Based on this narration script, generate YouTube metadata:

{narasi[:1000]}...

Provide JSON:
{{
  "title": "...",
  "description": "...",
  "hashtags": ["tag1", "tag2", ...]
}}

Output ONLY valid JSON."""

        response = client.models.generate_content(model=self.model, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

        return json.loads(text)

    async def generate_style_bible(self, narasi: str) -> Dict[str, Any]:
        """Extract visual style guide from narration."""
        return {
            "visual_themes": ["ink sketch", "educational", "historical"],
            "color_palette": ["brown", "cream", "sepia"],
            "mood": "contemplative, informative",
            "character_style": "semi-caricature, expressive",
            "backgrounds": "parchment, minimalist",
            "artistic_technique": "hatching, cross-hatching, organic lines",
        }
