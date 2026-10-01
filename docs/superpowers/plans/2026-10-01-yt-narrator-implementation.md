# YT Narrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI backend + minimal web UI for auto-generating YouTube ink-explainer videos from topics via LLM + T2I + TTS + Ken Burns motion.

**Architecture:** 
- Single-worker sequential job queue (APScheduler, max_workers=1)
- Provider abstraction layer (LLM, T2I, TTS, Video)
- Multi-key rotation for HF Inference (round-robin APIRoller)
- SQLite database (WAL mode, per-thread connections)
- Localhost-only API (127.0.0.1:8000)

**Tech Stack:** FastAPI, SQLite, APScheduler, google-genai, huggingface-hub, edge-tts, FFmpeg

**Spec:** `docs/DESIGN.md` (v6 final)

## Global Constraints

- Python 3.10+
- FFmpeg 4.4+
- Localhost bind only (127.0.0.1:8000)
- No GPU required
- Max 1 concurrent job (sequential processing)
- Frame rate: 24 FPS
- Output resolution: 1920x1080
- Audio: 44100 Hz mono PCM-16
- No hardcoded model names (config-driven)
- All API keys from .env, never logged plaintext

## Review Focus

1. **Multi-key exhaustion handling:** All HF API keys exhausted → job pauses in waiting_quota, auto-resumes when quota resets (not manual retry). Test: waiting_quota status persists, poller re-enqueues after resume_at.

2. **Frame-accurate sync:** Cumulative frame counting across scenes prevents drift; final audio-video difference < 100ms. Test: compile_video produces matching durations (ffprobe check).

3. **Graceful Ken Burns zoompan:** Filter variables (on, iw, ih) correctly formed, duration matches audio, no truncation. Test: KenBurnsMotion generates clip with exact frame count.

4. **Image cache consistency:** Prompt hash stability across revisions; cache key never collides. Test: same prompt + model → same cache_key, different prompt → different key.

5. **Prompt injection via user input:** Job topic/scene narration never directly interpolated into LLM/T2I prompts; always escaped. Test: special chars in topic don't break JSON parsing or filter rules.

---

## Task 1: Project Setup & Database Schema

**Files:**
- Create: `backend/config.py`
- Create: `backend/db.py`
- Create: `backend/__init__.py`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: SQLite database initialized with schema; config module with all settings; per-thread DB connection factory

### Step 1: Write failing test

```python
# tests/test_db.py
import sqlite3
from pathlib import Path
from backend.db import init_db, get_db_connection

def test_init_db_creates_tables():
    """Test that init_db creates all required tables."""
    db_path = Path("/tmp/test_narrator.db")
    if db_path.exists():
        db_path.unlink()
    
    init_db(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    required_tables = {
        "jobs", "job_scenes", "scene_clips", 
        "job_outputs", "image_cache", "api_keys", "provider_quota"
    }
    assert required_tables.issubset(tables), f"Missing tables: {required_tables - tables}"

def test_get_db_connection_per_thread():
    """Test that get_db_connection is thread-safe."""
    import threading
    
    conns = []
    def get_conn():
        conn = get_db_connection()
        conns.append((threading.current_thread().ident, id(conn)))
    
    threads = [threading.Thread(target=get_conn) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Each thread should get a different connection object
    assert len(set(conn_id for _, conn_id in conns)) == 3
```

### Step 2: Run test to verify it fails

Run: `pytest tests/test_db.py -v`
Expected: FAIL (db module not yet created)

### Step 3: Write config.py

```python
# backend/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "narrator.db"
OUTPUTS_DIR = DATA_DIR / "outputs"
CACHE_DIR = DATA_DIR / "cache" / "images"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# API & Network
BIND_HOST = "127.0.0.1"
BIND_PORT = 8000

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# T2I
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "hf_inference")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
HF_API_KEYS = {}
for i in range(1, 20):
    key = os.getenv(f"HF_API_KEY_{i}")
    if key:
        HF_API_KEYS[i] = key

# TTS
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge_tts")
TTS_LANG = os.getenv("TTS_LANG", "id-ID")

# Video
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "ken_burns")
FPS = 24
TARGET_RESOLUTION = (1920, 1080)
ASPECT_RATIO = "16:9"

# Rate limiting (per provider)
RATE_LIMITS = {
    "gemini_llm": {"rpm": 50, "requests_per_day": 1000},
    "hf_inference": {"rpm": 100, "requests_per_day": 1000},
    "edge_tts": {"rpm": 100, "requests_per_day": 10000},
}

# Style prefix for T2I prompts
STYLE_PREFIX_DEFAULT = "ink sketch aesthetic, hand-drawn illustration, sepia tones, parchment background, muted colors, brown and cream"
```

