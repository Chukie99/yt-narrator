"""Tests for provider interfaces and implementations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from backend.providers.base import LLMProvider, ImageProvider, TTSProvider, VideoProvider


class TestLLMProviderInterface:
    """Test LLMProvider interface."""

    def test_llm_provider_is_abstract(self):
        """Test that LLMProvider cannot be instantiated."""
        with pytest.raises(TypeError):
            LLMProvider()

    @pytest.mark.asyncio
    async def test_llm_generate_narasi_signature(self):
        """Test that generate_narasi has correct signature."""
        class MockLLM(LLMProvider):
            async def generate_narasi(self, topic):
                return {
                    "full_narasi": "test narasi",
                    "scenes": [
                        {
                            "scene_num": 1,
                            "narration_text": "test",
                            "t2i_prompt": "test image",
                            "duration_est": 10,
                        }
                    ],
                }

            async def breakdown_scenes(self, narasi, style_bible):
                pass

            async def generate_metadata(self, narasi):
                pass

            async def generate_style_bible(self, narasi):
                pass

        llm = MockLLM()
        result = await llm.generate_narasi("test topic")
        assert "full_narasi" in result
        assert "scenes" in result
        assert len(result["scenes"]) > 0


class TestImageProviderInterface:
    """Test ImageProvider interface."""

    def test_image_provider_is_abstract(self):
        """Test that ImageProvider cannot be instantiated."""
        with pytest.raises(TypeError):
            ImageProvider()

    @pytest.mark.asyncio
    async def test_image_generate_signature(self):
        """Test that generate has correct signature."""
        class MockImage(ImageProvider):
            async def generate(
                self, prompt, aspect_ratio="16:9", attempt=1, out_path=None
            ):
                if out_path is None:
                    out_path = Path("/tmp/test.png")
                out_path.touch()
                return out_path

        img_provider = MockImage()
        result = await img_provider.generate("test prompt")
        assert isinstance(result, Path)


class TestTTSProviderInterface:
    """Test TTSProvider interface."""

    def test_tts_provider_is_abstract(self):
        """Test that TTSProvider cannot be instantiated."""
        with pytest.raises(TypeError):
            TTSProvider()

    @pytest.mark.asyncio
    async def test_tts_synthesize_signature(self):
        """Test that synthesize has correct signature."""
        class MockTTS(TTSProvider):
            async def synthesize(self, text, lang="id-ID", out_path=None):
                if out_path is None:
                    out_path = Path("/tmp/test.wav")
                out_path.touch()
                return (out_path, 10.5)

        tts_provider = MockTTS()
        path, duration = await tts_provider.synthesize("test text")
        assert isinstance(path, Path)
        assert isinstance(duration, float)
        assert duration == 10.5


class TestVideoProviderInterface:
    """Test VideoProvider interface."""

    def test_video_provider_is_abstract(self):
        """Test that VideoProvider cannot be instantiated."""
        with pytest.raises(TypeError):
            VideoProvider()

    @pytest.mark.asyncio
    async def test_video_animate_signature(self):
        """Test that animate has correct signature."""
        class MockVideo(VideoProvider):
            async def animate(
                self,
                image_path,
                duration_sec,
                composition="wide",
                num_frames=None,
                out_path=None,
            ):
                if out_path is None:
                    out_path = Path("/tmp/test.mp4")
                out_path.touch()
                return out_path

        video_provider = MockVideo()
        result = await video_provider.animate(
            Path("/tmp/img.png"), duration_sec=10.5, composition="wide"
        )
        assert isinstance(result, Path)
