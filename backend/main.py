"""FastAPI main app + routes."""

import uuid
import json
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from backend.config import BIND_HOST, BIND_PORT, DB_PATH, OUTPUTS_DIR
from backend.db import init_db, get_db
from backend.models import (
    JobSubmitRequest,
    JobEstimateResponse,
    JobStatusResponse,
    ScenePatchRequest,
    StyleBiblePatchRequest,
    KeysReplaceRequest,
    KeyToggleRequest,
)
from backend.providers.hf_inference import key_store
from backend.scheduler import scheduler, start_scheduler

# Initialize
init_db()

async def lifespan(_app: FastAPI):
    # Lifespan replaces @app.on_event("startup"), which is deprecated and never
    # ran under TestClient, so these routes were never exercised by the tests.
    await start_scheduler()
    yield


app = FastAPI(title="YT Narrator", version="0.2.0", lifespan=lifespan)

# Middleware: localhost only
@app.middleware("http")
async def enforce_localhost(request, call_next):
    # Returning a response is the only thing middleware can do. Raising
    # HTTPException here escapes the app and surfaces as a 500.
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        return JSONResponse(
            status_code=403,
            content={"detail": "App ini hanya jalan di localhost."},
        )
    return await call_next(request)


# Routes
@app.post("/job/submit", response_model=JobEstimateResponse)
async def submit_job(req: JobSubmitRequest):
    """Submit job + estimate cost."""
    job_id = str(uuid.uuid4())
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """INSERT INTO jobs (id, topic, status, estimated_images, estimated_cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, req.topic, "pending", 8, 0.0, datetime.utcnow().isoformat())
        )
    
    return JobEstimateResponse(
        job_id=job_id,
        estimated_images=8,
        estimated_cost_usd=0.0,
        estimated_duration_min="5-10"
    )


@app.post("/job/{job_id}/approve")
async def approve_job(job_id: str):
    """Approve + enqueue job."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        cursor.execute("UPDATE jobs SET status = ? WHERE id = ?", ("pending", job_id))
    
    # Enqueue to scheduler
    from backend.worker import process_job
    scheduler.add_job(process_job, args=(job_id,), id=f"job_{job_id}", replace_existing=True)
    
    return {"status": "pending"}


@app.get("/job/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    """Get job status."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, status, stage, progress, revision, error_msg, created_at FROM jobs WHERE id = ?",
            (job_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return JobStatusResponse(
            id=row[0],
            status=row[1],
            stage=row[2],
            progress=row[3],
            revision=row[4],
            error_msg=row[5],
            created_at=row[6]
        )


@app.get("/job/{job_id}/video")
async def download_video(job_id: str):
    """Download final video."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT status, current_revision FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        status, revision = row
        if status != "done" and status != "finalized":
            raise HTTPException(status_code=400, detail="Video not ready")
        
        video_path = OUTPUTS_DIR / job_id / f"r{revision}" / "final.mp4"
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video file not found")
        
        return FileResponse(
            path=video_path,
            filename=f"{job_id}_final.mp4",
            media_type="video/mp4"
        )


@app.post("/job/{job_id}/regenerate")
async def regenerate_job(job_id: str):
    """Regenerate job (full retry)."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT status, revision FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        status, old_revision = row
        if status not in ["done", "error", "cancelled"]:
            raise HTTPException(status_code=409, detail="Cannot regenerate from current status")
        
        new_revision = old_revision + 1
        cursor.execute(
            "UPDATE jobs SET status = ?, revision = ?, current_revision = ? WHERE id = ?",
            ("regenerating", new_revision, new_revision, job_id)
        )
    
    # Re-enqueue
    from backend.worker import process_job
    scheduler.add_job(process_job, args=(job_id,), id=f"job_{job_id}", replace_existing=True)
    
    return {"status": "regenerating", "revision": new_revision}


@app.post("/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel job."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        status = row[0]
        if status not in ["generating", "regenerating", "waiting_quota"]:
            raise HTTPException(status_code=409, detail="Cannot cancel from current status")
        
        cursor.execute("UPDATE jobs SET cancel_requested = 1, status = ? WHERE id = ?", ("cancelling", job_id))
    
    return {"status": "cancelling"}


@app.get("/jobs")
async def list_jobs(status: str = None, limit: int = 10):
    """List jobs."""
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        
        if status:
            cursor.execute(
                "SELECT id, status, stage, progress, created_at FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cursor.execute(
                "SELECT id, status, stage, progress, created_at FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        
        rows = cursor.fetchall()
        return {
            "jobs": [
                {
                    "id": row[0],
                    "status": row[1],
                    "stage": row[2],
                    "progress": row[3],
                    "created_at": row[4]
                }
                for row in rows
            ]
        }


@app.delete("/job/{job_id}")
async def delete_job(job_id: str):
    """Delete job (only finalized/cancelled)."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        status = row[0]
        if status not in ["finalized", "cancelled", "error"]:
            raise HTTPException(status_code=409, detail="Cannot delete from current status")
        
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    
    return {"deleted": True}


@app.patch("/job/{job_id}/style-bible")
async def update_style_bible(job_id: str, req: StyleBiblePatchRequest):
    """Edit style bible."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()
        cursor.execute("SELECT style_bible_json FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        
        current = json.loads(row[0] or "{}")
        if req.visual_themes:
            current["visual_themes"] = req.visual_themes
        if req.color_palette:
            current["color_palette"] = req.color_palette
        if req.mood:
            current["mood"] = req.mood
        
        cursor.execute("UPDATE jobs SET style_bible_json = ? WHERE id = ?", (json.dumps(current), job_id))
    
    return current


@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the single-page UI from the same origin as the API."""
    index = Path(__file__).parent.parent / "frontend" / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index)


@app.get("/keys")
async def list_keys():
    """List saved HF keys. Tokens are redacted; the browser never sees them whole."""
    return {"keys": key_store.listing()}


@app.put("/keys")
async def replace_keys(req: KeysReplaceRequest):
    """Replace the whole key pool, one token per line."""
    lines = req.text.splitlines()
    count = key_store.set_tokens(lines)
    return {"saved": count, "keys": key_store.listing()}


@app.patch("/keys/{index}")
async def toggle_key(index: int, req: KeyToggleRequest):
    """Enable or disable one key without deleting it."""
    try:
        key_store.set_enabled(index, req.enabled)
    except IndexError:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"keys": key_store.listing()}


@app.delete("/keys/{index}")
async def delete_key(index: int):
    """Remove one key from the pool."""
    try:
        key_store.delete(index)
    except IndexError:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"keys": key_store.listing()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT)
