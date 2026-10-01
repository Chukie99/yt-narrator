"""Database module (SQLite WAL, per-thread connections)."""

import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager

from backend.config import DB_PATH

_local = threading.local()


def set_db_path(db_path: Path):
    """Point the thread-local connection at another database file.

    get_db_connection memoizes on the thread, so switching paths also has to
    drop the cached connection. Tests use this to stop writing jobs into the
    production database.
    """
    if hasattr(_local, "connection"):
        try:
            _local.connection.close()
        except Exception:
            pass
        delattr(_local, "connection")
    _local.db_path = Path(db_path)


def get_db_connection(db_path: Path = None) -> sqlite3.Connection:
    """Get thread-local DB connection (creates if missing)."""
    db_path = Path(db_path or getattr(_local, "db_path", None) or DB_PATH)
    if not hasattr(_local, "connection") or getattr(_local, "path", None) != db_path:
        if hasattr(_local, "connection"):
            try:
                _local.connection.close()
            except Exception:
                pass
        _local.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _local.connection.execute("PRAGMA foreign_keys = ON")
        _local.connection.execute("PRAGMA journal_mode = WAL")
        _local.connection.execute("PRAGMA busy_timeout = 5000")
        _local.path = db_path
    return _local.connection


@contextmanager
def get_db():
    """Context manager for DB transaction."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(db_path: Path = DB_PATH):
    """Initialize database schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    cursor = conn.cursor()

    # Jobs table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            stage TEXT,
            progress TEXT DEFAULT '',
            revision INTEGER DEFAULT 1,
            
            estimated_cost_usd REAL DEFAULT 0.0,
            actual_cost_usd REAL DEFAULT 0.0,
            estimated_images INTEGER DEFAULT 0,
            
            narasi_full TEXT,
            style_bible_json TEXT,
            cancel_requested BOOLEAN DEFAULT 0,
            
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            completed_at DATETIME,
            finalized_at DATETIME,
            resume_at DATETIME,
            
            error_msg TEXT,
            current_revision INTEGER DEFAULT 1,
            
            CONSTRAINT status_valid CHECK (status IN (
                'pending', 'generating', 'waiting_quota', 'done', 'error',
                'regenerating', 'cancelling', 'cancelled', 'finalized'
            ))
        )
    """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status_resume ON jobs(status, resume_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)"
    )

    # Job scenes table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_scenes (
            job_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            scene_num INTEGER NOT NULL,
            segment_id INTEGER,
            
            narration_text TEXT NOT NULL,
            t2i_prompt TEXT NOT NULL,
            tts_text TEXT,
            
            audio_path TEXT,
            audio_duration_sec REAL,
            image_path TEXT,
            
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            attempt INTEGER DEFAULT 1,
            
            PRIMARY KEY (job_id, revision, scene_num),
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        )
    """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scenes_job_rev ON job_scenes(job_id, revision)"
    )

    # Scene clips table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_clips (
            job_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            scene_num INTEGER NOT NULL,
            clip_variant INTEGER NOT NULL,
            
            composition TEXT NOT NULL,
            video_path TEXT NOT NULL,
            num_frames INTEGER NOT NULL,
            
            PRIMARY KEY (job_id, revision, scene_num, clip_variant),
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        )
    """
    )

    # Job outputs table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_outputs (
            job_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            
            video_path TEXT,
            title TEXT,
            description TEXT,
            hashtags TEXT,
            
            finalized_at DATETIME,
            
            PRIMARY KEY (job_id, revision),
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        )
    """
    )

    # Image cache table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS image_cache (
            cache_key TEXT PRIMARY KEY,
            image_path TEXT NOT NULL,
            model TEXT NOT NULL,
            aspect_ratio TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # API keys table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            service TEXT NOT NULL,
            env_var_name TEXT NOT NULL,
            key_suffix TEXT,
            status TEXT DEFAULT 'active',
            requests_today INTEGER DEFAULT 0,
            last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service, env_var_name)
        )
    """
    )

    # Provider quota table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_quota (
            provider TEXT PRIMARY KEY,
            requests_today INTEGER DEFAULT 0,
            requests_limit_per_day INTEGER,
            daily_spend_usd REAL DEFAULT 0.0,
            daily_spend_limit_usd REAL,
            last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            zone_name TEXT
        )
    """
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
