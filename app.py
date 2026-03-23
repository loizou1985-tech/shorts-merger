from flask import Flask, request, jsonify
import subprocess, os, uuid, requests, tempfile

app = Flask(__name__)

def download_file(url, suffix):
    r = requests.get(url, timeout=60)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.close()
    return tmp.name

@app.route("/merge", methods=["POST"])
def merge():
    data = request.json
    video_path = download_file(data["video_url"], ".mp4")
    music_path = download_file(data["music_url"], ".mp3")
    out_path = f"/tmp/{uuid.uuid4()}.mp4"
    has_voice = "voice_url" in data and data["voice_url"]
    if has_voice:
        voice_path = download_file(data["voice_url"], ".mp3")
        cmd = ["ffmpeg","-y","-i",video_path,"-i",music_path,"-i",voice_path,"-filter_complex","[1:a]volume=0.25[music];[2:a]volume=1.0[voice];[music][voice]amix=inputs=2:duration=shortest[aout]","-map","0:v","-map","[aout]","-t","20","-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,loop=-1:size=999999:start=0","-c:v","libx264","-c:a","aac","-shortest","-movflags","+faststart",out_path]
    else:
        cmd = ["ffmpeg","-y","-stream_loop","-1","-i",video_path,"-i",music_path,"-filter_complex","[1:a]volume=1.0[aout]","-map","0:v","-map","[aout]","-t","20","-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920","-c:v","libx264","-c:a","aac","-shortest","-movflags","+faststart",out_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500
    with open(out_path, "rb") as f:
        upload = requests.post("https://store1.gofile.io/uploadFile", files={"file": ("short.mp4", f, "video/mp4")})
    for p in [video_path, music_path, out_path]:
        try: os.unlink(p)
        except: pass
    if has_voice:
        try: os.unlink(voice_path)
        except: pass
    upload_data = upload.json()
    if upload_data.get("status") == "ok":
        direct_url = f"https://store1.gofile.io/download/direct/{upload_data['data']['fileId']}/short.mp4"
        return jsonify({"url": direct_url})
    else:
        return jsonify({"error": "Upload failed", "detail": upload_data}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
