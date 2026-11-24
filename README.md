# Delphi - A Webcam Light for Your Personal Data

**wHACKiest 2026 | Track 3: Know Thyself**

Delphi is a local-first personal intelligence hub that learns your posture, screen habits, and room environment on your own machine—requiring a time-boxed **Permission Slip** before any AI sees your data, logging every outbound byte in a tamper-evident **Privacy Ledger**, and letting you cut off all AI access instantly via voice or a physical **ESP32 hardware kill switch**.

## Architecture (6-Stage Local Trust Zone)
1. **Sensors:** Laptop screen/idle tracking + local MediaPipe Pose (frames processed in RAM, never saved) + ESP32 desk node (BME280 temperature/humidity, RGB status LED, physical kill switch).
2. **Ingest Hub (`vault/app.py`):** Flask REST API verifying per-device HMAC-SHA256 signatures and monotonic sequence numbers to block rogue or replayed readings.
3. **The Vault (`delphi_vault.db`):** Local SQLite database (WAL mode) with a SHA-256 hash-chained Privacy Ledger supporting right-to-forget (`/forget`) without breaking chain integrity.
4. **On-Device Intelligence:** Rolling EWMA baselines, z-scores, and `scikit-learn` Isolation Forest anomaly detection running locally on device.
5. **The Egress Gate:** Single outbound checkpoint enforcing scoped Permission Slips and data minimization (rounded aggregates only).
6. **Outputs:** ElevenLabs zero-retention spoken nudges (generic templates cached locally), ESP32 desk LED (Green / Amber / Red), and grounded AI summaries.

## Quick Start

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env
    python vault/app.py

Open `http://127.0.0.1:5000` for the Live Dashboard and `http://127.0.0.1:5000/verify-ui` for the Standalone Browser-Side Receipt Verifier.

## Implemented in Early MVP
- `vault/app.py`: Tier-1 Flask Vault, SQLite WAL storage, per-device HMAC-SHA256 & replay sequence validation (`POST /ingest`)
- Tamper-evident SHA-256 hash-chained Privacy Ledger with live integrity checking (`GET /verify`) and right-to-forget (`DELETE /forget`)
- On-device EWMA, z-score, and Isolation Forest anomaly detection
- Scoped AI Permission Slips (`POST /slips`, `POST /ai/ask`) and instant kill-switch revocation (`POST /revoke`, `GET /esp32/state`)
- ElevenLabs zero-retention voice nudge endpoint (`POST /nudge/speak`) with local MP3 caching
- `dashboard/index.html` & `dashboard/verify.html`: Live control dashboard + client-side WebCrypto receipt verifier

## Roadmap & Future Scope
### Phase 1 - Live Edge Sensors & Hardware (In Progress for Ideation / Hack Day)
- **ESP32 Desk Unit (`esp32_node/`):** Wi-Fi microcontroller with BME280 (temperature & humidity), MPU6050 (desk motion), real-time Green/Amber/Red status LED synced to `/esp32/state`, and a hardware interrupt push-button kill switch triggering `POST /revoke` with offline ring-buffer recovery.
- **Laptop Telemetry Collector (`collectors/laptop_agent.py`):** Native MediaPipe Pose posture scorer (frames discarded in RAM immediately) paired with `psutil` app-category/idle tracking and regex/entropy redaction.
- **Phone Side-View Posture Node:** Optional smartphone camera view running on-device posture estimation over LAN for higher accuracy.
- **Voice Command Override:** Hands-free voice commands ("Approve for 1 hour" / "Cut access") complementing the physical kill switch.

### Phase 2 - Hardened Isolation, Wearables & Ecosystem Scaling
- **Docker Air-Gapped Vault (`internal: true`):** Network-isolated vault container with zero direct internet routing, forcing all outbound traffic physically through a separate Egress Gate container.
- **Wearable & Health Exports:** Local ingestion of smartwatch/fitness exports (Samsung Health, Apple Health, BLE heart-rate broadcast) unified inside the same vault.
- **AWS Cloud Trust Add-Ons:** Periodic AWS S3 anchoring of 32-byte ledger chain-head hashes (external tamper proof with zero personal data uploaded) and optional AWS Bedrock weekly digest synthesis behind Permission Slips.
- **Universal AI Consent Layer (MCP):** Exposing Delphi's permission-slip and audit gate as a standard Model Context Protocol (MCP) server so any external AI assistant (health, calendar, finance, enterprise workflows) must pass Delphi before reading personal context.
