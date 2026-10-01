#!/usr/bin/env python3
"""
YT Narrator Spike: End-to-end 3-scene test
- HF Inference + FLUX.1 Schnell (real T2I call)
- edge-tts → WAV durasi terukur
- Ken Burns frame-accurate (kumulatif)
- Compile
- Report audio-video drift + cost
"""

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Requirements: pip install huggingface-hub edge-tts

WORK_DIR = Path.home() / "yt-narrator" / "spike_output"
WORK_DIR.mkdir(parents=True, exist_ok=True)

HF_API_KEY = None  # Will be set from env
FPS = 24

# Config
SCENE_CONFIG = [
    {
        "scene_num": 1,
        "narration": "Langit berwarna biru karena cahaya matahari tersebar di atmosfer bumi kita.",
        "prompt": "Clear blue sky with white clouds, watercolor painting style, minimalist illustration, ink sketch aesthetic, sepia tones",
        "duration_est": 8,
    },
    {
        "scene_num": 2,
        "narration": "Partikel oksigen dan nitrogen di udara menyerap cahaya biru lebih banyak dari warna lain.",
        "prompt": "Atmospheric particles scattering light rays, scientific diagram, blue light rays, ink sketch style, hand-drawn, brown and cream colors",
        "duration_est": 8,
    },
    {
        "scene_num": 3,
        "narration": "Itulah mengapa langit berwarna biru di siang hari dan oranye saat matahari terbenam.",
        "prompt": "Sunset with gradient orange and red sky, atmospheric effect, peaceful landscape, ink illustration style, parchment background",
        "duration_est": 7,
    },
]

def log(msg, level="INFO"):
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] [{level}] {msg}")

# ============================================================================
# HF INFERENCE + FLUX.1
# ============================================================================

async def generate_image_hf(prompt: str, scene_num: int) -> Optional[Path]:
    """Generate image via HF Inference API + FLUX.1 Schnell."""
    from huggingface_hub import InferenceClient
    from PIL import Image
    from io import BytesIO
    
    log(f"Generating image for scene {scene_num} via HF+FLUX.1...")
    
    if not HF_API_KEY:
        log(f"  ✗ HF_API_KEY not set", "ERROR")
        return None
    
    try:
        client = InferenceClient(api_key=HF_API_KEY)
        
        image = client.text_to_image(
            prompt=prompt,
            model="black-forest-labs/FLUX.1-schnell",
            height=1080,
            width=1920
        )
        
        # Convert PIL Image to PNG bytes
        image_path = WORK_DIR / f"scene_{scene_num}.png"
        image.save(image_path, format="PNG")
        
        image_size = image_path.stat().st_size
        log(f"  ✓ Saved to {image_path} ({image_size / 1024:.1f} KB)")
        return image_path
        
    except Exception as e:
        log(f"  ✗ Error: {e}", "ERROR")
        return None

# ============================================================================
# EDGE-TTS
# ============================================================================

async def synthesize_tts(text: str, scene_num: int) -> tuple[Optional[Path], float]:
    """Synthesize text to WAV, return (path, duration_sec)."""
    from edge_tts import Communicate
    
    log(f"TTS scene {scene_num}...")
    
    voice = "id-ID-GadisNeural"
    
    # Save as MP3 first
    mp3_path = WORK_DIR / f"scene_{scene_num}.mp3"
    
    try:
        communicate = Communicate(text, voice)
        await communicate.save(str(mp3_path))
        log(f"  ✓ MP3 saved")
        
        # Convert MP3 → WAV 44100 mono [FIX: separate paths]
        wav_path = WORK_DIR / f"scene_{scene_num}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path),
             "-ar", "44100", "-ac", "1", str(wav_path)],
            check=True, capture_output=True, timeout=60
        )
        log(f"  ✓ WAV saved")
        
        # Measure duration via ffprobe
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(wav_path)],
            capture_output=True, text=True, check=True
        )
        duration_sec = float(result.stdout.strip())
        log(f"  ✓ Duration: {duration_sec:.2f}s")
        
        mp3_path.unlink()  # cleanup
        return wav_path, duration_sec
        
    except Exception as e:
        log(f"  ✗ Error: {e}", "ERROR")
        return None, 0.0

