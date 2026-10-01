"""Job worker orchestrator (8-stage pipeline)."""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from backend.db import get_db
from backend.config import OUTPUTS_DIR, STYLE_PREFIX_DEFAULT
from backend.providers.gemini_llm import GeminiLLM
from backend.providers.hf_inference import HFInferenceImage
from backend.providers.edge_tts import EdgeTTSProvider
from backend.providers.ken_burns import KenBurnsMotion


async def process_job(job_id: str):
    """Process job through 8 stages."""
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()

        # Get job
        cursor.execute("SELECT topic, revision, cancel_requested FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return

        topic, revision, cancel_requested = row

        if cancel_requested:
            cursor.execute("UPDATE jobs SET status = ? WHERE id = ?", ("cancelled", job_id))
            return

        try:
            # Stage 1: LLM generate narasi + breakdown
            cursor.execute("UPDATE jobs SET status = ?, stage = ?, progress = ? WHERE id = ?",
                         ("generating", "llm", "LLM 1/1", job_id))

            llm = GeminiLLM()
            narasi_output = await llm.generate_narasi(topic)
            full_narasi = narasi_output.get("full_narasi", "")
            scenes = narasi_output.get("scenes", [])

            cursor.execute("UPDATE jobs SET narasi_full = ? WHERE id = ?", (full_narasi, job_id))

            # Stage 2: Style bible
            cursor.execute("UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                         ("style_bible", "Style 1/1", job_id))

            style_bible = await llm.generate_style_bible(full_narasi)
            import json
            cursor.execute("UPDATE jobs SET style_bible_json = ? WHERE id = ?",
                         (json.dumps(style_bible), job_id))

            # Insert job_scenes
            for scene_num, scene in enumerate(scenes, 1):
                cursor.execute(
                    """INSERT INTO job_scenes 
                       (job_id, revision, scene_num, narration_text, t2i_prompt, status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (job_id, revision, scene_num, scene.get("narration_text", ""),
                     scene.get("t2i_prompt", ""), "pending")
                )

            # Stage 3: TTS (per scene)
            cursor.execute("UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                         ("tts", f"TTS 0/{len(scenes)}", job_id))

            tts = EdgeTTSProvider()
            for scene_num in range(1, len(scenes) + 1):
                cursor.execute(
                    "SELECT narration_text FROM job_scenes WHERE job_id = ? AND revision = ? AND scene_num = ?",
                    (job_id, revision, scene_num)
                )
                scene_row = cursor.fetchone()
                if scene_row:
                    narration = scene_row[0]
                    audio_path, duration = await tts.synthesize(narration)
                    cursor.execute(
                        """UPDATE job_scenes 
                           SET audio_path = ?, audio_duration_sec = ?, status = ?
                           WHERE job_id = ? AND revision = ? AND scene_num = ?""",
                        (str(audio_path), duration, "done", job_id, revision, scene_num)
                    )

                cursor.execute("UPDATE jobs SET progress = ? WHERE id = ?",
                             (f"TTS {scene_num}/{len(scenes)}", job_id))

            # Stage 4: T2I (per scene)
            cursor.execute("UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                         ("images", f"Images 0/{len(scenes)}", job_id))

            img_provider = HFInferenceImage()
            for scene_num in range(1, len(scenes) + 1):
                cursor.execute(
                    "SELECT t2i_prompt FROM job_scenes WHERE job_id = ? AND revision = ? AND scene_num = ?",
                    (job_id, revision, scene_num)
                )
                scene_row = cursor.fetchone()
                if scene_row:
                    t2i_prompt = scene_row[0]
                    # Add style prefix
                    full_prompt = f"{STYLE_PREFIX_DEFAULT}. {t2i_prompt}"
                    image_path = await img_provider.generate(full_prompt)
                    cursor.execute(
                        """UPDATE job_scenes 
                           SET image_path = ?, status = ?
                           WHERE job_id = ? AND revision = ? AND scene_num = ?""",
                        (str(image_path), "done", job_id, revision, scene_num)
                    )

                cursor.execute("UPDATE jobs SET progress = ? WHERE id = ?",
                             (f"Images {scene_num}/{len(scenes)}", job_id))

            # Stage 5: Ken Burns (per scene, 3 compositions each)
            cursor.execute("Update jobs SET stage = ?, progress = ? WHERE id = ?",
                         ("motion", f"Motion 0/{len(scenes)}", job_id))

            ken_burns = KenBurnsMotion()
            cum_duration = 0.0
            compositions = ["wide", "close-up", "from-top"]

            for scene_num in range(1, len(scenes) + 1):
                cursor.execute(
                    """SELECT audio_duration_sec, image_path FROM job_scenes 
                       WHERE job_id = ? AND revision = ? AND scene_num = ?""",
                    (job_id, revision, scene_num)
                )
                scene_row = cursor.fetchone()
                if scene_row:
                    duration, image_path = scene_row
                    if not image_path:
                        continue

                    frame_start = round(cum_duration * 24)
                    frame_end = round((cum_duration + duration) * 24)
                    num_frames = frame_end - frame_start

                    for clip_variant, composition in enumerate(compositions, 1):
                        clip_path = await ken_burns.animate(
                            Path(image_path), duration, composition, num_frames
                        )
                        cursor.execute(
                            """INSERT INTO scene_clips 
                               (job_id, revision, scene_num, clip_variant, composition, video_path, num_frames)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (job_id, revision, scene_num, clip_variant, composition, str(clip_path), num_frames)
                        )

                    cum_duration += duration

                cursor.execute("UPDATE jobs SET progress = ? WHERE id = ?",
                             (f"Motion {scene_num}/{len(scenes)}", job_id))

            # Stage 6: Compile
            cursor.execute("UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                         ("compile", "Compile 1/1", job_id))

            # Fetch all clips in order
            cursor.execute(
                """SELECT video_path FROM scene_clips 
                   WHERE job_id = ? AND revision = ?
                   ORDER BY scene_num, clip_variant""",
                (job_id, revision)
            )
            clips = [Path(row[0]) for row in cursor.fetchall()]

            # Fetch all audio in order
            cursor.execute(
                """SELECT audio_path FROM job_scenes 
                   WHERE job_id = ? AND revision = ?
                   ORDER BY scene_num""",
                (job_id, revision)
            )
            audio_files = [Path(row[0]) for row in cursor.fetchall()]

            if clips and audio_files:
                output_dir = OUTPUTS_DIR / job_id / f"r{revision}"
                output_dir.mkdir(parents=True, exist_ok=True)
                final_video = output_dir / "final.mp4"

                await compile_video(clips, audio_files, final_video)

                # Stage 7: Metadata
                cursor.execute("UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                             ("metadata", "Metadata 1/1", job_id))

                metadata = await llm.generate_metadata(full_narasi)

                cursor.execute(
                    """INSERT OR REPLACE INTO job_outputs 
                       (job_id, revision, video_path, title, description, hashtags)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (job_id, revision, str(final_video),
                     metadata.get("title", ""),
                     metadata.get("description", ""),
                     ",".join(metadata.get("hashtags", [])))
                )

            # Mark done
            cursor.execute("UPDATE jobs SET status = ?, stage = ?, completed_at = ? WHERE id = ?",
                         ("done", None, datetime.utcnow().isoformat(), job_id))

        except Exception as e:
            cursor.execute("UPDATE jobs SET status = ?, error_msg = ? WHERE id = ?",
                         ("error", str(e)[:2000], job_id))
            raise


async def compile_video(clips, audio_files, output_path):
    """Compile video clips + audio (placeholder - full impl in Task 5)."""
    import subprocess
    import tempfile

    # Create concat demux for video
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for clip in clips:
            f.write(f"file '{clip.absolute()}'\n")
        concat_video_txt = f.name

    # Concat video
    video_concat = output_path.with_stem("video_concat")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_video_txt,
         "-c:v", "copy", str(video_concat)],
        check=True, capture_output=True, timeout=600
    )

    # Create concat demux for audio
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for audio in audio_files:
            f.write(f"file '{audio.absolute()}'\n")
        concat_audio_txt = f.name

    # Concat audio
    audio_mixed = output_path.with_stem("narasi_mixed")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_audio_txt,
         "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "1", str(audio_mixed)],
        check=True, capture_output=True, timeout=600
    )

    # Mux
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_concat), "-i", str(audio_mixed),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
         "-map", "0:v:0", "-map", "1:a:0", str(output_path)],
        check=True, capture_output=True, timeout=600
    )

    # Cleanup
    Path(concat_video_txt).unlink()
    Path(concat_audio_txt).unlink()
    video_concat.unlink()
    audio_mixed.unlink()
