from flask import Flask, request, jsonify
import subprocess
import os
import uuid
import requests
import tempfile
import traceback

app = Flask(__name__)

MAX_SIZE_MB = 20
REQUEST_TIMEOUT = 120
OUTPUT_DURATION = 12


def download_file(url, suffix):
    print(f"Downloading: {url}", flush=True)

    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()

        total = int(r.headers.get("content-length", 0))
        print(f"Content-Length: {total}", flush=True)

        if total and total > MAX_SIZE_MB * 1024 * 1024:
            raise Exception(f"File too large: {round(total / (1024 * 1024), 2)} MB")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
        finally:
            tmp.close()

    print(f"Saved to: {tmp.name}", flush=True)
    return tmp.name


@app.route("/merge", methods=["POST"])
def merge():
    video_path = None
    music_path = None
    voice_path = None
    out_path = None

    try:
        data = request.get_json(force=True) or {}
        print(f"Incoming payload: {data}", flush=True)

        video_url = data.get("video_url")
        music_url = data.get("music_url")
        voice_url = data.get("voice_url")

        if not video_url or not music_url:
            return jsonify({"error": "video_url and music_url are required"}), 400

        video_path = download_file(video_url, ".mp4")
        music_path = download_file(music_url, ".mp3")
        out_path = f"/tmp/{uuid.uuid4()}.mp4"

        has_voice = bool(voice_url)

        if has_voice:
            voice_path = download_file(voice_url, ".mp3")

            cmd = [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-i", music_path,
                "-i", voice_path,
                "-filter_complex",
                "[1:a]volume=0.25[music];[2:a]volume=1.0[voice];[music][voice]amix=inputs=2:duration=shortest[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-t", str(OUTPUT_DURATION),
                "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "96k",
                "-shortest",
                "-movflags", "+faststart",
                out_path,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop", "-1",
                "-i", video_path,
                "-i", music_path,
                "-map", "0:v",
                "-map", "1:a",
                "-t", str(OUTPUT_DURATION),
                "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "96k",
                "-shortest",
                "-movflags", "+faststart",
                out_path,
            ]

        print(f"Running ffmpeg: {' '.join(cmd)}", flush=True)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        print(f"FFmpeg return code: {result.returncode}", flush=True)
        if result.stdout:
            print(f"FFmpeg stdout: {result.stdout[-2000:]}", flush=True)
        if result.stderr:
            print(f"FFmpeg stderr: {result.stderr[-4000:]}", flush=True)

        if result.returncode != 0:
            return jsonify({
                "error": "ffmpeg failed",
                "detail": result.stderr[-4000:]
            }), 500

        print("Uploading to GoFile...", flush=True)
        with open(out_path, "rb") as f:
            upload = requests.post(
                "https://store1.gofile.io/uploadFile",
                files={"file": ("short.mp4", f, "video/mp4")},
                timeout=REQUEST_TIMEOUT
            )

        print(f"GoFile status: {upload.status_code}", flush=True)
        print(f"GoFile response text: {upload.text[:2000]}", flush=True)

        upload_data = upload.json()

        if upload_data.get("status") == "ok":
            direct_url = f"https://store1.gofile.io/download/direct/{upload_data['data']['fileId']}/short.mp4"
            print(f"Success URL: {direct_url}", flush=True)
            return jsonify({"url": direct_url})

        return jsonify({
            "error": "Upload failed",
            "detail": upload_data
        }), 500

    except requests.RequestException as e:
        print("RequestException:", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": "Download/upload request failed", "detail": str(e)}), 500

    except subprocess.TimeoutExpired:
        print("FFmpeg timed out", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": "ffmpeg timed out"}), 500

    except Exception as e:
        print("Unhandled exception:", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": str(e)}), 500

    finally:
        for p in [video_path, music_path, voice_path, out_path]:
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
