#!/usr/bin/env python3
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


CHAT_ENDPOINT = "https://aigateway.51talk.com/v1/chat/completions"
UPLOAD_ENDPOINT = "https://aigateway.51talk.com/v1/task/sync"
DEFAULT_MODEL = "gemini-3.1-pro-preview|efficiency"
DEFAULT_BIZ_ID = "8218469790818477355"
DEFAULT_BIZ_TYPE = "NTT_CE_PRS"
DEFAULT_UPLOAD_BUCKET = "ai-efficiency-center"
DEFAULT_UPLOAD_PROVIDER = "GOOGLE"


def run(cmd, *, capture=False):
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")
    return result.stdout if capture else ""


def require_tool(name):
    if not shutil.which(name):
        raise RuntimeError(f"Missing required tool: {name}")


def is_remote_video(value):
    return value.startswith("gs://") or value.startswith("http://") or value.startswith("https://")


def file_size_mb(path):
    return path.stat().st_size / 1024 / 1024


def ffprobe_json(path):
    require_tool("ffprobe")
    output = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture=True,
    )
    return json.loads(output)


def parse_fps(rate):
    if not rate or rate == "0/0":
        return None
    num, den = rate.split("/")
    den_value = float(den)
    if den_value == 0:
        return None
    return float(num) / den_value


def video_duration_seconds(path):
    data = ffprobe_json(path)
    duration = data.get("format", {}).get("duration")
    if duration:
        return float(duration)
    for stream in data.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise RuntimeError("Could not determine video duration")


def original_fps(path):
    data = ffprobe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            return parse_fps(stream.get("avg_frame_rate")) or parse_fps(stream.get("r_frame_rate"))
    return None


def compression_profile(path, max_mb):
    duration = video_duration_seconds(path)
    if duration <= 0:
        raise RuntimeError("Invalid video duration")

    safety = 0.90
    max_bits = max_mb * 1024 * 1024 * 8 * safety
    total_bps = max_bits / duration
    audio_bps = 48_000
    video_bps = max(160_000, int(total_bps - audio_bps))

    fps = original_fps(path)
    fps_filter = None
    if fps:
        if video_bps < 650_000 and fps > 8:
            fps_filter = "fps=8"
        elif video_bps < 1_100_000 and fps > 12:
            fps_filter = "fps=12"
        elif fps > 24:
            fps_filter = "fps=24"

    return duration, video_bps, audio_bps, fps_filter


def compress_video(input_path, output_dir, max_mb):
    require_tool("ffmpeg")
    input_path = Path(input_path).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if file_size_mb(input_path) <= max_mb:
        return input_path, False

    duration, video_bps, audio_bps, fps_filter = compression_profile(input_path, max_mb)
    stem = input_path.stem
    output_path = output_dir / f"{stem}-under{int(max_mb)}mb.mp4"

    vf_args = []
    if fps_filter:
        vf_args = ["-vf", fps_filter]

    with tempfile.TemporaryDirectory(prefix="demo-assessment-passlog-") as tmp:
        passlog = str(Path(tmp) / "ffmpeg-pass")
        first_pass = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            *vf_args,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            str(video_bps),
            "-pass",
            "1",
            "-passlogfile",
            passlog,
            "-an",
            "-f",
            "mp4",
            os.devnull,
        ]
        second_pass = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            *vf_args,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            str(video_bps),
            "-pass",
            "2",
            "-passlogfile",
            passlog,
            "-c:a",
            "aac",
            "-b:a",
            str(audio_bps),
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        print(f"Compressing video: {file_size_mb(input_path):.1f}MB -> <= {max_mb}MB", file=sys.stderr)
        print(f"Estimated duration: {duration:.1f}s, video bitrate: {math.floor(video_bps / 1000)}k", file=sys.stderr)
        run(first_pass)
        run(second_pass)

    if file_size_mb(output_path) > max_mb:
        raise RuntimeError(
            f"Compressed file is still too large: {file_size_mb(output_path):.1f}MB > {max_mb}MB. "
            "Try a lower --max-mb or manually reduce duration/resolution."
        )

    return output_path, True


def upload_video(path, args):
    require_tool("curl")
    path = Path(path).expanduser().resolve()
    custom_name = args.upload_name or f"codex_demo_{int(time.time())}{path.suffix or '.mp4'}"
    response_path = args.output_dir / f"{Path(custom_name).stem}_upload_response.json"

    cmd = [
        "curl",
        "--http1.1",
        "--retry",
        "2",
        "--retry-delay",
        "3",
        "-sS",
        "--location",
        "--request",
        "POST",
        args.upload_endpoint,
        "--header",
        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36",
        "--header",
        "Accept: application/json, text/plain, */*",
        "--form",
        f"file=@{path};type=video/mp4;filename={custom_name}",
        "--form",
        f"provider={args.upload_provider}",
        "--form",
        f"file_name={custom_name}",
        "--form",
        f"bucket_name={args.upload_bucket}",
    ]

    print(f"Uploading video: {path}", file=sys.stderr)
    response = run(cmd, capture=True)
    response_path.write_text(response, encoding="utf-8")
    data = json.loads(response)
    if not data.get("success"):
        raise RuntimeError(f"Upload failed. Raw response saved at {response_path}\n{response}")
    file_url = data.get("res", {}).get("file_url")
    if not file_url:
        raise RuntimeError(f"Upload succeeded but file_url is missing. Raw response saved at {response_path}")
    print(f"Uploaded file_url: {file_url}", file=sys.stderr)
    return file_url, response_path


