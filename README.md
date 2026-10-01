# YT Narrator

Auto-generate long-form YouTube ink-explainer videos from a topic: narration
(Gemini) → per-scene images (FLUX.1) → Indonesian TTS (edge-tts) → Ken Burns
motion → compiled 1920x1080 MP4 with title, description and hashtags.

Runs at **$0/month**: free Gemini tier, free HuggingFace tier, free edge-tts,
local FFmpeg.

![stage](https://img.shields.io/badge/pipeline-8%20stages-4a3728) ![tests](https://img.shields.io/badge/tests-69%20passing-2c7a4f)

---

## What you need first

| Thing | Why | Where |
|---|---|---|
| Python 3.11+ | runtime | python.org |
| FFmpeg **on PATH** | audio/video assembly | [ffmpeg.org/download](https://ffmpeg.org/download.html) |
| Gemini API key | narration, scenes, metadata | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free) |
| ≥1 HuggingFace key | images | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (free) |

FFmpeg is the one that bites people. Check with `ffmpeg -version`; if that
command is not found, install it before going further.

---

## Setup

```bash
git clone https://github.com/Chukie99/yt-narrator.git
cd yt-narrator

python -m venv .venv
```

**Windows**
```bash
.venv\Scripts\activate
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set the Gemini key:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Then edit `.env`:
```
GEMINI_API_KEY=paste_your_key_here
```

Start the server:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

---

## Your first video

1. **Add HF keys** — in the **Kunci API** panel, paste one key per line, click
   **Simpan kunci**. Add as many as you like; they rotate automatically and
   take effect immediately, no restart.
2. **Type a topic** and click **Buat videonya**.
3. **Watch the stages advance**: LLM → style bible → TTS → images → motion →
   compile → metadata.
4. **Download the MP4** when it finishes.

A 9-scene video takes roughly 5–10 minutes, most of it waiting on image
generation.

### Adding more keys later

Paste the full list again (including the old ones) and save. To take one key
out of rotation without deleting it, toggle it off in the list. A key that hits
`402` (out of credits) or `429` (rate limited) is skipped automatically and
the next key is tried.

---

## Pipeline

| # | Stage | What happens |
|---|-------|--------------|
| 1 | LLM narration | Gemini writes Indonesian narration, split into 6–12 scenes with English image prompts |
| 2 | Style bible | One visual style guide every image prompt follows |
| 3 | TTS | edge-tts renders each scene to WAV, duration measured with ffprobe |
| 4 | Images | FLUX.1 Schnell, 1920x1080, cached by prompt hash |
| 5 | Ken Burns | 3 zoompan compositions per scene, frame-accurate |
| 6 | Compile | H.264 + AAC, 1920x1080, 24fps |
| 7 | Metadata | Title, description, hashtags in Indonesian |

---

## Testing

```bash
pytest tests/ -q
```

69 tests, no API keys needed — they stub the LLM and exercise everything
downstream for real (TTS, images, motion, compile), so a regression in
duration math or file naming still gets caught.

To also hit the live APIs (spends real Gemini tokens and HF credits):

```bash
pytest tests/test_full_live.py -v
```

---

## Troubleshooting

**"ffmpeg not found"** — install FFmpeg and make sure it is on PATH.

**Every image call returns 402** — the HuggingFace account is out of included
credits. This is account-level, not per-key; add credits or use another
account's key.

**The job sits on one stage for minutes** — image generation is the slow part
and each call can take 30s+.

**Job status shows `error`** — the reason is in the UI and in the `error_msg`
column of `data/narrator.db`.

**Port 8000 already in use** — `--port 8001`.

---

## Notes on the free tiers

Both providers are rate limited and the limits move. The Gemini provider
retries with backoff and falls back through a list of live models, because
individual Flash models answer `503` intermittently. The image provider pins
`fal-ai` for FLUX.1 because the router's default (`nscale`) requires
pre-paid credits.

If a provider retires a model, edit `LLM_MODEL` in `.env` or the
`FALLBACK_MODELS` list in `backend/providers/gemini_llm.py`.

---

## API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Web UI |
| GET | `/keys` | List keys (redacted) |
| PUT | `/keys` | Replace the pool, one token per line |
| PATCH | `/keys/{index}` | Enable/disable one key |
| DELETE | `/keys/{index}` | Remove one key |
| POST | `/job/submit` | Create a job |
| POST | `/job/{id}/approve` | Enqueue a submitted job |
| GET | `/job/{id}` | Job status |
| GET | `/job/{id}/video` | Download the MP4 |
| POST | `/job/{id}/cancel` | Cancel |
| GET | `/jobs` | Recent jobs |

---

## Security

- Binds to `127.0.0.1` only; non-loopback requests are rejected.
- Tokens are never sent to the browser — the UI only receives the last four
  characters.
- `.env`, `data/keys.json` and everything under `data/` are gitignored.

---

## Layout

```
backend/
├── main.py          # FastAPI routes
├── worker.py        # the 8-stage pipeline
├── db.py            # SQLite, 7 tables
├── keystore.py      # HF key pool, re-read per call
├── config.py        # env config
└── providers/       # gemini, hf_inference, edge_tts, ken_burns
frontend/
├── index.html       # the whole UI
└── mockup.html      # design mockup, no backend needed
tests/
└── test_full_live.py  # opt-in, hits the real APIs
```

---

**Cost:** $0/month. **Stack:** FastAPI, SQLite, APScheduler, Google Gemini,
HuggingFace Inference, edge-tts, FFmpeg.