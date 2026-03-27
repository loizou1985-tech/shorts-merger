from flask import Flask, request, jsonify
import subprocess
import os
import requests
import tempfile
import traceback

app = Flask(__name__)

MAX_SIZE_MB = 30
REQUEST_TIMEOUT = 180
OUTPUT_DURATION = 60

# You can hardcode for testing, or replace with env vars later.
ELEVENLABS_API_KEY = "YOUR_REAL_ELEVENLABS_API_KEY"
ELEVENLABS_VOICE_ID = "uhYnkYTBc711oAY590Ea"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"


def download_file(url, suffix):
    print(f"Downloading: {url}", flush=True)

    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()

        content_length = int(r.headers.get("content-length", 0))
        print(f"Content-Length: {content_length}", flush=True)

        if content_length and content_length > MAX_SIZE_MB * 1024 * 1024:
            raise Exception(
                f"File too large: {round(content_length / (1024 * 1024), 2)} MB"
            )

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        written = 0

        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
                    written += len(chunk)
        finally:
            tmp.close()

    print(f"Saved to: {tmp.name} ({written} bytes)", flush=True)

    if written == 0:
        raise Exception("Downloaded file is empty or invalid URL")

    return tmp.name


def generate_voice_file(text):
    if not text or not text.strip():
        raise Exception("Voice script is empty")

    if not ELEVENLABS_API_KEY:
        raise Exception("ELEVENLABS_API_KEY is not configured")

    if not ELEVENLABS_VOICE_ID:
        raise Exception("ELEVENLABS_VOICE_ID is not configured")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
    }

    print("Generating ElevenLabs voiceover...", flush=True)
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    voice_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    with open(voice_path, "wb") as f:
        f.write(response.content)

    if not os.path.exists(voice_path) or os.path.getsize(voice_path) == 0:
        raise Exception("Generated voice file is empty")

    print(
        f"Voice saved to: {voice_path} ({os.path.getsize(voice_path)} bytes)",
        flush=True,
    )
    return voice_path


def upload_to_gofile(file_path):
    print("Getting GoFile server...", flush=True)

    server_res = requests.get("https://api.gofile.io/getServer", timeout=REQUEST_TIMEOUT)
    server_res.raise_for_status()
    server_data = server_res.json()

    if server_data.get("status") != "ok":
        raise Exception(f"Failed to get GoFile server: {server_data}")

    server = server_data["data"]["server"]
    upload_url = f"https://{server}.gofile.io/uploadFile"

    print(f"Uploading to: {upload_url}", flush=True)

    with open(file_path, "rb") as f:
        upload = requests.post(
            upload_url,
            files={"file": ("short.mp4", f, "video/mp4")},
            timeout=REQUEST_TIMEOUT,
        )

    print(f"GoFile upload status: {upload.status_code}", flush=True)
    print(f"GoFile response text: {upload.text[:2000]}", flush=True)

    if upload.status_code != 200:
        raise Exception(f"Upload failed with status code {upload.status_code}")

    upload_data = upload.json()

    if upload_data.get("status") != "ok":
        raise Exception(f"Upload failed: {upload_data}")

    data = upload_data.get("data", {})
    download_page = data.get("downloadPage")
    direct_link = data.get("directLink") or data.get("link")

    if not download_page:
        raise Exception("Download page missing from GoFile response")

    print(f"Success download page: {download_page}", flush=True)
    if direct_link:
        print(f"Direct link: {direct_link}", flush=True)

    return {
        "downloadPage": download_page,
        "directLink": direct_link,
        "raw": upload_data,
    }


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
        voice_script = data.get("voice_script", "")

        if not video_url or not music_url:
            return jsonify({"error": "video_url and music_url are required"}), 400

        if not voice_script or not voice_script.strip():
            return jsonify({"error": "voice_script is required"}), 400

        video_path = download_file(video_url, ".mp4")
        music_path = download_file(music_url, ".mp3")
        voice_path = generate_voice_file(voice_script)

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return jsonify({"error": "Video file is empty"}), 400

        if not os.path.exists(music_path) or os.path.getsize(music_path) == 0:
            return jsonify({"error": "Music file is empty"}), 400

        if not os.path.exists(voice_path) or os.path.getsize(voice_path) == 0:
            return jsonify({"error": "Voice file is empty"}), 400

        out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", video_path,
            "-i", music_path,
            "-i", voice_path,
            "-filter_complex",
            "[1:a]volume=0.18[music];"
            "[2:a]volume=1.0[voice];"
            "[music][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-t", str(OUTPUT_DURATION),
            "-vf",
            "scale=540:960:force_original_aspect_ratio=increase,"
            "crop=540:960,fps=30,format=yuv420p",
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

        print(f"Running ffmpeg: {' '.join(cmd)}", flush=True)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        print(f"FFmpeg return code: {result.returncode}", flush=True)
        if result.stdout:
            print(f"FFmpeg stdout: {result.stdout[-2000:]}", flush=True)
        if result.stderr:
            print(f"FFmpeg stderr: {result.stderr[-4000:]}", flush=True)

        if result.returncode != 0:
            return jsonify({
                "error": "ffmpeg failed",
                "detail": result.stderr[-4000:],
            }), 500

        if not os.path.exists(out_path) or os.path.getsize(out_path) < 100000:
            return jsonify({"error": "Generated video is invalid"}), 500

        upload_result = upload_to_gofile(out_path)

        return jsonify({
            "success": True,
            "downloadPage": upload_result["downloadPage"],
            "directLink": upload_result["directLink"],
        })

    except requests.RequestException as e:
        print("RequestException:", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({
            "error": "Download/upload request failed",
            "detail": str(e),
        }), 500

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


@app.route("/debug-env", methods=["GET"])
def debug_env():
    return jsonify({
        "has_api_key": bool(ELEVENLABS_API_KEY),
        "voice_id": ELEVENLABS_VOICE_ID,
        "model_id": ELEVENLABS_MODEL_ID,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