def build_payload(prompt_text, video_url, args):
    return {
        "provider": args.provider,
        "api_key": args.api_key,
        "stream": False,
        "presence_penalty": args.presence_penalty,
        "biz_type": args.biz_type,
        "temperature": args.temperature,
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "text": prompt_text,
                        "type": "text",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "file_url",
                        "text": args.user_text,
                        "file_url": {
                            "url": video_url,
                        },
                    }
                ],
            },
        ],
        "model": args.model,
        "biz_id": args.biz_id,
        "n": args.n,
    }


def call_chat(payload, args):
    require_tool("curl")
    payload_path = args.output_dir / f"{args.run_name}_payload.json"
    response_path = args.output_dir / f"{args.run_name}_response.json"
    content_path = args.output_dir / f"{args.run_name}_content.txt"

    safe_payload = dict(payload)
    safe_payload["api_key"] = "***"
    payload_path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        "curl",
        "-sS",
        "--location",
        "--request",
        "POST",
        args.chat_endpoint,
        "--header",
        "Content-Type: application/json",
        "--data-binary",
        "@-",
    ]
    print(f"Calling model: {args.model}", file=sys.stderr)
    result = subprocess.run(
        cmd,
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Chat request failed ({result.returncode}): {result.stderr.strip()}")

    response_path.write_text(result.stdout, encoding="utf-8")
    data = json.loads(result.stdout)
    if not data.get("success"):
        raise RuntimeError(f"Chat API returned failure. Raw response saved at {response_path}\n{result.stdout}")

    content = data.get("res", {}).get("choices", [{}])[0].get("message", {}).get("content", "")
    content_path.write_text(content, encoding="utf-8")
    return response_path, content_path, content


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run demo video assessment with an AI gateway prompt and video input."
    )
    parser.add_argument("--prompt", required=True, help="Path to prompt markdown/text file.")
    parser.add_argument("--video", required=True, help="Local video path, gs:// URL, or http(s) URL.")
    parser.add_argument("--api-key", default=os.getenv("AIGATEWAY_API_KEY"), help="Gateway API key. Defaults to AIGATEWAY_API_KEY.")
    parser.add_argument("--output-dir", default="outputs", type=Path, help="Directory for payload, response, and compressed video.")
    parser.add_argument("--run-name", default=None, help="Output filename prefix. Defaults to timestamp.")
    parser.add_argument("--max-mb", default=100, type=float, help="Compress local videos above this size before upload.")
    parser.add_argument("--skip-compress", action="store_true", help="Upload local video directly without compression.")
    parser.add_argument("--skip-upload", action="store_true", help="Treat --video as already model-readable. Useful for gs/http URLs.")
    parser.add_argument("--user-text", default="demo video", help="Text field sent with the file_url user message.")

    parser.add_argument("--chat-endpoint", default=CHAT_ENDPOINT)
    parser.add_argument("--upload-endpoint", default=UPLOAD_ENDPOINT)
    parser.add_argument("--provider", default="VERTEX")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--biz-id", default=DEFAULT_BIZ_ID)
    parser.add_argument("--biz-type", default=DEFAULT_BIZ_TYPE)
    parser.add_argument("--temperature", default=1, type=float)
    parser.add_argument("--presence-penalty", default=1, type=float)
    parser.add_argument("--n", default=1, type=int)
    parser.add_argument("--upload-bucket", default=DEFAULT_UPLOAD_BUCKET)
    parser.add_argument("--upload-provider", default=DEFAULT_UPLOAD_PROVIDER)
    parser.add_argument("--upload-name", default=None)

    args = parser.parse_args()
    if not args.api_key:
        parser.error("--api-key is required, or set AIGATEWAY_API_KEY.")
    if not args.run_name:
        args.run_name = f"demo_assessment_{time.strftime('%Y%m%d_%H%M%S')}"
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    return args


def main():
    args = parse_args()
    prompt_path = Path(args.prompt).expanduser().resolve()
    if not prompt_path.exists():
        raise RuntimeError(f"Prompt file does not exist: {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    video_input = args.video
    upload_response_path = None
    compressed_path = None

    if is_remote_video(video_input) or args.skip_upload:
        video_url = video_input
    else:
        video_path = Path(video_input).expanduser().resolve()
        if not video_path.exists():
            raise RuntimeError(f"Video file does not exist: {video_path}")
        if args.skip_compress:
            upload_path = video_path
        else:
            upload_path, compressed = compress_video(video_path, args.output_dir, args.max_mb)
            if compressed:
                compressed_path = upload_path
        video_url, upload_response_path = upload_video(upload_path, args)

    payload = build_payload(prompt_text, video_url, args)
    response_path, content_path, content = call_chat(payload, args)

    print("\nDONE", file=sys.stderr)
    print(f"video_url: {video_url}", file=sys.stderr)
    if compressed_path:
        print(f"compressed_video: {compressed_path}", file=sys.stderr)
    if upload_response_path:
        print(f"upload_response: {upload_response_path}", file=sys.stderr)
    print(f"raw_response: {response_path}", file=sys.stderr)
    print(f"model_content: {content_path}", file=sys.stderr)
    print(content)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
