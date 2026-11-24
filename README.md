# Delphi

A webcam light for your personal data. Built for wHACKiest 2026 (Track 3: Know Thyself).

## What it does
Your laptop and sensors can tell when you slouch, stare at the screen too long, or sit in a stuffy room, but getting insights usually means uploading all your raw data to someone else's cloud.

Delphi keeps everything on your laptop instead:
- Tracks your habits locally (posture, screen time, room temperature/humidity).
- Uses a permission slip before any AI can see your data (only rounded summaries, with a time limit).
- Records everything that leaves your laptop in a logbook that detects tampering.
- Connects to a small desk ESP32 light (Green = local only, Yellow = AI read a summary, Red = blocked) with a physical kill switch button to cut off all AI access instantly.

## How it works
1. **Sensors:** Laptop webcam checks posture in memory and drops the image immediately (only keeps a score). ESP32 reads room temp/humidity with a BME280 sensor and controls the LED + kill switch.
2. **Backend (`vault/app.py`):** Local Flask server + SQLite database. Checks device signatures and message counters so fake readings get rejected.
3. **Logbook:** Every entry links to the previous one using SHA-256 hashing. Editing an old entry breaks the chain and gets caught by `/verify`.
4. **Insights:** Runs simple stats and a local Isolation Forest model on your laptop to spot posture drops or unusual spikes.
5. **Exit Gate:** The only way data leaves your machine. Checks active permission slips, logs what went out, and handles voice nudges through ElevenLabs using generic sentences (no personal numbers sent).

## Current Progress (Early MVP)
- Built the core Flask backend (`vault/app.py`) with local SQLite storage.
- Added device signature + replay check (`POST /ingest`).
- Added the hash-chained privacy logbook, tamper check (`GET /verify`), and data deletion (`DELETE /forget`).
- Added permission slips (`POST /slips`), gated AI queries (`POST /ai/ask`), and instant lock (`POST /revoke`, `GET /esp32/state`).
- Added ElevenLabs voice nudges (`POST /nudge/speak`) with local audio caching.

## Setup
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env
    python vault/app.py

## Working on Next (Ideation Pitch & Hack Day)
- **Web Dashboard:** Live view of sensor readings, permission toggles, privacy logbook, and a standalone verify page.
- **Laptop Collector:** Python script using MediaPipe Pose for live posture scores and `psutil` for screen/app time.
- **ESP32 Desk Node:** Wiring the BME280 sensor, RGB status LED, and physical kill switch button over Wi-Fi.
- **Phone Camera (Optional):** Second camera angle for better posture detection.
- **Voice Controls:** Quick voice commands to approve or cut AI access.
- **Future Additions:** Smartwatch/health app exports, running the vault inside an isolated Docker network, AWS S3 backup for logbook hashes, and connecting external AI assistants via MCP.
