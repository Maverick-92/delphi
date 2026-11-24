# Delphi

**A webcam light for your personal data.**
*wHACKiest 2026 | MS Ramaiah Institute of Technology | Track 3: Know Thyself*

Your laptop webcam has a small light so you always know when someone is watching. **Delphi** gives your personal data the same thing: a physical desk light, a strict gate for outside AI apps, and a receipt book you can verify yourself.

Everything runs locally on your own laptop. Raw personal data stays on your device.

---

## How It Works

~~~text
Sensors (Webcam, Laptop, Watch, Room)
        |
        v
RAM-Only Check (Zero photos or raw logs saved)
        |
        v
Live Dashboard + Focus Check (Instant updates)
        |
        v
External AI --> THE GATE <--> Desk Light & Button (ESP32)
                   |
                   v
       Tamper-Proof Logbook + QR Receipt
~~~

---

## Key Features

1. **Nothing Raw Saved (RAM-Only Loop)**
   Webcam frames, active app names, and raw heart-rate ticks are checked in memory and deleted right away. A live counter on screen shows how many frames were checked vs. **0 bytes saved to disk**.

2. **Instant Live Updates**
   Instead of refreshing pages every few seconds, a live stream (`/stream`) pushes updates to the screen and desk light right away whenever your posture drops, an AI asks for data, or you press the desk button.

3. **Know Your State + Respectful Voice Nudges**
   Delphi combines four live signals—posture, app switching, room temperature/humidity, and smartwatch pulse—into one clear state: **Deep Flow**, **Slumped**, **Overheated**, or **Burnout Drift**.
   * **ElevenLabs Voice Nudges:** Delphi stays quiet while you are in **Deep Flow** and only speaks a short reminder when strain builds up. Only generic phrases are fetched from ElevenLabs and saved locally—**zero personal numbers ever leave your laptop**.

4. **The Gate (Control What AI Sees)**
   Outside AI tools cannot touch your raw files or sensors. They need a timed **Permission Slip** (for example: *posture trend only, expires in 1 hour*). Anything not listed on the slip gets stripped out before it leaves.

5. **Physical Desk Light & Button (ESP32)**
   * **Green Light:** Safe, local-only mode.
   * **Blinking Amber Light:** An outside AI is asking for data. **Short tap** the button on your desk to approve it once.
   * **Solid Red Light:** **Press and hold (2 seconds)** for the hardware kill switch. Cuts off all AI access right away and speaks *"Vault locked."*

6. **Tamper-Proof Logbook & QR Receipt**
   Every time an AI reads a summary, Delphi logs a linked receipt and shows a scannable **QR Data Receipt** (exact bytes sent, what was hidden, and a check code). Anyone can check it offline in `dashboard/verify.html`. Editing any old row breaks the chain and gets caught right away, while deleting personal values (`/forget`) keeps the proof intact.

7. **Live "Rogue AI" Test**
   A separate test script fires three real checks during demos: tries to grab blocked raw data, replays an old sensor message, and edits an old log entry—so anyone can watch Delphi block all three live.

---

## Files in This Repo (Right Now)

| File | What It Does |
|---|---|
| `vault/app.py` | Main local Python server (sensor checks, Permission Slips, logbook, ElevenLabs cache, ESP32 sync) |
| `dashboard/index.html` | Live control screen for Delphi |
| `dashboard/verify.html` | Standalone offline receipt checker |
| `requirements.txt` | Python packages needed to run the server |
| `.env.example` | Sample settings file (no real API keys are stored here) |
| `.gitignore` | Keeps secrets, local databases, and `.venv` off GitHub |

---

## Adding Live on Hack Day

* Live `/stream` event feed and 4-signal Focus Engine
* Real-time laptop collector (webcam posture + window focus) and smartwatch heart-rate link
* ESP32 desk hardware code (room sensor, RGB light, Tap-to-Approve / Hold-to-Kill button)
* Live "Rogue AI" demo script and scannable QR receipt view

---

## Run Locally (Windows PowerShell)

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python vault/app.py
~~~
