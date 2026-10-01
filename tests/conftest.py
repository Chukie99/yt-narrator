"""Pytest fixtures."""

import pytest
import sqlite3
import tempfile
import threading
from pathlib import Path
from backend.db import init_db


@pytest.fixture
def test_db():
    """Fixture providing a fresh test database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_narrator.db"
        init_db(db_path)
        yield db_path
        
        # Close any thread-local connections
        import backend.db as db_module
        if hasattr(db_module, '_local'):
            if hasattr(db_module._local, 'connection'):
                db_module._local.connection.close()
                delattr(db_module._local, 'connection')
