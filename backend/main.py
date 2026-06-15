import os
import uuid
import json
import base64
import asyncio
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="CineAnalysis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TEMP_DIR = Path(tempfile.gettempdir()) / "cineanalysis"
TEMP_DIR.mkdir(exist_ok=True)


class AnalyzeRequest(BaseModel):
    url: str


ANALYSIS_PROMPT = """You are an expert cinematographer and director of photography analyzing video frames.

Analyze these frames and return a complete filmmaker's breakdown as valid JSON only, no other text:

{
  "overview": {
    "style": "",
    "genre": "",
    "mood": "",
    "influences": [],
    "production_level": ""
  },
  "cinematography": {
    "dominant_shot_types": [],
    "camera_movement": [],
    "movement_feel": "",
    "estimated_focal_lengths": [],
    "depth_of_field": "",
    "lens_characteristics": "",
    "framing_notes": "",
    "aspect_ratio": ""
  },
  "lighting": {
    "style": "",
    "quality": "",
    "key_direction": "",
    "color_temperature": "",
    "natural_vs_artificial": "",
    "estimated_setup": "",
    "shadows": "",
    "highlights": ""
  },
  "color": {
    "grade_style": "",
    "primary_palette": [],
    "shadows_treatment": "",
    "midtones_treatment": "",
    "highlights_treatment": "",
    "skin_tone_handling": "",
    "saturation_style": "",
    "contrast_style": "",
    "lut_description": "",
    "colorist_brief": ""
  },
  "editing": {
    "avg_shot_length_estimate": "",
    "pacing": "",
    "pacing_description": "",
    "transition_types": [],
    "rhythm_notes": "",
    "structural_notes": ""
  },
  "gear_estimate": {
    "camera_tier": "",
    "likely_cameras": [],
    "likely_lenses": [],
    "stabilization": "",
    "likely_accessories": []
  },
  "techniques": [],
  "recreate_this_look": {
    "camera_settings": "",
    "lighting_setup": "",
    "color_grade_steps": [],
    "key_techniques": [],
    "difficulty": ""
  }
}"""


async def download_and_extract_frames(url: str, job_id: str) -> list[str]:
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    video_path = job_dir / "video.mp4"

    download_cmd = [
        "yt-dlp",
        "--format", "best[height<=720][ext=mp4]/best[height<=720]/best",
        "--output", str(video_path),
        "--no-playlist",
        "--max-filesize", "100m",
        url
    ]

    proc = await asyncio.create_subprocess_exec(
        *download_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Could not download video: {stderr.decode()[:500]}")

    video_files = list(job_dir.glob("video.*"))
    if not video_files:
        video_files = [f for f in job_dir.iterdir() if f.suffix in ['.mp4', '.mkv', '.webm', '.mov']]
    if not video_files:
        raise HTTPException(status_code=500, detail="No video file found after download")

    video_path = video_files[0]
    frames_dir = job_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    extract_cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "select='gt(scene,0.25)',scale=1280:-1",
        "-vsync", "vfr",
        "-frames:v", "16",
        "-q:v", "3",
        str(frames_dir / "frame_%04d.jpg"),
        "-y"
    ]

    proc = await asyncio.create_subprocess_exec(
        *extract_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()

    frames = sorted(frames_dir.glob("*.jpg"))

    if len(frames) < 4:
        for f in frames:
            f.unlink()
        fallback_cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "fps=1/5,scale=1280:-1",
            "-frames:v", "12",
            "-q:v", "3",
            str(frames_dir / "frame_%04d.jpg"),
            "-y"
        ]
        proc = await asyncio.create_subprocess_exec(
            *fallback_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        frames = sorted(frames_dir.glob("*.jpg"))

    return [str(f) for f in frames[:16]]


def frames_to_base64(frame_paths: list[str]) -> list[dict]:
    images = []
    for path in frame_paths:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
            images.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data}
            })
    return images


async def analyze_frames(frame_paths: list[str], url: str) -> dict:
    image_blocks = frames_to_base64(frame_paths)
    content = image_blocks + [{"type": "text", "text": f"Source: {url}\nFrames: {len(frame_paths)}\n\n{ANALYSIS_PROMPT}"}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)


def get_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "instagram.com" in url:
        return "Instagram"
    elif "vimeo.com" in url:
        return "Vimeo"
    elif "tiktok.com" in url:
        return "TikTok"
    return "Video"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze")
async def analyze_video(request: AnalyzeRequest):
    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    try:
        frame_paths = await download_and_extract_frames(request.url, job_id)
        if not frame_paths:
            raise HTTPException(status_code=500, detail="Could not extract frames")
        analysis = await analyze_frames(frame_paths, request.url)
        return {
            "success": True,
            "job_id": job_id,
            "platform": get_platform(request.url),
            "frame_count": len(frame_paths),
            "analysis": analysis
        }
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"AI returned invalid JSON: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
