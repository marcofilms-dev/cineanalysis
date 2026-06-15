# Cinematiq — AI Cinematography Analyzer

Paste any YouTube, Instagram, Vimeo, or TikTok link. Get a complete filmmaker's breakdown powered by Claude Vision.

---

## What It Does

- Downloads the video via yt-dlp
- Extracts key frames using scene-change detection (ffmpeg)
- Sends frames to Claude Vision for analysis
- Returns: cinematography, lighting, color grade, gear estimate, editing rhythm, and how to recreate the look

---

## Prerequisites

You already have these from your download tools:
- `yt-dlp` installed and in PATH
- `ffmpeg` installed and in PATH
- Python 3.10+
- Anthropic API key

---

## Setup

### Backend

```bash
cd backend
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
chmod +x start.sh
./start.sh
```

Backend runs on: http://localhost:8000

### Frontend

No build step needed. Just open:

```
frontend/index.html
```

In a browser. Or serve it:

```bash
cd frontend
python3 -m http.server 3000
# Open http://localhost:3000
```

---

## Project Structure

```
cinematiq/
├── backend/
│   ├── main.py          # FastAPI app — download, extract, analyze
│   ├── requirements.txt
│   ├── start.sh
│   └── .env.example
└── frontend/
    └── index.html       # Complete UI, no framework needed
```

---

## How It Works

```
User pastes URL
      ↓
FastAPI receives request
      ↓
yt-dlp downloads video (max 720p, max 200MB)
      ↓
ffmpeg extracts frames via scene-change detection
Falls back to interval sampling if < 6 scenes detected
Max 16 frames sent to Claude
      ↓
Claude Vision analyzes all frames simultaneously
Returns structured JSON breakdown
      ↓
Frontend renders the full breakdown
Temp files are deleted after each analysis
```

---

## Extending This

- Add Supabase to save analyses and build history
- Add PDF export of the breakdown
- Add side-by-side comparison mode
- Add color palette extraction as downloadable swatches
- Build a Next.js frontend for production deployment
- Deploy backend to Railway or Render
