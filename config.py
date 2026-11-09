import importlib
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "clips"
KILL_TEMPLATE = BASE_DIR / "kill_template.png"
REGION_FILE = BASE_DIR / "kill_region.json"
LOG_FILE = BASE_DIR / "killrecorder.log"
PID_FILE = BASE_DIR / "killrecorder.pid"
ENV_FILE = BASE_DIR / ".env"

MAX_UPLOAD_MB = 9
PRE_SECONDS = 3
POST_SECONDS = 2
FPS = 30
MONITOR_INDEX = 1
MATCH_THRESHOLD = 0.80
CONFIRM_FRAMES = 2
SEARCH_MARGIN_X = 60
SEARCH_MARGIN_Y = 300
MAX_MATCHES = 10
CLIP_HEIGHT = 720
JPEG_QUALITY = 85
KEEP_LOCAL_CLIPS = True
ENCODE_STEPS = [(25, None), (29, None), (32, 540), (36, 480)]

REQUIRED = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "mss": "mss",
    "requests": "requests",
    "imageio_ffmpeg": "imageio-ffmpeg",
}

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
VISIBLE = False
_LOG_LOCK = threading.Lock()


def load_env_file():
    if not ENV_FILE.exists():
        return

    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_env_file()

_raw_webhooks = os.environ.get("KillRecorder_WEBHOOK_URLS", os.environ.get("KillRecorder_WEBHOOK_URL", ""))
WEBHOOK_URLS = tuple(
    dict.fromkeys(
        url.strip()
        for url in _raw_webhooks.replace("\n", ",").split(",")
        if url.strip()
    )
)


def ensure_dependencies():
    for module, package in REQUIRED.items():
        try:
            importlib.import_module(module)
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package, "--quiet"],
                creationflags=NO_WINDOW,
            )


def set_visible(value):
    global VISIBLE
    VISIBLE = value


def log(message):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    with _LOG_LOCK:
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
                os.replace(LOG_FILE, str(LOG_FILE) + ".old")
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError:
            pass
    if VISIBLE:
        print(line, flush=True)


def hide_console():
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if handle:
            ctypes.windll.user32.ShowWindow(handle, 0)


def boost_timer():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