# ============================================================================
# KEN BURNS
# ============================================================================

def generate_kenburns(
    image_path: Path,
    scene_num: int,
    num_frames: int,
    composition: str = "wide"
) -> Optional[Path]:
    """Generate Ken Burns video clip."""
    log(f"Ken Burns scene {scene_num}: {composition}, {num_frames} frames...")
    
    try:
        # Upscale image to 1920x1080 [FIX: upscale first]
        upscaled_path = image_path.with_stem(f"{image_path.stem}_upscaled")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(image_path),
             "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease",
             str(upscaled_path)],
            check=True, capture_output=True, timeout=60
        )
        log(f"  ✓ Upscaled")
        
        # Ken Burns patterns [FIX: correct zoompan syntax]
        patterns = {
            "wide": "zoompan=z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=24",
            "close-up": "zoompan=z='min(1+0.002*on,1.5)':x='iw/4':y='ih/4':d=1:s=1920x1080:fps=24",
            "from-top": "zoompan=z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='0':d=1:s=1920x1080:fps=24",
        }
        zoompan = patterns.get(composition, patterns["wide"])
        
        # Generate video
        fps = 24
        out_path = WORK_DIR / f"scene_{scene_num}_{composition}_kenburns.mp4"
        duration_sec = num_frames / fps
        
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(upscaled_path),
            "-vf", zoompan,
            "-t", str(duration_sec),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-an",
            str(out_path)
        ]
        
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        log(f"  ✓ Generated {out_path}")
        
        upscaled_path.unlink()  # cleanup
        return out_path
    
    except Exception as e:
        log(f"  ✗ Error: {e}", "ERROR")
        return None

# ============================================================================
# COMPILE
# ============================================================================

def compile_video(clips: list[Path], audio_files: list[Path], output_path: Path) -> bool:
    """Concat video clips + mix audio."""
    log("Compiling video...")
    
    try:
        # Build concat demux for video
        concat_video_txt = WORK_DIR / "concat_video.txt"
        with open(concat_video_txt, "w") as f:
            for clip in clips:
                f.write(f"file '{clip.absolute()}'\n")
        
        # Concat video (lossless, -c copy) [FIX: valid since already normalized]
        video_concat = WORK_DIR / "video_concat.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_video_txt),
             "-c:v", "copy",
             str(video_concat)],
            check=True, capture_output=True, timeout=600
        )
        log(f"  ✓ Video concat")
        
        # Build concat demux for audio
        concat_audio_txt = WORK_DIR / "concat_audio.txt"
        with open(concat_audio_txt, "w") as f:
            for audio in audio_files:
                f.write(f"file '{audio.absolute()}'\n")
        
        # Concat audio [FIX: pcm_s16le not -c copy]
        audio_mixed = WORK_DIR / "narasi_mixed.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_audio_txt),
             "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "1",
             str(audio_mixed)],
            check=True, capture_output=True, timeout=600
        )
        log(f"  ✓ Audio concat")
        
        # Mux video + audio
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_concat), "-i", str(audio_mixed),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
             "-map", "0:v:0", "-map", "1:a:0",
             str(output_path)],
            check=True, capture_output=True, timeout=600
        )
        log(f"  ✓ Muxed final.mp4")
        
        return True
    
    except Exception as e:
        log(f"  ✗ Error: {e}", "ERROR")
        return False

# ============================================================================
# MEASURE DRIFT
# ============================================================================

def measure_drift(video_path: Path, audio_path: Path) -> float:
    """Measure audio-video sync drift (ms)."""
    try:
        result_v = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(video_path)],
            capture_output=True, text=True, check=True
        )
        duration_v = float(result_v.stdout.strip())
        
        result_a = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(audio_path)],
            capture_output=True, text=True, check=True
        )
        duration_a = float(result_a.stdout.strip())
        
        drift_ms = abs(duration_v - duration_a) * 1000
        return drift_ms
    
    except Exception as e:
        log(f"  ✗ Error measuring drift: {e}", "ERROR")
        return -1.0