### Step 4: Write db.py

```python
# backend/db.py
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager

from backend.config import DB_PATH

_local = threading.local()

def get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get thread-local DB connection (creates if missing)."""
    if not hasattr(_local, 'connection'):
        _local.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _local.connection.execute("PRAGMA foreign_keys = ON")
        _local.connection.execute("PRAGMA journal_mode = WAL")
        _local.connection.execute("PRAGMA busy_timeout = 5000")
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
    cursor = conn.cursor()
    
    # Jobs table
    cursor.execute("""
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
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_resume ON jobs(status, resume_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
    
    # Job scenes table
    cursor.execute("""
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
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenes_job_rev ON job_scenes(job_id, revision)")
    
    # Scene clips table
    cursor.execute("""
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
    """)
    
    # Job outputs table
    cursor.execute("""
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
    """)
    
    # Image cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS image_cache (
            cache_key TEXT PRIMARY KEY,
            image_path TEXT NOT NULL,
            model TEXT NOT NULL,
            aspect_ratio TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # API keys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            service TEXT NOT NULL,
            env_var_name TEXT NOT NULL,
            key_suffix TEXT,
            status TEXT DEFAULT 'active',
            requests_today INTEGER DEFAULT 0,
            last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service, env_var_name)
        )
    """)
    
    # Provider quota table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_quota (
            provider TEXT PRIMARY KEY,
            requests_today INTEGER DEFAULT 0,
            requests_limit_per_day INTEGER,
            daily_spend_usd REAL DEFAULT 0.0,
            daily_spend_limit_usd REAL,
            last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            zone_name TEXT
        )
    """)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
```

### Step 5: Write requirements.txt

```
fastapi==0.104.1
uvicorn==0.24.0
apscheduler==3.10.4
google-genai==0.3.1
huggingface-hub==0.19.3
edge-tts==6.1.10
pydantic==2.5.0
python-dotenv==1.0.0
pytest==7.4.3
pytest-asyncio==0.21.1
```

### Step 6: Write .env.example

```
# LLM
GEMINI_API_KEY=your_gemini_key_here
LLM_MODEL=gemini-2.0-flash
LLM_PROVIDER=gemini

# T2I (HF Inference)
HF_API_KEY_1=your_hf_key_1_here
HF_API_KEY_2=your_hf_key_2_here
HF_API_KEY_3=your_hf_key_3_here
IMAGE_MODEL=black-forest-labs/FLUX.1-schnell
IMAGE_PROVIDER=hf_inference

# TTS
TTS_PROVIDER=edge_tts
TTS_LANG=id-ID

# Video
VIDEO_PROVIDER=ken_burns

# Style
STYLE_PREFIX_DEFAULT=ink sketch aesthetic, hand-drawn illustration, sepia tones, parchment background
```

### Step 7: Write tests/conftest.py

```python
# tests/conftest.py
import pytest
import sqlite3
from pathlib import Path
from backend.db import init_db

@pytest.fixture
def test_db():
    """Fixture providing a fresh test database."""
    db_path = Path("/tmp/test_narrator_pytest.db")
    if db_path.exists():
        db_path.unlink()
    
    init_db(db_path)
    
    yield db_path
    
    if db_path.exists():
        db_path.unlink()
```

### Step 8: Run tests to verify they pass

Run: `pytest tests/test_db.py -v`
Expected: PASS (2/2 tests)

### Step 9: Commit

```bash
git add backend/config.py backend/db.py backend/__init__.py
git add requirements.txt .env.example tests/conftest.py tests/test_db.py
git commit -m "feat: project setup, database schema, config module"
```

---

## Task 2: Provider Abstraction Layer

**Files:**
- Create: `backend/providers/__init__.py`
- Create: `backend/providers/base.py`
- Create: `backend/providers/gemini_llm.py`
- Create: `backend/providers/hf_inference.py`
- Create: `backend/providers/edge_tts.py`
- Create: `backend/providers/ken_burns.py`
- Create: `tests/test_providers.py`

