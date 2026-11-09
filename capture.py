import json
import queue
import sys
import threading
import time
from collections import deque

import cv2
import mss
import numpy as np

from config import (
    CLIP_HEIGHT,
    CONFIRM_FRAMES,
    FPS,
    KILL_TEMPLATE,
    MATCH_THRESHOLD,
    MAX_MATCHES,
    MONITOR_INDEX,
    POST_SECONDS,
    PRE_SECONDS,
    REGION_FILE,
    SEARCH_MARGIN_X,
    SEARCH_MARGIN_Y,
    JPEG_QUALITY,
)


def grab_template(log):
    log("template-auswahl startet")
    with mss.mss() as screen:
        monitor = screen.monitors[MONITOR_INDEX]
        image = cv2.cvtColor(np.asarray(screen.grab(monitor)), cv2.COLOR_BGRA2BGR)

    height, width = image.shape[:2]
    scale = min(1.0, 1600 / width, 900 / height)
    preview = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else image

    print("Ziehe mit der Maus einen Rahmen um den Kill-Feed Text, dann ENTER.")
    rx, ry, rw, rh = cv2.selectROI("Kill-Feed auswaehlen", preview, fromCenter=False, showCrosshair=True)
    cv2.destroyAllWindows()

    x = max(0, int(round(rx / scale)))
    y = max(0, int(round(ry / scale)))
    crop_width = min(width - x, int(round(rw / scale)))
    crop_height = min(height - y, int(round(rh / scale)))
    if crop_width < 8 or crop_height < 8:
        log("keine (oder zu kleine) auswahl - abbruch")
        sys.exit(1)

    crop = image[y:y + crop_height, x:x + crop_width]
    ok, png = cv2.imencode(".png", crop)
    if not ok:
        log("template konnte nicht gespeichert werden")
        sys.exit(1)

    png.tofile(str(KILL_TEMPLATE))
    with REGION_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "x": x,
                "y": y,
                "w": crop_width,
                "h": crop_height,
                "screen_w": width,
                "screen_h": height,
            },
            file,
        )
    log(f"template gespeichert ({crop_width}x{crop_height} bei {x},{y})")
    print(f"Template gespeichert: {KILL_TEMPLATE}")


def load_template():
    image_data = np.fromfile(str(KILL_TEMPLATE), dtype=np.uint8)
    template = cv2.imdecode(image_data, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError("template nicht lesbar")
    with REGION_FILE.open("r", encoding="utf-8") as file:
        region = json.load(file)
    return template, region


def count_matches(roi_gray, template):
    result = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    template_height, template_width = template.shape[:2]
    count = 0

    while count < MAX_MATCHES:
        _, match_value, _, (x, y) = cv2.minMaxLoc(result)
        if match_value < MATCH_THRESHOLD:
            break
        count += 1
        result[
            max(0, y - template_height // 2):y + template_height // 2 + 1,
            max(0, x - template_width // 2):x + template_width // 2 + 1,
        ] = 0

    return count


class KillDetector:
    def __init__(self):
        self.confirmed = 0
        self.candidate = 0
        self.stable = 0

    def update(self, count):
        if count == self.candidate:
            self.stable += 1
        else:
            self.candidate = count
            self.stable = 1

        if self.stable >= CONFIRM_FRAMES and self.candidate != self.confirmed:
            detected = self.candidate > self.confirmed
            self.confirmed = self.candidate
            return detected
        return False


def snapshot(kill_time, frame_buffer, buffer_lock, jobs, log):
    with buffer_lock:
        frames = [
            (timestamp, jpg)
            for timestamp, jpg in frame_buffer
            if kill_time - PRE_SECONDS <= timestamp <= kill_time + POST_SECONDS
        ]

    if len(frames) < 5:
        log("zu wenige frames - clip verworfen")
        return

    try:
        jobs.put_nowait(frames)
    except queue.Full:
        log("warteschlange voll - clip verworfen")


def capture_loop(template, region, frame_buffer, buffer_lock, jobs, running, log):
    template_height, template_width = template.shape[:2]
    interval = 1.0 / FPS
    detector = KillDetector()

    while running.is_set():
        try:
            with mss.mss() as screen:
                monitor = screen.monitors[MONITOR_INDEX]
                screen_width = monitor["width"]
                screen_height = monitor["height"]

                if region.get("screen_w") and (region["screen_w"], region["screen_h"]) != (screen_width, screen_height):
                    log("warnung: aufloesung hat sich geaendert - mit --reselect neu auswaehlen")

                x0 = max(0, region["x"] - SEARCH_MARGIN_X)
                y0 = max(0, region["y"] - SEARCH_MARGIN_Y)
                x1 = min(screen_width, region["x"] + region["w"] + SEARCH_MARGIN_X)
                y1 = min(screen_height, region["y"] + region["h"] + SEARCH_MARGIN_Y)

                if x1 - x0 < template_width or y1 - y0 < template_height:
                    log("suchbereich kleiner als template - abbruch (--reselect)")
                    running.clear()
                    return

                scale = min(1.0, CLIP_HEIGHT / screen_height)
                output_width = max(2, int(screen_width * scale) // 2 * 2)
                output_height = max(2, int(screen_height * scale) // 2 * 2)
                resize_needed = (output_width, output_height) != (screen_width, screen_height)
                log(
                    f"capture gestartet ({screen_width}x{screen_height} -> "
                    f"{output_width}x{output_height}, suche in {x1 - x0}x{y1 - y0})"
                )

                next_time = time.monotonic()
                while running.is_set():
                    frame = np.asarray(screen.grab(monitor))
                    now = time.monotonic()

                    small = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA) if resize_needed else frame
                    ok, jpg = cv2.imencode(
                        ".jpg",
                        cv2.cvtColor(small, cv2.COLOR_BGRA2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
                    )
                    if ok:
                        with buffer_lock:
                            frame_buffer.append((now, jpg.tobytes()))
                            cutoff = now - (PRE_SECONDS + POST_SECONDS + 4)
                            while frame_buffer and frame_buffer[0][0] < cutoff:
                                frame_buffer.popleft()

                    roi = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGRA2GRAY)
                    match_count = count_matches(roi, template)
                    if detector.update(match_count):
                        log(f"kill erkannt ({match_count} im feed)")
                        timer = threading.Timer(
                            POST_SECONDS + 0.2,
                            snapshot,
                            args=(now, frame_buffer, buffer_lock, jobs, log),
                        )
                        timer.daemon = True
                        timer.start()

                    next_time += interval
                    delay = next_time - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -interval * 3:
                        next_time = time.monotonic()
        except Exception:
            import traceback
            log("capture fehler, neuer versuch: " + traceback.format_exc(limit=2))
            time.sleep(2)
