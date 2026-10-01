"""Ken Burns motion provider (FFmpeg zoompan, frame-accurate)."""

import subprocess
from pathlib import Path
from typing import Optional
from backend.config import FPS, TARGET_RESOLUTION


class KenBurnsMotion:
    """Ken Burns motion synthesis via FFmpeg zoompan."""

    def __init__(self, fps: int = None, resolution: tuple = None):
        self.fps = fps or FPS
        self.resolution = resolution or TARGET_RESOLUTION

    async def animate(
        self,
        image_path: Path,
        duration_sec: float,
        composition: str = "wide",
        num_frames: Optional[int] = None,
        out_path: Optional[Path] = None,
    ) -> Path:
        """Generate Ken Burns clip via FFmpeg zoompan."""
        import tempfile

        if out_path is None:
            out_path = Path(tempfile.gettempdir()) / "kenburns_temp.mp4"

        if num_frames is None:
            num_frames = round(duration_sec * self.fps)

        # Upscale image to target resolution
        upscaled_path = image_path.with_stem(f"{image_path.stem}_upscaled")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(image_path),
                "-vf",
                f"scale={self.resolution[0]}:{self.resolution[1]}:force_original_aspect_ratio=decrease",
                str(upscaled_path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

        # Ken Burns patterns. These must be zoompan OPTIONS (z=, x=, y=), not
        # a bare filter body: "-vf z='...':x='...'" makes FFmpeg parse "z" as
        # a filter name and fail with "No option name near ...".
        patterns = {
            "wide": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
            "close-up": "z='min(1+0.002*on,1.5)':x='iw/4':y='ih/4'",
            "from-top": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='0'",
        }
        zoompan = patterns.get(composition, patterns["wide"])

        # Generate Ken Burns video
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(upscaled_path),
                "-vf",
                f"zoompan={zoompan}:d=1:s={self.resolution[0]}x{self.resolution[1]}:fps={self.fps}",
                "-t",
                str(duration_sec),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(self.fps),
                "-an",
                str(out_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)

            upscaled_path.unlink()  # cleanup
            return out_path

        except Exception as e:
            raise RuntimeError(f"Ken Burns generation failed: {e}")
