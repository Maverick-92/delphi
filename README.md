# Delphi - A Webcam Light for Your Personal Data

**wHACKiest 2026 | Track 3: Know Thyself**

Delphi is a local-first personal intelligence hub that learns your posture, screen habits, and room environment on your own machine—requiring a time-boxed **Permission Slip** before any AI sees your data, logging every outbound byte in a tamper-evident **Privacy Ledger**, and letting you cut off all AI access instantly via voice or a physical **ESP32 hardware kill switch**.

## Architecture (6-Stage Local Trust Zone)
1. **Sensors:** Laptop screen/idle tracking + local MediaPipe Pose (frames processed in RAM, never saved) + ESP32 (BME280 temp/humidity, RGB status LED, physical kill switch).
2. **Ingest Hub (`vault/app.py`):** Flask REST API verifying per-device HMAC-SHA256 signatures and monotonic sequence numbers to block rogue/replayed readings.
3. **The Vault:** Local SQLite database (WAL mode) with a SHA-256 hash-chained Privacy Ledger supporting right-to-forget (`/forget`) without breaking integrity.
4. **On-Device Intelligence:** EWMA baselines, z-scores, and `scikit-learn` Isolation Forest anomaly detection running locally.
5. **The Egress Gate:** Single outbound checkpoint enforcing scoped Permission Slips and allow-listed aggregate rules.
6. **Outputs:** ElevenLabs zero-retention spoken nudges (generic templates only, cached locally), ESP32 desk LED (Green / Amber / Red), and grounded AI summaries.

## Quick Start

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env
    python vault/app.py

Open `http://127.0.0.1:5000` for the Live Dashboard and `http://127.0.0.1:5000/verify-ui` for the Standalone Receipt Verifier.

## Key Endpoints Built
- `POST /ingest` - HMAC + sequence-verified telemetry ingestion
- `POST /ai/ask` - Permission-slip-gated AI query with Privacy Ledger logging
- `POST /nudge/speak` - ElevenLabs zero-retention TTS with local MP3 cache
- `POST /revoke` & `GET /esp32/state` - Instant kill switch and LED state sync
- `GET /verify` & `DELETE /forget` - Live hash-chain integrity check and data erasure
