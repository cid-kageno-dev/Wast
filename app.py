from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
MAX_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024


def have_tools():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def ffprobe(path: Path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30
    )
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "Could not inspect this video.")
    return json.loads(p.stdout)


def bytes_label(n):
    if n is None:
        return "Unknown"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1000
    return str(n)


def video_report(path: Path):
    data = ffprobe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = data.get("format", {})
    try:
        num, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").split("/", 1)
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    bitrate = int(fmt.get("bit_rate") or 0)
    if not bitrate and duration and fmt.get("size"):
        bitrate = int(int(fmt["size"]) * 8 / duration)
    w, h = video.get("width"), video.get("height")
    return {
        "filename": path.name,
        "size": bytes_label(int(fmt.get("size") or path.stat().st_size)),
        "duration_seconds": round(duration, 3),
        "duration": f"{int(duration // 60):02}:{duration % 60:05.2f}",
        "width": w, "height": h, "fps": round(fps, 3),
        "video_codec": video.get("codec_name", "Unknown"),
        "audio_codecs": [s.get("codec_name", "Unknown") for s in audio],
        "bitrate": f"{bitrate / 1_000_000:.2f} Mbps" if bitrate else "Unknown",
        "is_4k": bool(w and h and w >= 3840 and h >= 2160),
        "is_60fps": fps >= 59.5,
        "streams": len(streams),
    }


def process_video(src: Path, mode: str, codec: str, crf: int, preset: str, audio_mode: str, out: Path):
    if mode == "remux":
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(src),
               "-map", "0", "-c", "copy", "-movflags", "+faststart", str(out)]
    else:
        encoder = "libx264" if codec == "h264" else "libx265"
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(src),
               "-map", "0:v:0", "-map", "0:a?", "-c:v", encoder,
               "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
               "-movflags", "+faststart"]
        if audio_mode == "copy":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-map_metadata", "0", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60)
    if p.returncode:
        out.unlink(missing_ok=True)
        raise RuntimeError((p.stderr or "FFmpeg failed")[-1800:])
    info = ffprobe(out)
    if not info.get("streams"):
        out.unlink(missing_ok=True)
        raise RuntimeError("Output did not contain readable streams.")
    return video_report(out)


@app.get("/")
def index():
    return render_template("index.html", max_mb=MAX_MB)


@app.get("/health")
def health():
    return jsonify({"ok": True, "ffmpeg_available": shutil.which("ffmpeg") is not None,
                    "ffprobe_available": shutil.which("ffprobe") is not None})


@app.post("/api/analyze")
def analyze():
    if not have_tools():
        return jsonify(error="FFmpeg and ffprobe must be installed on the server."), 500
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="Choose a video file first."), 400
    original_name = secure_filename(file.filename) or "video.mp4"
    if Path(original_name).suffix.lower() not in ALLOWED:
        return jsonify(error="Unsupported file type. Use MP4, MOV, M4V, MKV, WEBM or AVI."), 400
    token = uuid.uuid4().hex
    src = UPLOAD_DIR / f"{token}_{original_name}"
    try:
        file.save(src)
        report = video_report(src)
        return jsonify(report=report)
    except Exception as exc:
        return jsonify(error=str(exc)), 400
    finally:
        src.unlink(missing_ok=True)


@app.post("/api/process")
def process():
    if not have_tools():
        return jsonify(error="FFmpeg and ffprobe must be installed on the server."), 500
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="Choose a video file first."), 400
    original_name = secure_filename(file.filename) or "video.mp4"
    if Path(original_name).suffix.lower() not in ALLOWED:
        return jsonify(error="Unsupported file type."), 400
    mode = request.form.get("mode", "remux")
    codec = request.form.get("codec", "h264")
    preset = request.form.get("preset", "veryfast")
    audio_mode = request.form.get("audio", "aac")
    try:
        crf = int(request.form.get("crf", "18"))
    except ValueError:
        return jsonify(error="CRF must be an integer."), 400
    if mode not in {"remux", "optimize"} or codec not in {"h264", "h265"}:
        return jsonify(error="Invalid processing option."), 400
    if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}:
        return jsonify(error="Invalid encoding preset."), 400
    if audio_mode not in {"aac", "copy"} or not 0 <= crf <= 51:
        return jsonify(error="Invalid audio mode or CRF (0–51)."), 400

    token = uuid.uuid4().hex
    src = UPLOAD_DIR / f"{token}_{original_name}"
    out = OUTPUT_DIR / f"{token}_{Path(original_name).stem}_{mode}.mp4"
    try:
        file.save(src)
        before = video_report(src)
        after = process_video(src, mode, codec, crf, preset, audio_mode, out)
        return jsonify(
            download_url=f"/download/{out.name}",
            input=before,
            output=after,
            note="This processes the local upload on the server. TikTok may still transcode it after upload."
        )
    except subprocess.TimeoutExpired:
        out.unlink(missing_ok=True)
        return jsonify(error="Processing timed out. Try a shorter or smaller video."), 408
    except Exception as exc:
        out.unlink(missing_ok=True)
        return jsonify(error=str(exc)), 400
    finally:
        src.unlink(missing_ok=True)


@app.get("/download/<name>")
def download(name):
    # Only serve a generated filename directly inside OUTPUT_DIR.
    safe_name = Path(name).name
    path = OUTPUT_DIR / safe_name
    if not path.is_file():
        return jsonify(error="Output not found or already removed."), 404
    return send_file(path, as_attachment=True, download_name=safe_name)


if __name__ == "__main__":
    if not have_tools():
        print("WARNING: install FFmpeg: pkg install ffmpeg", flush=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