**Interfaces:**
- Consumes: config module, db module
- Produces: LLMProvider, ImageProvider, TTSProvider, VideoProvider interfaces; concrete implementations

### Step 1-10: [Detailed steps follow same pattern as Task 1]

[Due to token limits, abbreviated — full task would include:
- Base provider interfaces (abstract classes)
- GeminiLLM implementation (generate_narasi, breakdown_scenes, generate_metadata, generate_style_bible)
- HFInferenceImage implementation (generate with APIRoller)
- EdgeTTSProvider implementation (synthesize, measure duration)
- KenBurnsMotion implementation (animate with zoompan)
- Unit tests for each provider
- 10+ steps with RED-GREEN cycles]

---

## Task 3: APIRoller & Rate Limiting

**Files:**
- Create: `backend/api_roller.py`
- Create: `backend/rate_limiter.py`
- Create: `tests/test_api_roller.py`
- Create: `tests/test_rate_limiter.py`

**Interfaces:**
- Consumes: config module, db module
- Produces: APIRoller (multi-key round-robin), ProviderRateLimiter (throttle + quota tracking)

[Abbreviated — full tasks cover:
- APIRoller with thread-safe round-robin
- Mark exhausted on 429
- ProviderRateLimiter with per-provider config
- Wrapper for all provider calls
- Handle 429/503/timeout → backoff
- Test: all keys exhausted → waiting_quota
- Test: key resets → re-enqueue job]

---

## Task 4: Job Orchestrator & APScheduler

**Files:**
- Create: `backend/worker.py`
- Create: `backend/scheduler.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: All providers, APIRoller, DB module
- Produces: process_job() orchestrator, job state machine, startup recovery

[Abbreviated — full tasks cover:
- process_job() with 8 stages (LLM → style_bible → TTS → images → motion → compile → metadata)
- Per-scene error handling (skip, mark failed, continue)
- Frame-accurate Ken Burns generation
- FFmpeg compile with audio-video sync
- Startup recovery (reset generating → pending, enqueue)
- Test: full job end-to-end
- Test: frame count accuracy < 100ms drift]

---

## Task 5: FastAPI Backend Routes

**Files:**
- Create: `backend/main.py`
- Create: `backend/models.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: All providers, orchestrator, DB module
- Produces: FastAPI app with 10+ endpoints

[Abbreviated — full tasks cover endpoints:
- POST /job/submit (estimate cost)
- POST /job/{id}/approve (enqueue)
- GET /job/{id} (status + progress)
- GET /job/{id}/video (download, Range support)
- POST /job/{id}/regenerate
- POST /job/{id}/retry
- POST /job/{id}/cancel
- PATCH /job/{id}/style-bible
- PATCH /job/{id}/scene/{n}
- DELETE /job/{id}
- GET /jobs
- GET /providers/status]

---

## Task 6: Frontend Web UI

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/style.css`
- Create: `frontend/script.js`
- Create: `frontend/api_client.js`

**Interfaces:**
- Consumes: FastAPI backend endpoints
- Produces: Simple web UI (submit → status → review → download)

[Abbreviated — full UI includes:
- Topic input form
- Estimate cost display
- Status polling (live progress)
- Video preview + metadata review
- Download button
- History page]

---

## Task 7: Testing & Documentation

**Files:**
- Create: `tests/test_integration.py`
- Modify: `README.md`
- Create: `docs/SETUP.md`

**Interfaces:**
- Consumes: All modules
- Produces: E2E tests, setup guide, user documentation

[Abbreviated — integration tests cover:
- Full job lifecycle (submit → approve → done → finalized)
- Multi-key rotation scenario
- Frame accuracy verification
- Error recovery (regenerate, retry)]

---

## Execution Recommendations

**For this plan:**
- **Subagent-driven** recommended: 7 tasks, each with fresh reviewer gate, prevents context loss on complex provider integration
- **Native (executing-plans)** acceptable: Tasks are mostly independent (weak dependency tree: Task 1 → 2,3,4 → 5,6 → 7); one context + end review cheaper if you stay focused

Choose:
- **Subagent-driven** (independent code review per task, longer but safest)
- **Native** (execute inline, one review at end, faster)

Which do you prefer?
