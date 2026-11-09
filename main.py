import atexit
import os
import queue
import signal
import subprocess
import sys
import threading
import traceback
from collections import deque

from config import (
    BASE_DIR,
    NO_WINDOW,
    OUTPUT_DIR,
    PID_FILE,
    WEBHOOK_URLS,
    boost_timer,
    ensure_dependencies,
    hide_console,
    log,
    set_visible,
)

ensure_dependencies()

from capture import capture_loop, grab_template, load_template
from media import worker_loop


def thread_exception_handler(args):
    log(
        "thread-fehler: "
        + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    )


def stop_running():
    try:
        with PID_FILE.open("r", encoding="utf-8") as file:
            pid = int(file.read().strip())
    except (OSError, ValueError):
        print("Kein laufender KillRecorder gefunden.")
        return

    try:
        if os.name == "nt":
            subprocess.call(
                ["taskkill", "/PID", str(pid), "/F"],
                creationflags=NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        print("KillRecorder beendet.")
        log("KillRecorder per --stop beendet")
    except OSError as exception:
        print(f"Konnte Prozess nicht beenden: {exception}")

    try:
        PID_FILE.unlink()
    except OSError:
        pass


def cleanup_pid():
    try:
        with PID_FILE.open("r", encoding="utf-8") as file:
            if int(file.read().strip()) == os.getpid():
                PID_FILE.unlink()
    except (OSError, ValueError):
        pass


def main():
    args = set(sys.argv[1:])
    visible = "--visible" in args
    hidden_child = "--hidden" in args
    set_visible(visible or "--stop" in args)

    if "--stop" in args:
        stop_running()
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if "--reselect" in args:
            raise FileNotFoundError
        template, region = load_template()
    except Exception:
        if hidden_child:
            log("kein gueltiges template - bitte 'python main.py --reselect' ausfuehren")
            sys.exit(1)
        grab_template(log)
        template, region = load_template()

    if os.name == "nt" and not hidden_child and not visible:
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "main.py"), "--hidden"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(BASE_DIR),
        )
        print("KillRecorder laeuft jetzt im Hintergrund.")
        print("Beenden mit: python main.py --stop")
        return

    if not visible:
        hide_console()

    boost_timer()
    with PID_FILE.open("w", encoding="utf-8") as file:
        file.write(str(os.getpid()))
    atexit.register(cleanup_pid)
    threading.excepthook = thread_exception_handler

    if not WEBHOOK_URLS:
        log("hinweis: keine webhook-url gesetzt - clips werden nur lokal gespeichert")
    else:
        log(f"{len(WEBHOOK_URLS)} discord-webhook(s) konfiguriert")

    log("KillRecorder gestartet | Made by Nivo")

    frame_buffer = deque()
    buffer_lock = threading.Lock()
    jobs = queue.Queue(maxsize=50)
    running = threading.Event()
    running.set()

    threading.Thread(target=worker_loop, args=(jobs, log), daemon=True).start()

    try:
        capture_loop(template, region, frame_buffer, buffer_lock, jobs, running, log)
    except KeyboardInterrupt:
        pass
    finally:
        running.clear()
        jobs.put(None)
        log("gestoppt")


if __name__ == "__main__":
    main()
