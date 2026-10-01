## YT Narrator Desktop — Setup Guide

### What You Need

1. **YT-Narrator.exe** (44 MB, standalone)
2. **Backend server** running (separate process)
3. **API keys:**
   - 1× Google Gemini (free tier)
   - 1-50× HuggingFace (free tier, multi-key rotation)

---

### Step 1: Download Files

- `dist/YT-Narrator.exe` — Desktop app
- `backend/` folder — API backend

---

### Step 2: Start Backend Server

**Terminal 1** (keep running):
```bash
cd path\to\yt-narrator
set GEMINI_API_KEY=your-gemini-key
set HF_API_KEY_1=your-first-hf-key
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Wait for: `Uvicorn running on http://127.0.0.1:8000`

---

### Step 3: Launch Desktop App

**Double-click:** `YT-Narrator.exe`

(First launch may take 10-15 seconds)

---

### Step 4: Add API Keys

1. Click **Settings** tab
2. Paste **Gemini API Key**
3. Paste **HF API Keys** (one per line, up to 50)
   ```
   hf_key1
   hf_key2
   hf_key3
   ...
   ```
4. Click **Save Settings**

✓ Config saved to: `C:\Users\<username>\.yt-narrator\config.json`

---

### Step 5: Submit Job

1. Click **Submit Job** tab
2. Enter topic (e.g., "History of Ancient Egypt")
3. Click **Submit Job**
4. Copy **Job ID** from popup

---

### Step 6: Monitor Progress

1. Click **Monitor** tab
2. Paste **Job ID**
3. Click **Monitor**
4. Status updates every 2 seconds

Status stages:
- `pending` → queued
- `generating` → processing
- `done` → ready to download
- `error` → failed (check backend logs)

---

### Step 7: Download Video

When status = `done`:
1. Click **Download Video**
2. MP4 saved to: `C:\Users\<username>\Downloads\<job-id>.mp4`

---

### Troubleshooting

| Problem | Solution |
|---------|----------|
| "Connection refused" | Backend server not running (Step 2) |
| "Settings not saved" | Check `~\.yt-narrator\config.json` exists |
| "Job stuck on pending" | Check HF/Gemini quota limits |
| "Audio-video out of sync" | Retry job (Ken Burns sometimes needs re-encode) |
| ".exe won't start" | Windows Defender may block; allow in firewall |

---

### Advanced: Multi-Key Setup

Add up to 50 HF keys for unlimited quota:

**Settings tab:**
```
hf_Ak1...
hf_Ak2...
hf_Ak3...
...
hf_Ak50...
```

Backend rotates keys automatically when quota exhausted.

---

### Backend Requirements

- Python 3.11+
- FFmpeg (add to PATH or install: `choco install ffmpeg`)
- ~500 MB disk (for cache + output videos)

---

### Cost

| Component | Price |
|-----------|-------|
| HF FLUX.1 (free tier) | $0 |
| Google Gemini (free tier) | $0 |
| edge-tts | $0 |
| FFmpeg | $0 |
| **Total** | **$0/month** |

---

**Ready? Gas! 🚀**
