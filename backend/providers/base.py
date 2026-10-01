"""Provider base interfaces (abstract)."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


class LLMProvider(ABC):
    """Language model provider interface."""

    @abstractmethod
    async def generate_narasi(self, topic: str) -> Dict[str, Any]:
        """Generate full narration from topic.
        
        Returns: {
            "full_narasi": str,
            "scenes": [{"scene_num": int, "narration_text": str, 
                       "t2i_prompt": str, "duration_est": float}, ...]
        }
        """
        pass

    @abstractmethod
    async def breakdown_scenes(self, narasi: str, style_bible: Dict[str, Any]) -> Dict[str, Any]:
        """Break narration into scenes with style applied.
        
        Returns: {"scenes": [...]} same structure as generate_narasi
        """
        pass

    @abstractmethod
    async def generate_metadata(self, narasi: str) -> Dict[str, Any]:
        """Generate metadata (title, description, hashtags).
        
        Returns: {"title": str, "description": str, "hashtags": [str, ...]}
        """
        pass

    @abstractmethod
    async def generate_style_bible(self, narasi: str) -> Dict[str, Any]:
        """Extract visual style guide from narration.
        
        Returns: {"visual_themes": [...], "color_palette": [...], "mood": str, ...}
        """
        pass


class ImageProvider(ABC):
    """Text-to-image provider interface."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        attempt: int = 1,
        out_path: Optional[Path] = None,
    ) -> Path:
        """Generate image from prompt.
        
        Args:
            prompt: Text description (English)
            aspect_ratio: "16:9" or "9:16"
            attempt: Retry attempt number (for seed variation)
            out_path: Save path (default: cache/images/{hash}.png)
            
        Returns: Path to generated image file
        """
        pass


class TTSProvider(ABC):
    """Text-to-speech provider interface."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        lang: str = "id-ID",
        out_path: Optional[Path] = None,
    ) -> Tuple[Path, float]:
        """Synthesize text to audio.
        
        Args:
            text: Text to synthesize
            lang: Language code (e.g., "id-ID")
            out_path: Save path (default: temp WAV)
            
        Returns: (path_to_wav, duration_seconds)
                 WAV must be 44100 Hz, mono, PCM-16
        """
        pass


class VideoProvider(ABC):
    """Video animation provider interface."""

    @abstractmethod
    async def animate(
        self,
        image_path: Path,
        duration_sec: float,
        composition: str = "wide",
        num_frames: Optional[int] = None,
        out_path: Optional[Path] = None,
    ) -> Path:
        """Generate animated video from image (Ken Burns motion).
        
        Args:
            image_path: Path to image file
            duration_sec: Video duration in seconds
            composition: "wide", "close-up", or "from-top"
            num_frames: Frame count (if None, calculate from duration)
            out_path: Save path (default: temp MP4)
            
        Returns: Path to output video file (MP4, H.264, 1920x1080, 24fps)
        """
        pass