# ============================================================================
# MAIN
# ============================================================================

async def main():
    global HF_API_KEY
    
    log("=== YT NARRATOR SPIKE ===")
    log(f"Working directory: {WORK_DIR}")
    
    # Get API key
    import os
    HF_API_KEY = os.getenv("HF_API_KEY")
    if not HF_API_KEY:
        log("ERROR: Set HF_API_KEY environment variable", "ERROR")
        sys.exit(1)
    
    try:
        # STAGE 1: Generate images
        log("\n--- STAGE 1: Image Generation ---")
        images = []
        for scene in SCENE_CONFIG:
            img = await generate_image_hf(scene["prompt"], scene["scene_num"])
            images.append(img)
        
        if not any(images):
            log("ERROR: No images generated", "ERROR")
            sys.exit(1)
        
        # STAGE 2: TTS + measure duration
        log("\n--- STAGE 2: Text-to-Speech ---")
        audio_files = []
        scene_durations = []
        for scene in SCENE_CONFIG:
            wav, duration = await synthesize_tts(scene["narration"], scene["scene_num"])
            audio_files.append(wav)
            scene_durations.append(duration)
            log(f"  Scene {scene['scene_num']}: {duration:.2f}s")
        
        if not any(audio_files):
            log("ERROR: No audio generated", "ERROR")
            sys.exit(1)
        
        # STAGE 3: Ken Burns (frame-accurate kumulatif)
        log("\n--- STAGE 3: Ken Burns Motion ---")
        clips = []
        cum_duration = 0.0
        for i, duration in enumerate(scene_durations):
            scene_num = i + 1
            
            if images[i] is None:
                log(f"Scene {scene_num}: Skipping (no image)", "WARN")
                cum_duration += duration
                continue
            
            # Frame calculation [FIX: kumulatif]
            frame_start = round(cum_duration * FPS)
            frame_end = round((cum_duration + duration) * FPS)
            num_frames = frame_end - frame_start
            
            log(f"Scene {scene_num}: frames {frame_start}-{frame_end} ({num_frames} frames, {duration:.2f}s)")
            
            # Choose composition (rotate)
            composition = ["wide", "close-up", "from-top"][i % 3]
            
            clip = generate_kenburns(images[i], scene_num, num_frames, composition)
            if clip:
                clips.append(clip)
            
            cum_duration += duration
        
        # STAGE 4: Compile
        log("\n--- STAGE 4: Compile ---")
        if clips and audio_files and any(audio_files):
            output_video = WORK_DIR / "final.mp4"
            audio_valid = [a for a in audio_files if a]
            
            if compile_video(clips, audio_valid, output_video):
                # STAGE 5: Measure drift
                audio_full = WORK_DIR / "narasi_mixed.wav"
                if output_video.exists() and audio_full.exists():
                    drift_ms = measure_drift(output_video, audio_full)
                    
                    log("\n=== RESULTS ===")
                    log(f"Video: {output_video}")
                    log(f"Size: {output_video.stat().st_size / (1024*1024):.1f} MB")
                    log(f"Audio-video drift: {drift_ms:.1f} ms")
                    log(f"Status: {'✓ PASS' if drift_ms < 100 else '✗ DRIFT > 100ms'}")
                    log(f"Total duration: {cum_duration:.1f}s")
                    log(f"FPS: {FPS}")
                    log(f"\nCost estimate: $0.00 (gratis via HF multi-akun)")
                else:
                    log("ERROR: Compile failed", "ERROR")
        else:
            log("ERROR: No clips or audio to compile", "ERROR")
    
    except KeyboardInterrupt:
        log("Interrupted", "WARN")
    except Exception as e:
        log(f"FATAL: {e}", "ERROR")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
