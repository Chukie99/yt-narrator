"""APScheduler job queue setup."""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from datetime import datetime, timedelta
from backend.db import get_db
from backend.worker import process_job

scheduler = AsyncIOScheduler()
scheduler.configure(
    jobstores={"default": MemoryJobStore()},
    job_defaults={"coalesce": True, "max_instances": 1},
    timezone="UTC",
)


async def startup_recovery():
    """Recover hanging jobs at startup."""
    with get_db() as db:
        db.execute("PRAGMA foreign_keys = ON")
        cursor = db.cursor()

        # Reset generating → pending
        cursor.execute(
            "UPDATE jobs SET status = ? WHERE status = ?", ("pending", "generating")
        )

        # Enqueue pending jobs
        cursor.execute("SELECT id FROM jobs WHERE status = ? ORDER BY created_at", ("pending",))
        for row in cursor.fetchall():
            job_id = row[0]
            scheduler.add_job(
                process_job,
                args=(job_id,),
                id=f"job_{job_id}",
                replace_existing=True,
            )


async def quota_poller():
    """Poll for waiting_quota jobs to resume."""
    while True:
        try:
            now = datetime.utcnow()

            with get_db() as db:
                db.execute("PRAGMA foreign_keys = ON")
                cursor = db.cursor()

                # Find jobs ready to resume
                cursor.execute(
                    """SELECT id FROM jobs 
                       WHERE status = ? AND resume_at IS NOT NULL AND resume_at <= ?
                       ORDER BY resume_at""",
                    ("waiting_quota", now.isoformat()),
                )

                for row in cursor.fetchall():
                    job_id = row[0]
                    cursor.execute(
                        "UPDATE jobs SET status = ? WHERE id = ?", ("pending", job_id)
                    )

                    scheduler.add_job(
                        process_job,
                        args=(job_id,),
                        id=f"job_{job_id}",
                        replace_existing=True,
                    )

            await asyncio.sleep(60)  # Poll every minute

        except Exception as e:
            print(f"Quota poller error: {e}")
            await asyncio.sleep(60)


async def start_scheduler():
    """Start scheduler + startup recovery. Must be awaited from inside the
    running loop: calling loop.run_until_complete() here would raise
    "This event loop is already running"."""
    await startup_recovery()

    # Resume jobs that were waiting on quota when the app was closed.
    asyncio.create_task(quota_poller())

    scheduler.start()


if __name__ == "__main__":
    asyncio.run(startup_recovery())
    print("Recovery complete")
