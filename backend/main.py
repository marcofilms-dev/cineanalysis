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

app = FastAPI(title="Cinematiq API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TEMP_DIR = Path(tempfile.gettempdir()) / "cinematiq"
TEMP_DIR.mkdir(exist_ok=True)


class AnalyzeRequest(BaseModel):
    url: str


ANALYSIS_PROMPT = """You are an expert cinematographer, director of photography, and film analyst with 20+ years of experience across commercials, features, and music videos.

Analyze these frames extracted from a video and return a complete, highly detailed filmmaker's breakdown.

Be extremely specific and technical. Reference real-world techniques, equipment, and industry terminology.
For gear estimates, give realistic options (not just "professional camera").
For color grade, describe it as if briefing a colorist.
For lighting, describe it as if briefing a gaffer.

Return ONLY a valid JSON object with NO additional text, matching exactly this structure:

{
  "overview": {
    "style": "one-sentence style description",
    "genre": "commercial / narrative / documentary / music video / etc",
    "mood": "emotional quality of the footage",
    "influences": ["director or film influences you can identify"],
    "production_level": "indie / mid-budget / high-end commercial / studio"
  },
  "cinematography": {
    "dominant_shot_types": ["list of shot types used: ECU, CU, MCU, MS, MWS, WS, EWS"],
    "camera_movement": ["specific movements: static lockoff, handheld, gimbal, dolly, slider, crane, drone"],
    "movement_feel": "description of how movement feels — intimate, clinical, kinetic, etc",
    "estimated_focal_lengths": ["35mm", "50mm", etc],
    "depth_of_field": "deep / shallow / mixed — with notes",
    "lens_characteristics": "clinical sharp / vintage rendering / anamorphic / spherical / etc",
    "framing_notes": "how subjects are framed, headroom, negative space usage",
    "aspect_ratio": "estimated aspect ratio"
  },
  "lighting": {
    "style": "high-key / low-key / naturalistic / dramatic / flat",
    "quality": "hard / soft / diffused / mixed",
    "key_direction": "side / front / back / top / motivated",
    "color_temperature": "warm / cool / mixed / specific kelvin estimate",
    "natural_vs_artificial": "natural / artificial / mixed — with notes",
    "estimated_setup": "description of likely light setup — e.g. large softbox camera left, practicals for fill",
    "shadows": "shadow quality and treatment",
    "highlights": "highlight roll-off character"
  },
  "color": {
    "grade_style": "name or description of the grade approach",
    "primary_palette": ["3-5 dominant hex color values"],
    "shadows_treatment": "how shadows are handled — lifted, crushed, color cast",
    "midtones_treatment": "midtone character",
    "highlights_treatment": "highlight roll-off and treatment",
    "skin_tone_handling": "how skin is treated in the grade",
    "saturation_style": "desaturated / vivid / selective / natural",
    "contrast_style": "flat / punchy / filmic S-curve / etc",
    "lut_description": "if you were to describe this as a LUT preset style",
    "colorist_brief": "one paragraph brief you would give a colorist to recreate this look"
  },
  "editing": {
    "avg_shot_length_estimate": "X-Y seconds",
    "pacing": "slow / medium / fast / very fast",
    "pacing_description": "how the pacing serves the content",
    "transition_types": ["cut / dissolve / match cut / etc"],
    "rhythm_notes": "how edits relate to audio or music if present",
    "structural_notes": "how the piece is structured narratively"
  },
  "gear_estimate": {
    "camera_tier": "cinema camera / mirrorless hybrid / broadcast / DSLR",
    "likely_cameras": ["specific camera models in order of likelihood"],
    "likely_lenses": ["specific lens sets or series"],
    "stabilization": "tripod / gimbal / handheld / steadicam / drone",
    "likely_accessories": ["follow focus / matte box / monitor / etc"]
  },
  "techniques": [
    "specific named techniques visible — e.g. rack focus, motivated camera movement, available light, etc"
  ],
  "recreate_this_look": {
    "camera_settings": "ISO range, aperture range, shutter angle/speed recommendations",
    "lighting_setup": "step by step how to approximate this lighting",
    "color_grade_steps": [
      "Step 1: ...",
      "Step 2: ...",
      "Step 3: ..."
    ],
    "key_techniques": ["the most important things to nail to get this look"],
    "difficulty": "beginner / intermediate / advanced / professional"
  }
}"""


async def download_and_extract_frames(url: str, job_id: str) -> list[str]:
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    video_path = job_dir / "video.mp4"

    # Download video with yt-dlp
    download_cmd = [
        "yt-dlp",
        "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
        "--merge-output-format", "mp4",
        "--output", str(video_path),
        "--no-playlist",
        "--max-filesize", "200m",
        url
    ]

    proc = await asyncio.create_subprocess_exec(
        *download_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Failed to download video: {stderr.decode()}")

    # Find the actual downloaded file (yt-dlp may rename it)
    mp4_files = list(job_dir.glob("*.mp4"))
    if not mp4_files:
        # Try any video file
        all_files = list(job_dir.iterdir())
        video_files = [f for f in all_files if f.suffix in ['.mp4', '.mkv', '.webm', '.mov']]
        if not video_files:
            raise HTTPException(status_code=500, detail="No video file found after download")
        video_path = video_files[0]
    else:
        video_path = mp4_files[0]

    frames_dir = job_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    # Extract frames using scene change detection
    # select frames where scene change score > 0.25, max 20 frames
    extract_cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", "select='gt(scene,0.25)',scale=1280:-1",
        "-vsync", "vfr",
        "-frames:v", "20",
        "-q:v", "2",
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

    # If scene detection gave us too few frames, fall back to interval sampling
    if len(frames) < 6:
        for f in frames:
            f.unlink()

        fallback_cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vf", "fps=1/4,scale=1280:-1",
            "-frames:v", "16",
            "-q:v", "2",
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

    # Cap at 16 frames to stay within API limits
    return [str(f) for f in frames[:16]]


def frames_to_base64(frame_paths: list[str]) -> list[dict]:
    images = []
    for path in frame_paths:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
            images.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": data
                }
            })
    return images


async def analyze_frames(frame_paths: list[str], url: str) -> dict:
    image_blocks = frames_to_base64(frame_paths)

    content = image_blocks + [
        {
            "type": "text",
            "text": f"Source URL: {url}\nNumber of frames provided: {len(frame_paths)}\n\n{ANALYSIS_PROMPT}"
        }
    ]

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
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
    else:
        return "Video"


@app.post("/analyze")
async def analyze_video(request: AnalyzeRequest):
    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id

    try:
        frame_paths = await download_and_extract_frames(request.url, job_id)

        if not frame_paths:
            raise HTTPException(status_code=500, detail="Could not extract frames from video")

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
        # Clean up temp files
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/health")
async def health():
    return {"status": "ok"}
