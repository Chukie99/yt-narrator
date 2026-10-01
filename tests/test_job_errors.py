"""A crashed job must reach a terminal state with a readable reason.

Regression: the failure handler wrote status='error' on the same connection as
the failed stage and then re-raised. The db context manager rolled that UPDATE
back, so the job stayed in 'pending' forever with error_msg still NULL and the
UI polled a status that would never change.
"""
import pytest

from backend.db import get_db


@pytest.mark.asyncio
async def test_failed_stage_marks_job_error_not_pending(pipeline, monkeypatch):
    job_id, _tmp = pipeline

    from backend.providers import hf_inference
    from backend.worker import process_job

    def boom(*a, **k):
        raise RuntimeError("FLUX quota exhausted")

    monkeypatch.setattr(hf_inference.HFInferenceImage, "generate", boom)

    with pytest.raises(RuntimeError):
        await process_job(job_id)

    with get_db() as db:
        row = db.execute(
            "SELECT status, stage, error_msg FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    status, stage, error_msg = row
    assert status == "error", f"job stuck in {status!r} instead of error"
    assert error_msg, "no error message recorded"
    assert "FLUX quota exhausted" in error_msg
    assert "RuntimeError" in error_msg, "error type missing from message"


@pytest.mark.asyncio
async def test_error_message_survives_a_second_failure_in_the_handler(pipeline, monkeypatch):
    """The error path must survive its own bookkeeping, not revert the job."""
    job_id, _tmp = pipeline

    import backend.worker as worker
    from backend.worker import process_job

    def boom(*a, **k):
        raise ValueError("LLM rejected the key")

    # Patch where worker looks the provider up, not the original module: the
    # fixture already replaced worker.GeminiLLM with the stub.
    monkeypatch.setattr(worker, "GeminiLLM", boom)

    with pytest.raises(ValueError):
        await process_job(job_id)

    with get_db() as db:
        status, error_msg = db.execute(
            "SELECT status, error_msg FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    assert status == "error"
    assert "LLM rejected the key" in error_msg