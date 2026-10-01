# YT Narrator — Design Specification v6 (Final)

**Last Updated:** 2026-10-01  
**Status:** Ready for implementation  
**Target:** Auto-generate YouTube ink-explainer videos from topic/title

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Pipeline](#pipeline)
4. [Data Model](#data-model)
5. [Providers](#providers)
6. [API Endpoints](#api-endpoints)
7. [State Machine](#state-machine)
8. [Security](#security)
9. [Deployment](#deployment)
10. [FAQ & Decisions](#faq--decisions)

---

## Overview

### Goal

Build a Python FastAPI application that:
- Takes user input (topic/title)
- Auto-generates YouTube-ready videos (16:9, 1920x1080, H.264+AAC)
- **Style:** Ink-explainer aesthetic (hand-drawn feel, muted colors, Ken Burns motion)
- **Process:** LLM breakdown → TTS → T2I (FLUX.1) → Ken Burns animation → compile
- **Cost:** $0 (fully gratis via HF + Google free tier)

### Key Features

- **Multi-key rotation:** 5-10 HF API keys (rotate on quota exhaustion)
- **Frame-accurate sync:** Audio-video drift < 100ms
- **Revision tracking:** Full or per-scene re-generation
- **Image caching:** By prompt hash (no re-gen if unchanged)
- **Style consistency:** Auto-generated visual style guide applied to all scenes
- **Metadata generation:** Title, description, hashtags (LLM-generated, editable)

### Tech Stack

```
Backend:     FastAPI (Python 3.10+)
Job Queue:   APScheduler (max 1 concurrent job)
Database:    SQLite (WAL mode, per-thread connection)
LLM:         Google Gemini (free tier, via google-genai SDK)
T2I:         HF Inference API + FLUX.1 Schnell (5-10 multi-key pool)
TTS:         edge-tts (free, Indonesian native)
Video:       FFmpeg 4.4+ (Ken Burns motion synthesis)
Frontend:    Simple web UI (HTML/JS polling)
```

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         FastAPI Server                       │
│                      (localhost:8000)                        │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Routes (POST/GET/PATCH/DELETE)                       │   │
│  │  • POST /job/submit (topic → estimate cost)          │   │
│  │  • GET  /job/{id} (status + progress)                │   │
│  │  • POST /job/{id}/approve (start processing)         │   │
│  │  • GET  /job/{id}/video (download final video)       │   │
│  │  • POST /job/{id}/regenerate (full retry)            │   │
│  │  • POST /job/{id}/retry (resume failed scenes)       │   │
│  │  • POST /job/{id}/cancel (cancel job)                │   │
│  │  • DELETE /job/{id} (delete finalized only)          │   │
│  │  • PATCH /job/{id}/style-bible (edit style guide)    │   │
│  │  • PATCH /job/{id}/scene/{n} (edit single scene)     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Job Orchestrator (APScheduler)                       │   │
│  │  • process_job(job_id) — run pipeline stages        │   │
│  │  • Max workers = 1 (sequential processing)           │   │
│  │  • Quota poller (check resume_at, re-enqueue)        │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Provider Layer (Abstraction)                         │   │
│  │                                                       │   │
│  │  LLMProvider:                                        │   │
│  │   └─ Impl: GeminiLLM (google-genai SDK)              │   │
│  │      • generate_narasi(topic) → full narration      │   │
│  │      • breakdown_scenes(narasi, style_bible)        │   │
│  │        → [{narration, t2i_prompt, duration_est}]    │   │
│  │      • generate_metadata(narasi)                    │   │
│  │        → {title, description, hashtags}             │   │
│  │      • generate_style_bible(narasi)                 │   │
│  │        → {visual_themes, mood, palette}             │   │
│  │                                                       │   │
│  │  ImageProvider:                                      │   │
│  │   └─ Impl: HFInferenceImage (FLUX.1 Schnell)         │   │
│  │      • generate(prompt, aspect_ratio, attempt)      │   │
│  │      • Return: image_path (cached by hash)           │   │
│  │                                                       │   │
│  │  TTSProvider:                                        │   │
│  │   └─ Impl: EdgeTTSProvider (free, Indonesian)        │   │
│  │      • synthesize(text, lang="id-ID") → wav_path     │   │
│  │      • Output: WAV 44100 Hz mono (PCM-16)            │   │
│  │                                                       │   │
│  │  VideoProvider:                                      │   │
│  │   └─ Impl: KenBurnsMotion (FFmpeg zoompan)           │   │
│  │      • animate(image_path, duration, composition)   │   │
│  │      • Return: mp4_path (Ken Burns clip)             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ APIRoller (Multi-key rotation)                       │   │
│  │  • Round-robin per-key quota tracking                │   │
│  │  • Mark exhausted on 429 → try next key              │   │
│  │  • All keys exhausted → waiting_quota (pause job)    │   │
│  │  • Key pool: HF_API_KEY_1, HF_API_KEY_2, ...         │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ProviderRateLimiter (Throttle per provider)          │   │
│  │  • Per-provider config: RPM, request/day, timeout    │   │
│  │  • Wrapper for ALL provider calls                    │   │
│  │  • Handle 429/503/timeout → backoff + retry          │   │
│  │  • Track actual_cost_usd (always $0, for future)     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Database (SQLite WAL mode)                           │   │
│  │  • jobs, job_scenes, scene_clips                     │   │
│  │  • image_cache, api_keys, provider_quota             │   │
│  │  • Per-thread connection (thread-safe)               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Request Flow

```
1. User submits topic/title
   POST /job/submit { "topic": "Kenapa langit biru" }
   ↓
2. Backend estimates (LLM narasi gen + image count)
   Response: { estimated_cost_usd: 0.00, estimated_images: 8, estimated_duration_min: "5-10" }
   ↓
3. User approves
   POST /job/{id}/approve
   ↓
4. Status: "pending" → enqueue job
   ↓
5. Worker process_job():
   • Stage 1: LLM generate narasi + breakdown scenes
   • Stage 2: Style bible (extracted from narasi)
   • Stage 3: TTS (per scene, measure duration)
   • Stage 4: T2I (per scene, cache by hash)
   • Stage 5: Ken Burns (per scene, 3-5 sub-clips)
   • Stage 6: Compile (concat video + audio)
   ↓
6. Status: "done" (draft ready)
   ↓
7. User reviews video (GET /job/{id}/video)
   • Approve → Status: "finalized"
   • Regenerate → Status: "regenerating" (goto Stage 1)
   • Edit scene → PATCH /job/{id}/scene/{n} (goto Stage 3 for that scene)
   ↓
8. Download
   GET /job/{id}/video (1920x1080, H.264+AAC, ready for upload)
   + Copy metadata (title, desc, hashtags)
```

---

## Pipeline

### Stages (Sequential, max_workers=1)

#### Stage 1: LLM Generation & Breakdown

**Input:** Topic/title (string, 1-200 chars)

**LLM Call (Gemini):**
```python
prompt = f"""
Generate a comprehensive narration script for an educational video about: {topic}

Requirements:
1. Write full narration (500-2000 words) suitable for a 5-15 minute video
2. Break narration into scenes (6-12 scenes typical)
3. Each scene should be 30-120 seconds when spoken naturally
4. For each scene, provide:
   - narration_text: the exact words to speak (30-150 words per scene)
   - t2i_prompt: detailed visual description in ENGLISH for AI image generation
     (must match the narration content perfectly)
   - duration_est: estimated duration in seconds

Output format (JSON):
{{
  "full_narasi": "...",
  "scenes": [
    {{
      "scene_num": 1,
      "narration_text": "...",
      "t2i_prompt": "ink sketch style, ...",
      "duration_est": 45
    }},
    ...
  ]
}}

Important: Ensure narration is coherent and visual descriptions match the narrative exactly.
"""

response = gemini.generate_content(prompt)
output = json.loads(response.text)
```

**Output to DB:**
- `jobs.style_bible_json`: Extracted after stage 1 (visual themes, palette, mood)
- `job_scenes`: Insert rows with narration_text, t2i_prompt
- Status: "generating", stage: "llm"

**Failure:** LLM error → status: "error", store error message

---

#### Stage 2: Style Bible (Extracted, User-Editable)

**Auto-generated from narasi:**
```json
{
  "visual_themes": ["ink sketch", "historical", "minimalist"],
  "color_palette": ["brown", "cream", "sepia"],
  "mood": "educational, serious, contemplative",
  "character_style": "semi-caricature, expressive, stylized",
  "backgrounds": "parchment texture, simple, muted",
  "artistic_technique": "hatching, cross-hatching, organic lines"
}
```

**User can PATCH:**
```
PATCH /job/{id}/style-bible
{ "color_palette": ["black", "white", "gold"], "mood": "dramatic" }
```

**Applied to all T2I prompts** as prefix (concatenated before base prompt).

**Status:** "generating", stage: "style_bible"

---

#### Stage 3: TTS (Per-Scene)

**For each scene:**
```python
# Input: narration_text
wav_path, duration_sec = edge_tts.synthesize(
    text=narration_text,
    lang="id-ID",
    output_path=f"outputs/{job_id}/r{revision}/scene_{scene_num}.wav"
)
# Output: WAV 44100 Hz, mono, PCM-16
```

**Measure duration via ffprobe:**
```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 scene_N.wav
```

**DB update:**
- `job_scenes.audio_path`: Path to WAV
- `job_scenes.audio_duration_sec`: Measured duration (e.g., 42.5 sec)

**Jeda antar scene:** 150-250 ms built-in to TTS pause (natural speech flow, included in duration).

**Status:** "generating", stage: "tts"

---

#### Stage 4: T2I Image Generation (Per-Scene)

**For each scene:**

```python
# Build prompt with style prefix
style_prefix = jobs.style_bible_json  # or user-edited version
full_prompt = f"{style_prefix}. {scene.t2i_prompt}"

# Cache key = hash(model + aspect_ratio + full_prompt)
cache_key = hashlib.sha256(
    f"{model}|{aspect_ratio}|{full_prompt}".encode()
).hexdigest()

# Check cache
cached = image_cache.get(cache_key)
if cached and not bypass_cache:
    image_path = cached.image_path
else:
    # Call HF Inference (with APIRoller for multi-key)
    api_key = api_roller.get_key()  # Round-robin
    image_path = hf_inference.generate(
        model="black-forest-labs/FLUX.1-schnell",
        prompt=full_prompt,
        aspect_ratio="16:9",  # or "9:16" if configured
        api_key=api_key,
        attempt=attempt_num
    )
    # Save to cache
    cache[cache_key] = image_path
```

**Output:** `cache/images/{cache_key}.png` (1920x1080 or native model resolution)

**Upscale if needed:**
```bash
ffmpeg -i {original}.png -vf scale=1920:1080:force_original_aspect_ratio=decrease {upscaled}.png
```

**Handle errors:**
- 429 (quota): APIRoller.mark_exhausted() → try next key
- All keys exhausted → waiting_quota (pause job, resume next day or when key resets)
- 503 (model loading): backoff exponential, retry
- Content filter (blocked prompt): rewrite prompt (less explicit) + retry (max 3 attempts)

**DB update:**
- `job_scenes.image_path`: Cached image path
- `image_cache`: Insert/update cache entry

**Status:** "generating", stage: "images"

---

#### Stage 5: Ken Burns Motion (Per-Scene, 3-5 Sub-Clips)

**For each scene:**

**Frame calculation (cumulative across job):**
```python
# Cumulative frame count (entire video)
fps = 24
cum_start_sec = sum(audio_duration for scenes before current)
cum_end_sec = cum_start_sec + current_audio_duration

frame_start = round(cum_start_sec * fps)
frame_end = round(cum_end_sec * fps)
num_frames = frame_end - frame_start

# Divide into 3-5 sub-clips (equal frame distribution)
num_clips = 3  # or config: 3-5
frames_per_clip = num_frames // num_clips
sub_clips = [
    (frame_start + i*frames_per_clip, frame_start + (i+1)*frames_per_clip)
    for i in range(num_clips)
]
```

**Composition patterns (rotate per scene):**

```python
compositions = ["wide", "close-up", "from-top"]
composition = compositions[scene_num % 3]

# Zoompan filter patterns
patterns = {
    "wide": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "close-up": "z='min(1+0.002*on,1.5)':x='iw/4':y='ih/4'",
    "from-top": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='0'",
}
zoompan = patterns[composition]
```

**Generate sub-clip video:**

```bash
ffmpeg -y -loop 1 -i {upscaled_image} \
  -vf "zoompan={zoompan}:d=1:s=1920x1080:fps=24" \
  -t {duration_sec_for_this_clip} \
  -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p -r 24 \
  -an \
  {output_path}
```

**Variables explained:**
- `on`: Output frame number (filter variable, increments per frame)
- `iw/ih`: Input width/height
- `zoom`: Current zoom level (calculated by filter)
- `d=1`: Zoompan depth (controls zoom interpolation range)
- `-t {duration}`: Stop at exactly this duration (synchronize to audio)

**DB update:**
- `scene_clips`: Insert row per sub-clip
  - (job_id, revision, scene_num, clip_variant)
  - composition, video_path, num_frames

**Status:** "generating", stage: "motion"

---

#### Stage 6: Compile (Concat + Mux)

**Inputs:**
- All scene Ken Burns videos (per-clip)
- All scene WAV files

**Step 1: Concat video clips (lossless)**

```bash
# Build concat demux
cat > concat_video.txt << EOF
file '/path/to/scene_1_wide_kenburns.mp4'
file '/path/to/scene_1_close-up_kenburns.mp4'
file '/path/to/scene_1_from-top_kenburns.mp4'
file '/path/to/scene_2_wide_kenburns.mp4'
...
EOF

ffmpeg -y -f concat -safe 0 -i concat_video.txt \
  -c:v copy \
  video_concat.mp4
```

**Step 2: Concat audio clips (lossless WAV)**

```bash
# Build concat demux
cat > concat_audio.txt << EOF
file '/path/to/scene_1.wav'
file '/path/to/scene_2.wav'
...
EOF

ffmpeg -y -f concat -safe 0 -i concat_audio.txt \
  -c:a pcm_s16le -ar 44100 -ac 1 \
  narasi_mixed.wav
```

**Step 3: Mux video + audio + encode**

```bash
ffmpeg -y \
  -i video_concat.mp4 -i narasi_mixed.wav \
  -c:v copy -c:a aac -b:a 128k -shortest \
  -map 0:v:0 -map 1:a:0 \
  -pix_fmt yuv420p \
  final.mp4
```

**Output:** `outputs/{job_id}/r{revision}/final.mp4`
- Resolution: 1920x1080
- Video codec: H.264
- Audio codec: AAC 128 kbps
- Frame rate: 24 fps
- Duration: Audio duration (video trimmed/padded to match)

**Verify sync:**
```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 final.mp4
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 narasi_mixed.wav

# Drift should be < 100 ms
```

**DB update:**
- `job_outputs`: Insert (job_id, revision)
  - video_path: final.mp4
  - title, description, hashtags (from metadata stage)
- Status: "done"

---

### Stage 7: Metadata (Parallel with compile, optional)

**LLM call (Gemini):**
```python
prompt = f"""
Based on this narration script, generate YouTube metadata:

{full_narasi}

Provide JSON:
{{
  "title": "...",  # 30-60 chars, SEO-friendly
  "description": "...",  # 1000-5000 chars, engaging
  "hashtags": ["tag1", "tag2", ...]  # 5-15 hashtags
}}
"""

response = gemini.generate_content(prompt)
metadata = json.loads(response.text)
```

**DB update:**
- `job_outputs`: title, description, hashtags (same row as Stage 6)

---

## Data Model

### Database Schema (SQLite)

```sql
-- Jobs table
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,  -- UUID v4
    topic TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending, generating, waiting_quota, done, error, regenerating, cancelling, cancelled, finalized
    stage TEXT,  -- llm, style_bible, tts, images, motion, compile, metadata
    progress TEXT DEFAULT '',  -- "TTS 5/10", "Images 8/12", etc.
    revision INTEGER DEFAULT 1,
    
    -- Processing
    estimated_cost_usd REAL DEFAULT 0.0,
    actual_cost_usd REAL DEFAULT 0.0,
    estimated_images INTEGER DEFAULT 0,
    
    -- Content
    narasi_full TEXT,
    style_bible_json TEXT,  -- JSON, editable by user
    cancel_requested BOOLEAN DEFAULT 0,
    
    -- Timing
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    completed_at DATETIME,
    finalized_at DATETIME,
    resume_at DATETIME,  -- when to resume from waiting_quota
    
    -- Error handling
    error_msg TEXT,  -- error details (max 2000 chars)
    
    -- Current revision (for video endpoint)
    current_revision INTEGER DEFAULT 1,
    
    CONSTRAINT status_valid CHECK (status IN (
        'pending', 'generating', 'waiting_quota', 'done', 'error',
        'regenerating', 'cancelling', 'cancelled', 'finalized'
    ))
);

CREATE INDEX idx_jobs_status_resume ON jobs(status, resume_at);
CREATE INDEX idx_jobs_created ON jobs(created_at DESC);

-- Job scenes
CREATE TABLE job_scenes (
    job_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    scene_num INTEGER NOT NULL,
    segment_id INTEGER,  -- which segment this scene came from
    
    narration_text TEXT NOT NULL,  -- exact words to speak
    t2i_prompt TEXT NOT NULL,  -- image prompt (English)
    tts_text TEXT,  -- optional: override pronunciation (numbers, abbreviations)
    
    audio_path TEXT,  -- path to WAV file
    audio_duration_sec REAL,  -- measured via ffprobe
    image_path TEXT,  -- path to generated image (cached)
    
    status TEXT DEFAULT 'pending',  -- pending, done, failed
    error_msg TEXT,  -- if failed
    
    attempt INTEGER DEFAULT 1,  -- retry counter
    
    PRIMARY KEY (job_id, revision, scene_num),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX idx_scenes_job_rev ON job_scenes(job_id, revision);

-- Scene clips (Ken Burns sub-clips per scene)
CREATE TABLE scene_clips (
    job_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    scene_num INTEGER NOT NULL,
    clip_variant INTEGER NOT NULL,  -- 1, 2, 3, ... (sub-clip order)
    
    composition TEXT NOT NULL,  -- wide, close-up, from-top
    video_path TEXT NOT NULL,
    num_frames INTEGER NOT NULL,  -- frame count for this clip
    
    PRIMARY KEY (job_id, revision, scene_num, clip_variant),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

-- Job outputs (final video + metadata)
CREATE TABLE job_outputs (
    job_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    
    video_path TEXT,
    title TEXT,
    description TEXT,
    hashtags TEXT,  -- JSON array
    
    finalized_at DATETIME,
    
    PRIMARY KEY (job_id, revision),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

-- Image cache (avoid re-generating same prompt)
CREATE TABLE image_cache (
    cache_key TEXT PRIMARY KEY,  -- hash(model + aspect + prompt)
    image_path TEXT NOT NULL,
    model TEXT NOT NULL,
    aspect_ratio TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- API keys & quota tracking
CREATE TABLE api_keys (
    service TEXT NOT NULL,  -- "hf_inference"
    env_var_name TEXT NOT NULL,  -- "HF_API_KEY_1", "HF_API_KEY_2", ...
    key_suffix TEXT,  -- last 4 chars for UI display
    status TEXT DEFAULT 'active',  -- active, exhausted
    requests_today INTEGER DEFAULT 0,
    last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (service, env_var_name)
);

-- Provider quota tracking (for future multi-provider setup)
CREATE TABLE provider_quota (
    provider TEXT PRIMARY KEY,  -- "gemini_llm", "hf_image", "edge_tts"
    requests_today INTEGER DEFAULT 0,
    requests_limit_per_day INTEGER,
    daily_spend_usd REAL DEFAULT 0.0,
    daily_spend_limit_usd REAL,
    last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
    zone_name TEXT  -- timezone for reset (e.g., "UTC", "Asia/Jakarta")
);
```

---

## Providers

### Provider Interface Pattern

```python
# Base interfaces (abstract)

class LLMProvider(ABC):
    @abstractmethod
    async def generate_narasi(self, topic: str) -> dict:
        """topic → full narration"""
        pass
    
    @abstractmethod
    async def breakdown_scenes(self, narasi: str, style_bible: dict) -> dict:
        """narasi + style_bible → scenes list"""
        pass
    
    @abstractmethod
    async def generate_metadata(self, narasi: str) -> dict:
        """narasi → {title, description, hashtags}"""
        pass
    
    @abstractmethod
    async def generate_style_bible(self, narasi: str) -> dict:
        """narasi → {visual_themes, mood, palette}"""
        pass

class ImageProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        attempt: int = 1,
        out_path: Optional[Path] = None
    ) -> Path:
        """prompt → image file path"""
        pass

class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        lang: str = "id-ID",
        out_path: Optional[Path] = None
    ) -> tuple[Path, float]:
        """text → (wav_path, duration_sec)"""
        pass

class VideoProvider(ABC):
    @abstractmethod
    async def animate(
        self,
        image_path: Path,
        duration_sec: float,
        composition: str = "wide",
        num_frames: Optional[int] = None,
        out_path: Optional[Path] = None
    ) -> Path:
        """image + duration → video clip path (Ken Burns)"""
        pass
```

### Implementation: GeminiLLM

```python
from google import genai

class GeminiLLM(LLMProvider):
    def __init__(self, api_key: str, model: str = None):
        self.client = genai.Client(api_key=api_key)
        self.model = model or config.LLM_MODEL  # from config, not hardcoded
    
    async def generate_narasi(self, topic: str) -> dict:
        prompt = f"Generate narration for: {topic}"
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt
        )
        # Parse JSON from response.text
        return json.loads(response.text)
    
    # ... other methods
```

### Implementation: HFInferenceImage

```python
from huggingface_hub import InferenceClient

class HFInferenceImage(ImageProvider):
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("HF_API_KEY")
        self.model = model or config.IMAGE_MODEL  # "black-forest-labs/FLUX.1-schnell"
        self.client = InferenceClient(api_key=self.api_key)
    
    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        attempt: int = 1,
        out_path: Optional[Path] = None
    ) -> Path:
        image_bytes = await self.client.text_to_image(
            prompt=prompt,
            model=self.model,
            height=1080,  # or derived from aspect_ratio
            width=1920
        )
        out_path = out_path or Path(f"temp_{uuid4()}.png")
        out_path.write_bytes(image_bytes)
        return out_path
```

### Implementation: EdgeTTSProvider

```python
from edge_tts import Communicate

class EdgeTTSProvider(TTSProvider):
    async def synthesize(
        self,
        text: str,
        lang: str = "id-ID",
        out_path: Optional[Path] = None
    ) -> tuple[Path, float]:
        voice = f"{lang}-Neural2-A"  # e.g. "id-ID-GadisNeural"
        
        # Save as MP3
        mp3_path = (out_path or Path(f"temp_{uuid4()}.mp3"))
        communicate = Communicate(text, voice)
        await communicate.save(str(mp3_path))
        
        # Convert MP3 → WAV 44100 mono
        wav_path = out_path or mp3_path.with_suffix(".wav")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(mp3_path),
            "-ar", "44100", "-ac", "1", str(wav_path)
        ], check=True, capture_output=True, timeout=60)
        
        # Measure duration
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(wav_path)
        ], capture_output=True, text=True, check=True)
        duration_sec = float(result.stdout.strip())
        
        mp3_path.unlink()  # cleanup
        return wav_path, duration_sec
```

### Implementation: KenBurnsMotion

```python
class KenBurnsMotion(VideoProvider):
    async def animate(
        self,
        image_path: Path,
        duration_sec: float,
        composition: str = "wide",
        num_frames: Optional[int] = None,
        out_path: Optional[Path] = None
    ) -> Path:
        # Upscale image
        upscaled = image_path.with_stem(f"{image_path.stem}_upscaled")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(image_path),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease",
            str(upscaled)
        ], check=True, capture_output=True, timeout=60)
        
        # Ken Burns pattern
        patterns = {
            "wide": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
            "close-up": "z='min(1+0.002*on,1.5)':x='iw/4':y='ih/4'",
            "from-top": "z='min(1+0.0015*on,1.5)':x='iw/2-(iw/zoom/2)':y='0'",
        }
        zoompan = patterns.get(composition, patterns["wide"])
        
        # Generate video
        fps = 24
        out_path = out_path or Path(f"temp_{uuid4()}.mp4")
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(upscaled),
            "-vf", f"{zoompan}:d=1:s=1920x1080:fps={fps}",
            "-t", str(duration_sec),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-an", str(out_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        
        upscaled.unlink()
        return out_path
```

### Multi-Key Rotation: APIRoller

```python
class APIRoller:
    def __init__(self, service: str, env_prefix: str = None):
        self.service = service
        self.env_prefix = env_prefix or service.upper()
        self.keys = self._load_keys()
        self.current_idx = 0
    
    def _load_keys(self) -> dict:
        """Load keys from .env: HF_API_KEY_1, HF_API_KEY_2, ..."""
        keys = {}
        for i in range(1, 20):  # Support up to 20 keys
            env_var = f"{self.env_prefix}_API_KEY_{i}"
            key = os.getenv(env_var)
            if key:
                keys[i] = key
        return keys
    
    async def get_key(self) -> str:
        """Round-robin next active key. If all exhausted, raise."""
        if not self.keys:
            raise ValueError(f"No {self.env_prefix}_API_KEY_* keys found")
        
        start_idx = self.current_idx
        attempts = 0
        while attempts < len(self.keys):
            idx = self.current_idx % len(self.keys)
            self.current_idx = (self.current_idx + 1) % len(self.keys)
            
            key_id = list(self.keys.keys())[idx]
            db_entry = db.query(api_keys).filter(
                api_keys.service == self.service,
                api_keys.env_var_name == f"{self.env_prefix}_API_KEY_{key_id}"
            ).first()
            
            if db_entry and db_entry.status == "active":
                return self.keys[key_id]
            
            attempts += 1
        
        raise QuotaExhausted(f"All {self.service} keys exhausted")
    
    async def mark_exhausted(self, key: str, retry_after_sec: int = None):
        """Mark key as exhausted after 429."""
        db.update(api_keys).values(
            status="exhausted",
            last_reset=datetime.now() + timedelta(seconds=retry_after_sec or 3600)
        ).where(...).execute()
```

---

## API Endpoints

### Core Endpoints

```
POST /job/submit
  Body: { "topic": "Kenapa langit biru" }
  Response: {
    "job_id": "uuid",
    "estimated_cost_usd": 0.0,
    "estimated_images": 8,
    "estimated_duration_min": "5-10"
  }
  Note: Does NOT start processing (estimate only)

POST /job/{id}/approve
  Body: {} (empty)
  Action: Enqueue job for processing
  Response: { "status": "pending" }

GET /job/{id}
  Response: {
    "id": "uuid",
    "status": "generating",
    "stage": "tts",
    "progress": "5/10",
    "revision": 1,
    "created_at": "...",
    "error_msg": null,
    "video_url": "/video/{id}",  (only if status == "done")
    "metadata": {  (only if status == "done")
      "title": "...",
      "description": "...",
      "hashtags": [...]
    }
  }

GET /job/{id}/video
  Query: ?start=0&end=1920x1080  (optional Range header)
  Response: Video file (1920x1080, H.264+AAC)
  Status: 200 OK (full), 206 Partial Content (Range), 404 (not ready)

POST /job/{id}/approve-metadata
  Body: { "title": "...", "description": "...", "hashtags": [...] }
  Action: Approve metadata, finalize job
  Response: { "status": "finalized" }

POST /job/{id}/regenerate
  Query: ?type=full|scene&scene_num=1  (optional for single scene)
  Body: { "bypass_cache": false }
  Action: Trigger re-generation (full job or single scene)
  Response: { "status": "regenerating", "revision": 2 }

POST /job/{id}/retry
  Body: {} (empty)
  Action: Resume failed scenes only
  Response: { "status": "generating", "resume_from": "tts" }

PATCH /job/{id}/style-bible
  Body: { "color_palette": [...], "mood": "..." }
  Action: Edit style guide (apply to all future T2I calls)
  Response: { "style_bible": {...}, "applied_to_scenes": "all" }

PATCH /job/{id}/scene/{n}
  Body: { "narration_text": "...", "t2i_prompt": "..." }
  Action: Edit single scene, re-process from TTS onwards
  Response: { "scene_num": n, "status": "regenerating" }

POST /job/{id}/cancel
  Body: {} (empty)
  Action: Cancel job (only if generating/regenerating/waiting_quota)
  Response: { "status": "cancelling" }

DELETE /job/{id}
  Action: Delete job (only if finalized/cancelled/error/waiting_quota)
  Response: { "deleted": true }

GET /jobs
  Query: ?status=done&limit=10&offset=0
  Response: { "jobs": [...], "total": 50 }

GET /providers/status
  Response: {
    "hf_inference": {
      "active_keys": 3,
      "exhausted_keys": 2,
      "requests_today": 145,
      "requests_limit": 1000
    },
    "gemini": {...}
  }
```

---

## State Machine

### Status Transitions

```
           submit
             ↓
       [pending] ← approve
             ↓
        enqueue job
             ↓
      [generating]
       ↙    ↓    ↖
      /     |     \
    [done] [error] [waiting_quota]
      ↓              ↓
    review      (paused, auto-resume
      ↓          when key reset)
  approve_meta  ↓
      ↓      [generating] (continues)
  [finalized]   ↓
    ↓         [done]
  (archive)    ↓
           [finalized]

Regenerate flow:
  [done] → POST /regenerate → [regenerating] → [done] (revision+1)

Cancel flow:
  [generating/regenerating/waiting_quota] → POST /cancel → [cancelling] → [cancelled]

Retry (resume failed scenes):
  [error] → POST /retry → [generating] (from failed stage)
```

### Guard Rules

```
/approve:           pending only
/regenerate:        done, error, cancelled only (triggers regenerating)
/cancel:            generating, regenerating, waiting_quota (→ cancelling)
DELETE:             finalized, cancelled, error, waiting_quota only
PATCH /style-bible: any state except generating/regenerating
PATCH /scene/{n}:   done, error only (triggers regenerating from stage 3)
```

---

## Security

### Principles

- **Bind localhost only:** 127.0.0.1:8000 (no network exposure)
- **Job ID validation:** UUID v4, prevent path traversal
- **No API key logging:** Sanitize logs (show only last 4 chars)
- **Per-thread DB connection:** Prevent race conditions
- **CORS disabled** by default
- **Input validation:** Pydantic models for all requests

### Implementation

```python
# main.py
app = FastAPI()

@app.middleware("http")
async def enforce_localhost(request: Request, call_next):
    if request.client.host != "127.0.0.1":
        raise HTTPException(status_code=403, detail="Forbidden")
    return await call_next(request)

# job_id validation in routes
@app.get("/job/{job_id}")
async def get_job(job_id: str):
    try:
        uuid.UUID(job_id)  # Validate UUID format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    # ... proceed
```

---

## Deployment

### Project Structure

```
yt-narrator/
├── backend/
│   ├── main.py                 # FastAPI app
│   ├── config.py               # Config (read from .env)
│   ├── db.py                   # SQLite setup
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract interfaces
│   │   ├── gemini_llm.py       # LLM implementation
│   │   ├── hf_inference.py     # T2I implementation
│   │   ├── edge_tts.py         # TTS implementation
│   │   └── ken_burns.py        # Video implementation
│   ├── worker.py               # APScheduler orchestrator
│   ├── api_roller.py           # Multi-key rotation
│   ├── rate_limiter.py         # ProviderRateLimiter
│   └── models.py               # Pydantic schemas
├── frontend/
│   ├── index.html              # Web UI
│   ├── style.css
│   └── script.js
├── docs/
│   ├── DESIGN.md               # (this file)
│   └── README.md               # User guide
├── .env.example                # Template
├── .gitignore
├── requirements.txt            # Dependencies (pinned)
├── spike.py                    # Standalone test script
└── tests/
    ├── test_providers.py
    ├── test_api_roller.py
    └── test_e2e.py
```

### Setup & Deployment

**1. Prerequisites**

```bash
# OS: Windows 10/11, macOS, Linux
# Python: 3.10+
# FFmpeg: 4.4+
python --version
ffmpeg -version

# Create venv
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

**requirements.txt (pinned):**
```
fastapi==0.104.1
uvicorn==0.24.0
apscheduler==3.10.4
google-genai==0.3.1
huggingface-hub==0.19.3
edge-tts==6.1.10
pydantic==2.5.0
sqlite3  # built-in
```

**3. Environment setup**

```bash
# Create .env (from .env.example)
cp .env.example .env

# Fill in API keys
# HF_API_KEY_1=hf_xxxxx
# HF_API_KEY_2=hf_yyyyy
# ...
# GEMINI_API_KEY=xxx
```

**4. Initialize database**

```bash
python -c "from backend.db import init_db; init_db()"
```

**5. Run locally**

```bash
# Development (hot reload)
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Production
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**6. Access UI**

```
http://localhost:8000
```

---

## FAQ & Decisions

### Why Sequential Processing (max_workers=1)?

**Decision:** Process jobs one-at-a-time (no parallelism).

**Reasoning:**
- Multi-key quota tracking is simpler with sequential workload
- Reduces concurrent API calls (easier to stay within RPM limits)
- Simpler error recovery (don't need to track partial state of multiple jobs)
- User typically submits 1-3 videos per session

**Trade-off:** Jobs queue (users wait for their turn). Acceptable for MVP.

---

### Why HF Inference + FLUX.1 over alternatives?

**Decision:** HF Inference (free tier) + FLUX.1 Schnell (multi-key rotation).

**Reasoning:**
- **Cost:** $0 (gratis selamanya via multi-akun pool)
- **Quality:** FLUX.1 Schnell excellent (comparable to paid models)
- **Unlimited:** Rotate 5-10 keys → 1000 requests/day per key → 5000-10000 images/day total
- **Setup:** Simple API (InferenceClient)

**Alternatives considered:**
- Stable Diffusion 3 (free, but lower quality than FLUX.1)
- Replicate (limited free trial, not sustainable)
- Stability AI / OpenAI ($$, not aligned with budget)
- Local ComfyUI (requires GPU, user has no GPU)

---

### Why frame-accurate cumulative counting?

**Decision:** Frame count per scene = round(cum_end * fps) - round(cum_start * fps)

**Reasoning:**
- Per-scene rounding accumulates drift (1-2 ms × 20 scenes → 20-40 ms total)
- Cumulative rounding locks to video duration end-to-end
- Result: < 100 ms drift (acceptable for YouTube)

**Example:**
```
Scene 1: 45.2 sec → frame 0-1084
Scene 2: 38.5 sec → frame 1084-2008
Scene 3: 42.3 sec → frame 2008-3024
Total: 3024 frames = 126 sec @ 24fps
```

---

### Why Ken Burns (not I2V)?

**Decision:** Ken Burns zoompan motion (free, local FFmpeg).

**Reasoning:**
- **Cost:** $0 (FFmpeg built-in, no API calls)
- **Speed:** ~1-2 sec per scene (instant)
- **Quality:** Smooth 3-5 sub-clips per scene (simulate animation)
- **Control:** Deterministic, reproducible (no model variance)

**Alternatives considered:**
- HF I2V (limited, low quality on free tier)
- Runway Gen-3 / Pika ($$, not in budget)
- Local animation model (requires GPU, user has none)

**Limitation:** Not true "drawing animation" (Ken Burns is motion, not generation). Mitigated with:
- Good prompt engineering ("ink sketch" style in T2I)
- Sound effects (whoosh, scratch sounds in future)
- Narration voiceover (carries the story)

---

### Why edge-tts (not cloud TTS)?

**Decision:** edge-tts (free, no API key, Indonesian native).

**Reasoning:**
- **Cost:** $0 (free service, unofficial library)
- **Language:** Indonesian native voice (id-ID-GadisNeural)
- **Setup:** No auth required (just install package)
- **Quality:** Natural prosody for Indonesian speech

**Fallback:** If edge-tts service goes down, have Azure Speech / Google Cloud TTS available (documented in README, not default).

---

### Why Gemini LLM (not Claude/Groq)?

**Decision:** Gemini (config-driven, can swap to Claude/Groq later).

**Reasoning:**
- **Free tier:** 50 RPM, ~1000 requests/day sufficient for MVP
- **Quality:** Good for narration generation + scene breakdown
- **API:** Simple `google-genai` SDK (google.generativeai deprecated)
- **Flexibility:** Config-driven model selection (no hardcode)

**Fallback:** If quota hit, can fallback to Groq (free tier).

---

### Why 16:9 (not 9:16)?

**Decision:** 16:9 landscape (YouTube standard).

**Reasoning:**
- Target: YouTube long-form (5-15 min videos)
- 9:16 portrait for TikTok/Reels (future feature)

**Config:** `ASPECT_RATIO` in config.py (easy to change).

---

### Why SQLite (not PostgreSQL)?

**Decision:** SQLite (local, no server setup).

**Reasoning:**
- **Setup:** Zero infrastructure (file-based, no Docker/server)
- **Scope:** Single-machine workload (max 1 concurrent job)
- **Sufficient:** WAL mode + per-thread connection handles concurrency

**Limitation:** Not scalable to multi-machine setup (future consideration).

---

### Cost breakdown?

**Decision:** $0 total cost.

**Breakdown:**
```
LLM (Gemini):       $0 (free tier, ~50 RPM)
T2I (HF):           $0 (free tier, 5-10 multi-akun)
TTS (edge-tts):     $0 (free, unofficial)
Video (FFmpeg):     $0 (local)
────────────────────────
Total per video:    $0
Limit:              Unlimited (selama key ada)
```

---

### What if quota hit?

**Decision:** Wait + auto-resume (job paused in waiting_quota status).

**Flow:**
```
1. T2I provider returns 429 (quota)
2. APIRoller marks all keys exhausted
3. Job status → waiting_quota
4. Job.resume_at = next_reset_time (e.g., tomorrow)
5. Poller checks every 60s: if now >= resume_at → re-enqueue job
6. Job continues (transparent to user)
```

**User experience:** Job shows "paused, resuming tomorrow" → auto-resumes, user refreshes status.

---

### How to handle errors?

**Decision:** Fail gracefully, allow retry.

**Strategy:**
- Per-scene errors: Skip scene, mark failed, continue other scenes
- Job ends as "error" if any scene failed
- User can POST /retry to resume failed scenes
- Or POST /regenerate (full job) to start fresh (revision+1)

---

### Metadata generation?

**Decision:** Sekali saja (single LLM call), editable after.

**Flow:**
1. After compile (Stage 6), call LLM → title, description, hashtags
2. Job status → done (video ready + metadata in response)
3. User reviews metadata (GET /job/{id})
4. User approves → POST /job/{id}/approve-metadata → finalized
5. Or edits before approval (PATCH /job/{id} with updated metadata)

---

## Changelog (v5 → v6)

### Major Changes

1. **Provider model:** Switched from hardcoded Gemini Image to **HF Inference + FLUX.1** (free tier, multi-key)
2. **Architecture:** Added provider abstraction (LLMProvider, ImageProvider, TTSProvider, VideoProvider interfaces)
3. **Pipeline:** Refined to 8 stages (added metadata generation stage)
4. **API:** Added endpoint for style-bible editing + scene patching
5. **Database:** Updated schema (style_bible_json, resume_at, scene_clips table)
6. **Frame counting:** Clarified cumulative counting (not per-scene)
7. **Ken Burns:** Detailed zoompan patterns + variable names (`on`, `iw`, `ih`)
8. **Compilation:** Fixed FFmpeg recipe (concat video lossless, audio re-encode PCM)
9. **Error handling:** Per-scene failure + job retry logic
10. **Multi-key:** Detailed APIRoller implementation (round-robin + exhausted tracking)

### Removed (no longer in scope)

- Paid providers (Gemini Image, Stability, OpenAI)
- Local GPU models (ComfyUI)
- I2V (Image-to-Video) — Ken Burns is primary motion
- Multi-database support (SQLite only)
- Batch processing (sequential only)

### Kept (from v5)

- Revision tracking + re-generation
- Image caching by hash
- Startup recovery (enqueue pending jobs)
- Security (localhost bind, UUID job_id)
- Metadata generation (title, desc, hashtags)

---

## Next Steps

1. **Spike testing:** Run spike.py (test FLUX.1 + Ken Burns + compile real)
2. **Review spike results:** Audio-video drift, timing, errors
3. **Implementation phase:** Build MVP (API + worker + UI)
4. **Testing:** Unit tests (providers), integration tests (e2e)
5. **Deployment:** Local setup guide + .env template

---

**Document frozen.** Ready for implementation.
