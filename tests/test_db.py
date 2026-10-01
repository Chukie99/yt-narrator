"""Tests for database module."""

import sqlite3
from pathlib import Path
from backend.db import init_db, get_db_connection


def test_init_db_creates_tables(test_db):
    """Test that init_db creates all required tables."""
    conn = sqlite3.connect(str(test_db))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    required_tables = {
        "jobs",
        "job_scenes",
        "scene_clips",
        "job_outputs",
        "image_cache",
        "api_keys",
        "provider_quota",
    }
    assert required_tables.issubset(
        tables
    ), f"Missing tables: {required_tables - tables}"


def test_init_db_foreign_keys(test_db):
    """Test that foreign key constraints are enabled."""
    conn = sqlite3.connect(str(test_db))
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # Try to insert a job_scene with non-existent job_id
    try:
        cursor.execute(
            """
            INSERT INTO job_scenes 
            (job_id, revision, scene_num, narration_text, t2i_prompt)
            VALUES ('nonexistent', 1, 1, 'test', 'test')
        """
        )
        conn.commit()
        assert False, "Should have raised foreign key constraint error"
    except sqlite3.IntegrityError:
        pass  # Expected
    finally:
        conn.close()


def test_db_schema_complete(test_db):
    """Test that all required tables and indexes exist."""
    conn = sqlite3.connect(str(test_db))
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    required = {"jobs", "job_scenes", "scene_clips", "job_outputs", "image_cache", "api_keys", "provider_quota"}
    assert required.issubset(tables), f"Missing: {required - tables}"
    
    # Check indexes
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
    )
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_jobs_status_resume" in indexes
    assert "idx_jobs_created" in indexes
    
    conn.close()
