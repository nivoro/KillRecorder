# KillRecorder

A lightweight gameplay clip recorder with automatic kill-feed detection and Discord webhook delivery.

KillRecorder watches a selected screen region, detects new kill-feed entries and creates an MP4 recording with the configured pre-event and post-event footage. Clips are saved locally and can be delivered to one or more Discord channels through webhooks.

## Features

- Automatic kill-feed detection
- Configurable pre-event and post-event recording
- MP4 encoding with automatic compression
- Upload to multiple Discord channels
- Background mode on Windows
- Local clip archive and rotating log file
- No Discord bot token or personal account token required

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- A display with the game running

## Installation

```powershell
py -m pip install -r requirements.txt
```

## Discord webhooks

Create one Discord webhook for every channel that should receive clips:

1. Open the channel settings in Discord.
2. Go to **Integrations** and open **Webhooks**.
3. Create a webhook and copy its URL.
4. Add the webhook URLs to `KillRecorder_WEBHOOK_URLS`.

Example with one channel:

```env
KillRecorder_WEBHOOK_URLS=https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN
```

Example with multiple channels:

```env
KillRecorder_WEBHOOK_URLS=https://discord.com/api/webhooks/ID_ONE/TOKEN_ONE,https://discord.com/api/webhooks/ID_TWO/TOKEN_TWO
```

Each URL sends clips to the channel connected to that webhook. Keep `.env` private. Never commit webhook URLs to GitHub. The `.gitignore` file already excludes `.env`.

When no webhook is configured, KillRecorder keeps the clips in the local `clips` directory.

## First start

```powershell
py main.py --visible
```

On the first run, select the kill-feed text with your mouse and press **Enter**. The selection is stored locally and reused on the next start.

To run KillRecorder in the background on Windows:

```powershell
py main.py
```

## Commands

| Command | Description |
|---|---|
| `py main.py` | Start in the background on Windows |
| `py main.py --visible` | Start with console output |
| `py main.py --reselect` | Select the kill-feed area again |
| `py main.py --stop` | Stop the background process |

## Project structure

| File | Purpose |
|---|---|
| `main.py` | Application entry point and process control |
| `capture.py` | Screen capture and kill-feed detection |
| `media.py` | MP4 encoding and webhook uploads |
| `config.py` | Settings, environment configuration and logging |
| `.env` | Webhook configuration template |
| `requirements.txt` | Python dependencies |

## Configuration

Clip duration, capture FPS, matching threshold, output quality and other runtime settings are defined in `config.py`.

Generated clips, the kill-feed template, region data, log files and local webhook configuration are stored outside the tracked source files.

---

<sub>Made by Nivo</sub>
