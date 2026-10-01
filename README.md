# YT Narrator — Auto YouTube Videos

Auto-generate long-form YouTube ink-explainer videos from topic title → narration (LLM) → scenes (LLM breakdown) → images (HF FLUX.1) → TTS audio → Ken Burns motion → compiled video.

**Status:** MVP complete (86%, Task 7 final commit pending)

---

## Features

✅ **LLM-driven narration** — Google Gemini generates Indonesian narration + scene breakdown  
✅ **Text-to-image** — HF Inference FLUX.1 Schnell (free tier, $0 cost)  
✅ **Text-to-speech** — edge-tts (free, Indonesian voice)  
✅ **Ken Burns motion** — Frame-accurate 3-composition zoompan per scene  
✅ **Auto-compile** — H.264 + AAC, 1920x1080, 24fps  
✅ **FastAPI backend** — Job queue, SQLite persistence, rate limiting  
✅ **Web UI** — Submit, monitor, download videos  

---

## Quick Start

### 1. Install deps

```bash
cd yt-narrator
pip install -r requirements.txt
```

### 2. Set the Gemini key

```bash
echo "GEMINI_API_KEY=your-key" > .env
```

HuggingFace keys are **not** set here. Add them in the UI (see below) so you
can turn individual keys off without restarting.

### 3. Start the server

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 4. Open http://127.0.0.1:8000

Paste one HF key per line in the **Kunci API** panel and click **Simpan kunci**.
Keys rotate one at a time; disable one from the list if it runs out. Stored in
`data/keys.json`, which is gitignored. The browser only ever sees the last four
characters.

### 5. Generate

Type a topic, click **Buat videonya**, watch the stage advance, download the MP4.

---

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Web UI |
| GET | `/keys` | List keys (redacted) |
| PUT | `/keys` | Replace the whole pool, one token per line |
| PATCH | `/keys/{index}` | Enable/disable one key |
| DELETE | `/keys/{index}` | Remove one key |
| POST | `/job/submit` | Create new job |
| GET | `/job/{id}` | Job status |
| GET | `/job/{id}/video` | Download video |
| POST | `/job/{id}/approve` | Enqueue a submitted job |
| POST | `/job/{id}/cancel` | Cancel |
| POST | `/job/{id}/regenerate` | New revision |
| PATCH | `/job/{id}/style-bible` | Edit style bible |
| GET | `/jobs` | Recent jobs |
| DELETE | `/job/{id}` | Delete finished job |

---

## Architecture

```
backend/
├── main.py              # FastAPI routes
├── models.py            # Pydantic schemas
├── db.py                # SQLite (7 tables)
├── config.py            # Env config
├── worker.py            # 8-stage pipeline
├── scheduler.py         # APScheduler queue
├── keystore.py          # HF key pool (data/keys.json, reloaded per call)
├── rate_limiter.py      # Quota + backoff
└── providers/
    ├── base.py          # Abstract interfaces
    ├── gemini_llm.py    # LLM narration
    ├── hf_inference.py  # FLUX.1 images
    ├── edge_tts.py      # TTS audio
    └── ken_burns.py     # Ken Burns motion

frontend/
├── index.html           # Whole UI, CSS and JS inline
├── mockup.html          # Standalone design mockup (no backend)
```

---

## Pipeline (8 stages)

1. **Segmentation** — LLM breaks topic into 2-3 scenes
2. **Style Bible** — Generate visual aesthetic guide
3. **LLM Narration** — Generate per-scene narration + t2i prompts
4. **TTS Audio** — Convert narration to WAV (measured duration)
5. **Generate Images** — FLUX.1 → PNG per scene
6. **Ken Burns Motion** — 3 zoompan compositions per image
7. **Compile Video** — Mux video + audio, H.264 + AAC
8. **Metadata** — Generate title, description, hashtags

---

## Verified (Spike Test)

✅ HF FLUX.1: 3 images (3.7 MB)  
✅ edge-TTS: 3 WAV files (5.88s, 6.74s, 6.48s)  
✅ Ken Burns: 9 clips (3×3 compositions, frame-accurate)  
✅ Compile: 2.8 MB video (1920x1080, 24fps)  
✅ **Drift: 20.7 ms (< 100ms PASS)**  
✅ **Cost: $0.00 (gratis)**

---

## Cost

| Component | Cost |
|-----------|------|
| HF FLUX.1 (free tier) | $0/month |
| edge-tts (free) | $0/month |
| FFmpeg (free) | $0/month |
| **Total** | **$0/month** |

---

## Rate Limits

- Gemini LLM: ~50 RPM (free tier)
- HF FLUX.1: ~1000 req/day per akun (multi-akun pool rotates)
- Backoff: 429/503 → waiting_quota status → auto-retry

---

## Testing

```bash
pytest tests/ -v
```

Results:
- test_db.py: 3/3 pass
- test_providers.py: 8/8 pass
- test_keystore.py: 18/18 pass
- test_keys_api.py: 9/9 pass
- test_e2e.py: 9/9 pass
- 47 total

`test_e2e.py` is offline: it checks wiring (app boots, key routes, submit
lands in the DB, guards reject bad input). The full pipeline needs live API
keys and lives in `spike.py`.

---

## Known Limits

- Max parallel jobs: 1 (APScheduler max_workers=1)
- Output resolution: 1920x1080 (min 1280x720)
- Narration language: Indonesian (TTS) + English (T2I prompts)
- Ken Burns duration: Audio duration (measured via ffprobe)

---

## Next Steps

1. Add GEMINI_API_KEY + HF_API_KEY env vars
2. Run `uvicorn backend.main:app`
3. Submit topic via web UI
4. Monitor job status → Download video when done

---

## Git Commits

| Task | Commit | Status |
|------|--------|--------|
| 1. Database | 9dcab61 | ✅ |
| 2. Providers | ... | ✅ |
| 3. APIRoller | ... | ✅ |
| 4. Orchestrator | ... | ✅ |
| 5. FastAPI | ... | ✅ |
| 6. Frontend | ... | ✅ |
| 7. Tests | (pending) | ⏳ |

---

**Built with:** FastAPI, AsyncIO, SQLite, HF Inference, Google Gemini, edge-tts, FFmpeg

**Author:** YT Narrator MVP  
**Date:** 2026-10-01
