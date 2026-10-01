"""Configuration module (environment-driven, no hardcodes)."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "narrator.db"
OUTPUTS_DIR = DATA_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = DATA_DIR / "cache" / "images"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# API & Network
BIND_HOST = "127.0.0.1"
BIND_PORT = 8000

# LLM (Gemini)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# T2I (HF Inference + FLUX.1)
# HF keys are NOT read from env here: they live in data/keys.json so the UI can
# add or disable them while the app is running. See backend/keystore.py.
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "hf_inference")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")

# TTS (edge-tts)
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge_tts")
TTS_LANG = os.getenv("TTS_LANG", "id-ID")

# Video (Ken Burns)
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

# Style prefix for T2I
STYLE_PREFIX_DEFAULT = os.getenv(
    "STYLE_PREFIX_DEFAULT",
    "ink sketch aesthetic, hand-drawn illustration, sepia tones, parchment background, muted colors, brown and cream"
)

# Job settings
MAX_NARASI_CHARS = 50000
MAX_SCENES = 20
