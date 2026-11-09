import os
import subprocess
import time
from datetime import datetime
from urllib.parse import urlparse

import cv2
import imageio_ffmpeg
import numpy as np
import requests

from config import (
    ENCODE_STEPS,
    KEEP_LOCAL_CLIPS,
    MAX_UPLOAD_MB,
    NO_WINDOW,
    OUTPUT_DIR,
    WEBHOOK_URLS,
)


def decode_frame(jpg):
    return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)


def encode_ffmpeg(frames, fps, path, crf, height):
    first = decode_frame(frames[0][1])
    if first is None:
        return False, "erstes frame konnte nicht dekodiert werden"

    frame_height, frame_width = first.shape[:2]
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{frame_width}x{frame_height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]

    if height and height < frame_height:
        command += ["-vf", f"scale=-2:{height}"]
    command.append(str(path))

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
    )

    try:
        for _, jpg in frames:
            decoded = decode_frame(jpg)
            if decoded is not None:
                process.stdin.write(decoded.tobytes())
        process.stdin.close()
    except (BrokenPipeError, OSError, AttributeError):
        try:
            process.stdin.close()
        except (OSError, AttributeError):
            pass

    error = process.stderr.read().decode(errors="replace")
    return_code = process.wait()
    if return_code != 0:
        return False, error
    return os.path.exists(path) and os.path.getsize(path) > 0, error


def encode_fallback(frames, fps, path):
    first = decode_frame(frames[0][1])
    if first is None:
        return False

    frame_height, frame_width = first.shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (frame_width, frame_height),
    )
    if not writer.isOpened():
        return False

    for _, jpg in frames:
        decoded = decode_frame(jpg)
        if decoded is not None:
            writer.write(decoded)
    writer.release()
    return os.path.exists(path) and os.path.getsize(path) > 0


def encode_clip(frames, fps, path, log):
    for crf, height in ENCODE_STEPS:
        try:
            ok, error = encode_ffmpeg(frames, fps, path, crf, height)
        except Exception as exception:
            log(f"ffmpeg fehler: {exception}")
            ok, error = False, ""

        if not ok:
            if error:
                log(f"ffmpeg rc-fehler: {error[:300]}")
            log("ffmpeg fehlgeschlagen - nutze fallback (mp4v)")
            return encode_fallback(frames, fps, path)

        size_mb = os.path.getsize(path) / 1048576
        if size_mb <= MAX_UPLOAD_MB:
            return True
        log(f"clip zu gross ({size_mb:.1f} MB), versuche staerkere kompression")

    return True


def is_valid_webhook(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    return (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and parsed.path.startswith("/api/webhooks/")
        and len(parsed.path.strip("/").split("/")) >= 4
    )


def send_to_webhook(path, webhook_url, log):
    for attempt in range(1, 4):
        try:
            with open(path, "rb") as file:
                response = requests.post(
                    webhook_url,
                    data={"content": "🎮 New kill clip"},
                    files={"files[0]": (os.path.basename(path), file, "video/mp4")},
                    timeout=120,
                )

            if response.status_code in (200, 201, 204):
                return True

            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after", 5))
                except (TypeError, ValueError):
                    wait = 5.0
                log(f"webhook rate limit, warte {wait:.0f}s")
                time.sleep(min(wait, 60) + 0.5)
                continue

            if response.status_code in (400, 401, 403, 404):
                log(f"webhook abgelehnt ({response.status_code})")
                return False

            if response.status_code == 413:
                log("discord: datei zu gross - MAX_UPLOAD_MB senken")
                return False

            log(f"webhook antwort {response.status_code}: {response.text[:200]}")
        except requests.RequestException as exception:
            log(f"webhook sendefehler (versuch {attempt}): {exception}")

        if attempt < 3:
            time.sleep(2 * attempt)

    return False


def send_discord_clip(path, log):
    if not WEBHOOK_URLS:
        log("keine webhook-url konfiguriert - clip nur lokal gespeichert")
        return False

    successful = 0
    for index, webhook_url in enumerate(WEBHOOK_URLS, start=1):
        if not is_valid_webhook(webhook_url):
            log(f"webhook {index}: ungueltige discord-webhook-url")
            continue

        if send_to_webhook(path, webhook_url, log):
            successful += 1
            log(f"clip an webhook {index}/{len(WEBHOOK_URLS)} gesendet")

    if successful != len(WEBHOOK_URLS):
        log(f"webhook-upload unvollstaendig: {successful}/{len(WEBHOOK_URLS)} erfolgreich")
        return False

    return successful > 0


def process_clip(frames, log):
    span = max(frames[-1][0] - frames[0][0], 0.1)
    fps = int(max(5, min(60, round((len(frames) - 1) / span))))
    name = f"kill_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.mp4"
    path = OUTPUT_DIR / name

    if not encode_clip(frames, fps, path, log):
        log("clip konnte nicht erstellt werden")
        return

    size_mb = os.path.getsize(path) / 1048576
    log(f"clip gespeichert: {name} ({size_mb:.1f} MB, {len(frames)} frames, {fps} fps)")

    if send_discord_clip(path, log) and not KEEP_LOCAL_CLIPS:
        try:
            os.remove(path)
        except OSError:
            pass


def worker_loop(jobs, log):
    while True:
        frames = jobs.get()
        try:
            if frames is None:
                return
            process_clip(frames, log)
        except Exception:
            import traceback
            log("clip fehler: " + traceback.format_exc(limit=3))
        finally:
            jobs.task_done()
