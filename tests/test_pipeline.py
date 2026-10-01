"""Full pipeline run with a stubbed LLM.

Exercises stages 2-8 for real: TTS, FLUX.1 images, Ken Burns, FFmpeg compile.
Only the LLM is faked, so the test needs no Gemini key and costs one image.

The `pipeline` fixture and StubLLM live in conftest.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db import get_db
from backend import config as cfg


def _state(job_id):
    from conftest import job_state

    return job_state(job_id)


@pytest.mark.asyncio
async def test_pipeline_produces_playable_video(pipeline):
    job_id, tmp = pipeline

    from backend.worker import process_job

    await process_job(job_id)

    status, stage, err = _state(job_id)
    assert status == "done", f"pipeline failed at stage={stage}: {err}"
    assert stage is None

    final = tmp / "outputs" / job_id / "r1" / "final.mp4"
    assert final.exists(), "final.mp4 was not written"
    assert final.stat().st_size > 50_000, "final.mp4 is suspiciously small"


@pytest.mark.asyncio
async def test_every_clip_and_audio_has_its_own_file(pipeline):
    """Every DB row must point at a distinct, existing file.

    The providers default to one shared temp filename, so passing no out_path
    made all 9 clips and all 3 audio files collapse onto the same path."""
    job_id, tmp = pipeline

    from backend.worker import process_job

    await process_job(job_id)
    status, stage, err = _state(job_id)
    assert status == "done", f"failed at {stage}: {err}"

    with get_db() as db:
        cur = db.cursor()
        cur.execute("SELECT video_path FROM scene_clips WHERE job_id = ?", (job_id,))
        clip_paths = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT audio_path FROM job_scenes WHERE job_id = ?", (job_id,))
        audio_paths = [r[0] for r in cur.fetchall()]

    assert len(clip_paths) == 9, f"expected 9 clips, got {len(clip_paths)}"
    assert len(set(clip_paths)) == 9, "clip paths are not unique"
    assert len(audio_paths) == 3, f"expected 3 audio files, got {len(audio_paths)}"
    assert len(set(audio_paths)) == 3, "audio paths are not unique"

    for path in clip_paths + audio_paths:
        p = Path(path)
        assert p.exists(), f"recorded file is missing: {p}"
        assert p.stat().st_size > 1000, f"recorded file is empty: {p}"

    # Artifacts must land in the job's own output dir, not the providers'
    # shared temp filenames (which get overwritten by each next clip).
    for path in clip_paths + audio_paths:
        assert "kenburns_temp" not in path, f"clip left in shared temp file: {path}"
        assert "tts_temp" not in path, f"audio left in shared temp file: {path}"
        assert Path(path).is_relative_to(tmp), (
            f"artifact escaped the job output dir: {path}"
        )


@pytest.mark.asyncio
async def test_audio_and_video_lengths_match(pipeline):
    """The regression that motivated this test: 3 Ken Burns clips per scene
    each got the full scene duration, so the video ran 3x longer than the
    audio. Compiled output must not drift."""
    job_id, tmp = pipeline

    import subprocess
    import json
    from backend.worker import process_job

    await process_job(job_id)
    status, stage, err = _state(job_id)
    assert status == "done", f"failed at {stage}: {err}"

    final = tmp / "outputs" / job_id / "r1" / "final.mp4"

    def probe(stream):
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", stream,
                "-show_entries", "stream=duration",
                "-of", "json", str(final),
            ],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams", [])
        return float(streams[0]["duration"]) if streams else 0.0

    v = probe("v:0")
    a = probe("a:0")
    assert v > 0 and a > 0, f"missing streams: video={v} audio={a}"

    # -shortest would silently hide a video that runs long, so measure the
    # intermediate clips too: every scene's Ken Burns sub-clips together must
    # add up to that scene's audio, not 3x it.
    with get_db() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT audio_duration_sec FROM job_scenes WHERE job_id = ? ORDER BY scene_num",
            (job_id,),
        )
        audio_total = sum(r[0] or 0 for r in cur.fetchall())
        cur.execute(
            "SELECT num_frames FROM scene_clips WHERE job_id = ?", (job_id,)
        )
        frames_total = sum(r[0] or 0 for r in cur.fetchall())

    assert frames_total > 0, "no Ken Burns clips were recorded"
    video_expected = frames_total / 24.0
    ratio = video_expected / audio_total
    assert abs(video_expected - audio_total) < 0.15, (
        f"clips total {video_expected:.2f}s but audio is {audio_total:.2f}s "
        f"(ratio {ratio:.2f}x). Sub-clips must split the scene duration."
    )

    drift = abs(v - a)
    assert drift < 0.15, (
        f"audio/video drift {drift:.3f}s (video={v:.2f}s audio={a:.2f}s). "
        "Ken Burns sub-clips must share the scene duration, not each take it."
    )
