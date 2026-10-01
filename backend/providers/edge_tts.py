"""Edge-TTS provider (free, Indonesian, MP3→WAV conversion)."""

import subprocess
from pathlib import Path
from typing import Optional, Tuple
from backend.providers.base import TTSProvider
from backend.config import TTS_LANG


class EdgeTTSProvider(TTSProvider):
    """Edge-TTS provider (free, no API key required)."""

    def __init__(self, lang: str = None):
        self.lang = lang or TTS_LANG

    async def synthesize(
        self,
        text: str,
        lang: str = None,
        out_path: Optional[Path] = None,
    ) -> Tuple[Path, float]:
        """Synthesize text to WAV via edge-tts."""
        from edge_tts import Communicate
        import tempfile

        lang = lang or self.lang
        voice = f"{lang}-Neural2-A"  # e.g., "id-ID-GadisNeural"

        if out_path is None:
            out_path = Path(tempfile.gettempdir()) / "tts_temp.wav"

        # Save as MP3 first
        mp3_path = out_path.with_suffix(".mp3")

        try:
            communicate = Communicate(text, voice)
            await communicate.save(str(mp3_path))

            # Convert MP3 → WAV 44100 mono
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(mp3_path),
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    str(out_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )

            # Measure duration via ffprobe
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            duration_sec = float(result.stdout.strip())

            mp3_path.unlink()  # cleanup
            return out_path, duration_sec

        except Exception as e:
            raise RuntimeError(f"TTS synthesis failed: {e}")
