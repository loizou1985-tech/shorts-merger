from flask import Flask, request, jsonify
import subprocess
import os
import requests
import tempfile
import traceback

app = Flask(__name__)

MAX_SIZE_MB = 20
REQUEST_TIMEOUT = 120
OUTPUT_DURATION = 10


def download_file(url, suffix):
    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()

        content_length = int(r.headers.get("content-length", 0))
        if content_length and content_length > MAX_SIZE_MB * 1024 * 1024:
            raise Exception(f"File too large: {round(content_length / (1024 * 1024), 2)} MB")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        written = 0
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
                    written += len(chunk)
        finally:
            tmp.close()

    if written == 0:
        raise Exception("Downloaded file is empty or invalid URL")

    return tmp.name


@app.route("/merge", methods=["POST"])
def merge():
    video_path = None
    music_path = None
    out_path = None

    try:
        data = request.get_json(force=True) or {}

        video_url = data.get("video_url")
        music_url = data.get("music_url")

        if not video_url or not music_url:
            return jsonify({"error": "video_url and music_url are required"}), 400

        video_path = download_file(video_url, ".mp4")
        music_path = download_file(music_url, ".mp3")

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return jsonify({"error": "Video file is empty"}), 400

        if not os.path.exists(music_path) or os.path.getsize(music_path) == 0:
            return jsonify({"error": "Music file is empty"}), 400

        out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", video_path,
            "-i", music_path,
            "-map", "0:v",
            "-map", "1:a",
            "-t", str(OUTPUT_DURATION),
            "-vf", "scale=540:960:force_original_aspect_ratio=increase,crop=540:960,fps=30,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-profile:v", "high",
            "-level", "4.0",
            "-g", "15",
            "-keyint_min", "15",
            "-sc_threshold", "0",
            "-bf", "2",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "48000",
            "-ac", "2",
            out_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            return jsonify({
                "error": "ffmpeg failed",
                "detail": result.stderr[-4000:]
            }), 500

        if not os.path.exists(out_path) or os.path.getsize(out_path) < 100000:
            return jsonify({"error": "Generated video is invalid"}), 500

        with open(out_path, "rb") as f:
            upload = requests.post(
                "https://store1.gofile.io/uploadFile",
                files={"file": ("short.mp4", f, "video/mp4")},
                timeout=REQUEST_TIMEOUT
            )

        upload_data = upload.json()

        if upload_data.get("status") == "ok":
            return jsonify({
                "downloadPage": upload_data["data"]["downloadPage"]
            })

        return jsonify({
            "error": "Upload failed",
            "detail": upload_data
        }), 500

    except requests.RequestException as e:
        return jsonify({"error": "Download/upload request failed", "detail": str(e)}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ffmpeg timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500
    finally:
        for p in [video_path, music_path, out_path]:
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
