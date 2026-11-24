import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from sklearn.ensemble import IsolationForest

# --- Setup & Config ---
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DB_PATH = ROOT_DIR / "delphi_vault.db"
CACHE_DIR = ROOT_DIR / "cache"
DASHBOARD_DIR = ROOT_DIR / "dashboard"
CACHE_DIR.mkdir(exist_ok=True)

DEVICE_SECRET = os.getenv("DELPHI_DEVICE_SECRET", "delphi_hackathon_secret_2026")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()

# Generic, value-free nudge phrases (zero personal numbers ever leave to ElevenLabs)
NUDGE_TEMPLATES = {
    "posture": "Posture check. You have been slouching. Sit up straight and roll your shoulders back.",
    "stuffy": "The room air is getting warm and stuffy. Crack a window and take a quick breath.",
    "focus": "You have been at the screen for a long stretch. Take a two minute break and rest your eyes.",
    "access_granted": "Permission verified. An approved summary was just shared with your AI assistant.",
    "access_blocked": "Access denied. Your Delphi vault is currently locked.",
}

app = Flask(__name__)
CORS(app)


# --- Database Helpers ---
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                source TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                metric TEXT NOT NULL,
                value REAL,
                unit TEXT NOT NULL,
                payload_json TEXT,
                payload_sha256 TEXT NOT NULL,
                forgotten INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS device_seq (
                device_id TEXT PRIMARY KEY,
                last_seq INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS privacy_ledger (
                idx INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                destination TEXT NOT NULL,
                rule_matched TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                outbound_json TEXT NOT NULL,
                bytes_out INTEGER NOT NULL,
                prev_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS permission_slips (
                slip_id TEXT PRIMARY KEY,
                client TEXT NOT NULL,
                allowed_metrics TEXT NOT NULL,
                window_minutes INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Default state
        defaults = {
            "kill_switch": "0",
            "led_state": "GREEN",
            "led_until": "0",
        }
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_state (key, value) VALUES (?, ?)", (k, v)
            )

        # Default demo Permission Slip (valid 2 hours)
        now = int(time.time())
        conn.execute(
            """
            INSERT OR IGNORE INTO permission_slips
            (slip_id, client, allowed_metrics, window_minutes, expires_at, revoked)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                "slip-demo",
                "delphi-assistant",
                json.dumps(["posture_score", "humidity_pct", "temp_c", "focus_score"]),
                60,
                now + 7200,
            ),
        )
        conn.commit()


# --- Security & Hash Chain Helpers ---
def compute_hmac(device_id, seq, ts, metric, value, unit) -> str:
    preimage = f"1|{device_id}|{seq}|{ts}|{metric}|{float(value):.2f}|{unit}"
    return hmac.new(
        DEVICE_SECRET.encode("utf-8"), preimage.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def compute_chain_hash(
    prev_hash, ts, event_type, actor, destination, payload_sha256, outbound_json
) -> str:
    raw = f"{prev_hash}|{ts}|{event_type}|{actor}|{destination}|{payload_sha256}|{outbound_json}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_ledger(
    conn, event_type, actor, destination, rule_matched, payload_sha256, outbound_obj
):
    ts = int(time.time())
    outbound_json = json.dumps(outbound_obj, sort_keys=True, separators=(",", ":"))
    bytes_out = len(outbound_json.encode("utf-8")) if destination != "local_vault" else 0

    row = conn.execute(
        "SELECT chain_hash FROM privacy_ledger ORDER BY idx DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["chain_hash"] if row else "0" * 64
    chain_hash = compute_chain_hash(
        prev_hash, ts, event_type, actor, destination, payload_sha256, outbound_json
    )

    cur = conn.execute(
        """
        INSERT INTO privacy_ledger
        (ts, event_type, actor, destination, rule_matched, payload_sha256, outbound_json, bytes_out, prev_hash, chain_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            event_type,
            actor,
            destination,
            rule_matched,
            payload_sha256,
            outbound_json,
            bytes_out,
            prev_hash,
            chain_hash,
        ),
    )
    return cur.lastrowid, chain_hash


def get_state(conn, key, default=""):
    row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def set_led(conn, color, hold_seconds=5):
    set_state(conn, "led_state", color)
    set_state(conn, "led_until", int(time.time()) + hold_seconds)


def current_led_color(conn):
    if get_state(conn, "kill_switch", "0") == "1":
        return "RED"
    until = int(get_state(conn, "led_until", "0"))
    if int(time.time()) < until:
        return get_state(conn, "led_state", "GREEN")
    return "GREEN"


# --- On-Device Intelligence (EWMA + Z-Score + Isolation Forest) ---
def analyze_insights(conn):
    rows = conn.execute(
        """
        SELECT id, metric, value, unit, ts
        FROM telemetry
        WHERE forgotten = 0 AND value IS NOT NULL
        ORDER BY id DESC LIMIT 200
        """
    ).fetchall()

    by_metric = {}
    for r in reversed(rows):
        by_metric.setdefault(r["metric"], []).append(dict(r))

    insights = []
    alerts = []

    for metric, items in by_metric.items():
        vals = np.array([x["value"] for x in items], dtype=float)
        n = len(vals)
        latest = float(vals[-1])
        mean = float(np.mean(vals))
        std = float(np.std(vals)) if n > 1 else 0.0

        # EWMA baseline
        alpha = 0.25
        ewma = vals[0]
        for v in vals[1:]:
            ewma = alpha * v + (1 - alpha) * ewma

        z_score = round((latest - mean) / std, 2) if std > 0.01 else 0.0
        confidence = "high" if n >= 20 else ("medium" if n >= 8 else "low")

        # Isolation Forest anomaly check once enough live readings arrive
        anomaly = False
        if n >= 10 and std > 0.01:
            iso = IsolationForest(n_estimators=40, contamination=0.1, random_state=42)
            preds = iso.fit_predict(vals.reshape(-1, 1))
            anomaly = bool(preds[-1] == -1)

        if metric == "posture_score":
            claim = (
                f"Posture averaging {round(mean, 1)}% (latest {round(latest, 1)}%, "
                f"EWMA {round(float(ewma), 1)}%)."
            )
            if latest < 65:
                alerts.append({"type": "posture", "reason": f"Posture dropped to {round(latest, 1)}%"})
        elif metric == "humidity_pct":
            claim = f"Room humidity averaging {round(mean, 1)}% (latest {round(latest, 1)}%)."
            if latest > 70 or z_score > 1.8:
                alerts.append({"type": "stuffy", "reason": f"Humidity spiked to {round(latest, 1)}%"})
        elif metric == "temp_c":
            claim = f"Room temperature averaging {round(mean, 1)} C (latest {round(latest, 1)} C)."
        else:
            claim = f"{metric} averaging {round(mean, 1)} (latest {round(latest, 1)})."

        insights.append(
            {
                "metric": metric,
                "latest": round(latest, 1),
                "mean": round(mean, 1),
                "ewma": round(float(ewma), 1),
                "z_score": z_score,
                "anomaly": anomaly,
                "sample_count": n,
                "confidence": confidence,
                "evidence_ids": [x["id"] for x in items[-5:]],
                "claim": claim,
            }
        )

    return insights, alerts


# --- Routes: Ingestion & Security ---
@app.route("/ingest", methods=["POST"])
def ingest():
    data = request.get_json(silent=True) or {}
    required = ["device_id", "source", "seq", "ts", "metric", "value", "unit", "sig"]
    missing = [k for k in required if k not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    device_id = str(data["device_id"])
    source = str(data["source"])
    seq = int(data["seq"])
    ts = int(data["ts"])
    metric = str(data["metric"])
    value = float(data["value"])
    unit = str(data["unit"])
    sig = str(data["sig"])

    expected_sig = compute_hmac(device_id, seq, ts, metric, value, unit)
    payload_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
    payload_sha = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    with get_db() as conn:
        # 1. Verify device HMAC signature
        if not hmac.compare_digest(sig, expected_sig):
            append_ledger(
                conn,
                "INGEST_REJECTED",
                device_id,
                "local_vault",
                "invalid_hmac_signature",
                payload_sha,
                {"reason": "bad_signature", "device_id": device_id, "metric": metric},
            )
            set_led(conn, "RED", 4)
            conn.commit()
            return jsonify({"error": "Invalid HMAC signature — rogue device rejected"}), 401

        # 2. Verify monotonic sequence number (replay attack protection)
        seq_row = conn.execute(
            "SELECT last_seq FROM device_seq WHERE device_id = ?", (device_id,)
        ).fetchone()
        if seq_row and seq <= seq_row["last_seq"]:
            append_ledger(
                conn,
                "INGEST_REJECTED",
                device_id,
                "local_vault",
                "replay_seq_detected",
                payload_sha,
                {"reason": "replay_attack", "seq": seq, "last_seq": seq_row["last_seq"]},
            )
            set_led(conn, "RED", 4)
            conn.commit()
            return jsonify({"error": f"Replay rejected: seq {seq} <= {seq_row['last_seq']}"}), 409

        # 3. Store locally in Vault
        conn.execute(
            "INSERT OR REPLACE INTO device_seq (device_id, last_seq) VALUES (?, ?)",
            (device_id, seq),
        )
        cur = conn.execute(
            """
            INSERT INTO telemetry
            (device_id, source, seq, ts, metric, value, unit, payload_json, payload_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (device_id, source, seq, ts, metric, value, unit, payload_str, payload_sha),
        )

        # Record digest in chain (0 bytes leave machine)
        append_ledger(
            conn,
            "LOCAL_INGEST",
            device_id,
            "local_vault",
            "hmac_verified",
            payload_sha,
            {"metric": metric, "device_id": device_id, "seq": seq},
        )
        conn.commit()

    return jsonify({"status": "stored", "id": cur.lastrowid, "payload_sha256": payload_sha}), 201


# --- Routes: Egress Gate, Permission Slips & AI ---
@app.route("/slips", methods=["GET", "POST"])
def handle_slips():
    with get_db() as conn:
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            slip_id = body.get("slip_id", "slip-demo")
            client = body.get("client", "delphi-assistant")
            allowed_metrics = body.get(
                "allowed_metrics", ["posture_score", "humidity_pct", "temp_c", "focus_score"]
            )
            duration_min = int(body.get("duration_minutes", 60))
            expires_at = int(time.time()) + (duration_min * 60)

            conn.execute(
                """
                INSERT OR REPLACE INTO permission_slips
                (slip_id, client, allowed_metrics, window_minutes, expires_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (slip_id, client, json.dumps(allowed_metrics), duration_min, expires_at),
            )
            append_ledger(
                conn,
                "SLIP_UPDATED",
                "user",
                "local_vault",
                "permission_slip_set",
                hashlib.sha256(slip_id.encode()).hexdigest(),
                {"slip_id": slip_id, "allowed_metrics": allowed_metrics, "expires_at": expires_at},
            )
            conn.commit()
            return jsonify({"status": "updated", "slip_id": slip_id, "allowed_metrics": allowed_metrics})

        rows = conn.execute("SELECT * FROM permission_slips").fetchall()
        slips = []
        for r in rows:
            d = dict(r)
            d["allowed_metrics"] = json.loads(d["allowed_metrics"])
            slips.append(d)
        return jsonify({"slips": slips})


@app.route("/ai/ask", methods=["POST"])
def ai_ask():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "How am I doing today?")).strip()
    slip_id = str(body.get("slip_id", "slip-demo"))
    now = int(time.time())

    with get_db() as conn:
        # Gate Check 1: Hardware Kill Switch
        if get_state(conn, "kill_switch", "0") == "1":
            append_ledger(
                conn,
                "EGRESS_BLOCKED",
                "delphi-assistant",
                "external_ai",
                "hardware_kill_switch_active",
                hashlib.sha256(question.encode()).hexdigest(),
                {"blocked": True, "reason": "Hardware kill switch active"},
            )
            set_led(conn, "RED", 6)
            conn.commit()
            return (
                jsonify(
                    {
                        "allowed": False,
                        "error": "BLOCKED: Hardware Kill Switch is active. Zero bytes left your machine.",
                    }
                ),
                403,
            )

        # Gate Check 2: Permission Slip validity
        slip = conn.execute(
            "SELECT * FROM permission_slips WHERE slip_id = ?", (slip_id,)
        ).fetchone()
        if not slip or slip["revoked"] == 1 or now > slip["expires_at"]:
            append_ledger(
                conn,
                "EGRESS_BLOCKED",
                "delphi-assistant",
                "external_ai",
                "permission_slip_revoked_or_expired",
                hashlib.sha256(question.encode()).hexdigest(),
                {"blocked": True, "slip_id": slip_id},
            )
            set_led(conn, "RED", 6)
            conn.commit()
            return (
                jsonify(
                    {
                        "allowed": False,
                        "error": "BLOCKED: Permission Slip is revoked or expired. Zero bytes left.",
                    }
                ),
                403,
            )

        allowed_metrics = json.loads(slip["allowed_metrics"])
        insights, _ = analyze_insights(conn)

        # Data minimization: ONLY rounded summary numbers for allow-listed metrics leave The Gate
        approved_payload = {
            ins["metric"]: {
                "avg": round(ins["mean"]),
                "latest": round(ins["latest"]),
                "samples": ins["sample_count"],
                "anomaly": ins["anomaly"],
            }
            for ins in insights
            if ins["metric"] in allowed_metrics
        }

        if not approved_payload:
            approved_payload = {"note": "No live metrics recorded yet for allowed scopes"}

        payload_sha = hashlib.sha256(
            json.dumps(approved_payload, sort_keys=True).encode()
        ).hexdigest()
        entry_id, chain_hash = append_ledger(
            conn,
            "EGRESS_ALLOWED",
            slip["client"],
            "scoped_ai_summary",
            f"slip:{slip_id}",
            payload_sha,
            approved_payload,
        )
        set_led(conn, "AMBER", 5)
        conn.commit()

    # Build grounded, hallucination-proof answer strictly from approved_payload
    parts = []
    if "posture_score" in approved_payload:
        p = approved_payload["posture_score"]
        parts.append(
            f"Your posture averaged {p['avg']}% across {p['samples']} live checks (latest: {p['latest']}%)."
        )
    if "humidity_pct" in approved_payload:
        h = approved_payload["humidity_pct"]
        parts.append(f"Room humidity is at {h['latest']}% (avg {h['avg']}%).")
    if "temp_c" in approved_payload:
        t = approved_payload["temp_c"]
        parts.append(f"Room temperature is around {t['latest']} C.")
    if "focus_score" in approved_payload:
        f = approved_payload["focus_score"]
        parts.append(f"Screen focus averaged {f['avg']}%.")

    if not parts:
        answer = "Your permission slip was verified, and no allowed metrics have arrived yet. Start the laptop or ESP32 collector to stream real-time readings."
    else:
        answer = " ".join(parts) + " (Wellness trend only — generated strictly from rounded, slip-approved aggregates.)"

    return jsonify(
        {
            "allowed": True,
            "answer": answer,
            "outbound_payload": approved_payload,
            "ledger_entry_idx": entry_id,
            "chain_hash": chain_hash[:16] + "...",
            "led_state": "AMBER",
        }
    )


# --- Routes: ElevenLabs Zero-Retention Voice Nudge ---
@app.route("/nudge/speak", methods=["POST"])
def speak_nudge():
    body = request.get_json(silent=True) or {}
    nudge_key = str(body.get("type", "posture"))
    text = NUDGE_TEMPLATES.get(nudge_key, NUDGE_TEMPLATES["posture"])

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    mp3_name = f"nudge_{nudge_key}_{text_hash}.mp3"
    mp3_path = CACHE_DIR / mp3_name

    # Local cache hit -> ZERO outbound bytes
    if mp3_path.exists():
        return jsonify(
            {
                "status": "cached_local",
                "text": text,
                "audio_url": f"/audio/{mp3_name}",
                "bytes_out": 0,
            }
        )

    with get_db() as conn:
        if get_state(conn, "kill_switch", "0") == "1":
            return jsonify({"status": "fallback_browser_tts", "text": text, "reason": "Vault locked"})

        if not ELEVENLABS_API_KEY:
            return jsonify({"status": "fallback_browser_tts", "text": text, "reason": "No ElevenLabs key set"})

        # Outbound call to ElevenLabs with Zero Retention (enable_logging=false)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}?enable_logging=false"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {"text": text, "model_id": "eleven_flash_v2_5"}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=12)
            resp.raise_for_status()
            mp3_path.write_bytes(resp.content)

            append_ledger(
                conn,
                "EGRESS_ALLOWED",
                "delphi-nudge",
                "elevenlabs_tts_zero_retention",
                "generic_template_no_pii",
                hashlib.sha256(text.encode()).hexdigest(),
                {"template": nudge_key, "text": text, "enable_logging": False},
            )
            set_led(conn, "AMBER", 4)
            conn.commit()
            return jsonify(
                {
                    "status": "elevenlabs_generated",
                    "text": text,
                    "audio_url": f"/audio/{mp3_name}",
                    "bytes_out": len(text.encode("utf-8")),
                }
            )
        except Exception as e:
            return jsonify({"status": "fallback_browser_tts", "text": text, "error": str(e)})


@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(CACHE_DIR, filename)


# --- Routes: Hardware Kill Switch, ESP32 State & Ownership ---
@app.route("/esp32/state", methods=["GET"])
def esp32_state():
    with get_db() as conn:
        return current_led_color(conn), 200, {"Content-Type": "text/plain"}


@app.route("/revoke", methods=["POST"])
def revoke_all():
    body = request.get_json(silent=True) or {}
    actor = str(body.get("actor", "hardware_kill_switch"))
    with get_db() as conn:
        set_state(conn, "kill_switch", "1")
        set_led(conn, "RED", 3600)
        conn.execute("UPDATE permission_slips SET revoked = 1")
        append_ledger(
            conn,
            "KILL_SWITCH_TRIGGERED",
            actor,
            "local_vault",
            "instant_global_revoke",
            hashlib.sha256(f"revoke-{time.time()}".encode()).hexdigest(),
            {"action": "all_ai_access_cut", "actor": actor},
        )
        conn.commit()
    return jsonify({"status": "LOCKED", "led_state": "RED"})


@app.route("/unlock", methods=["POST"])
def unlock_vault():
    now = int(time.time())
    with get_db() as conn:
        set_state(conn, "kill_switch", "0")
        set_led(conn, "GREEN", 1)
        conn.execute(
            "UPDATE permission_slips SET revoked = 0, expires_at = ? WHERE slip_id = 'slip-demo'",
            (now + 7200,),
        )
        append_ledger(
            conn,
            "VAULT_UNLOCKED",
            "user",
            "local_vault",
            "manual_unlock",
            hashlib.sha256(f"unlock-{now}".encode()).hexdigest(),
            {"action": "vault_resumed"},
        )
        conn.commit()
    return jsonify({"status": "UNLOCKED", "led_state": "GREEN"})


@app.route("/forget", methods=["DELETE", "POST"])
def forget_metric():
    metric = request.args.get("metric") or (request.get_json(silent=True) or {}).get("metric")
    if not metric:
        return jsonify({"error": "Specify metric to forget"}), 400
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE telemetry SET value = NULL, payload_json = NULL, forgotten = 1 WHERE metric = ? AND forgotten = 0",
            (metric,),
        )
        append_ledger(
            conn,
            "RIGHT_TO_FORGET",
            "user",
            "local_vault",
            "erased_raw_values_chain_intact",
            hashlib.sha256(metric.encode()).hexdigest(),
            {"metric_erased": metric, "rows_wiped": cur.rowcount},
        )
        conn.commit()
    return jsonify({"status": "forgotten", "metric": metric, "rows_wiped": cur.rowcount})


# --- Routes: Tamper-Evident Verification & Demo Controls ---
@app.route("/verify", methods=["GET"])
def verify_chain():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM privacy_ledger ORDER BY idx ASC").fetchall()

    expected_prev = "0" * 64
    for r in rows:
        recomputed = compute_chain_hash(
            expected_prev,
            r["ts"],
            r["event_type"],
            r["actor"],
            r["destination"],
            r["payload_sha256"],
            r["outbound_json"],
        )
        if r["prev_hash"] != expected_prev or r["chain_hash"] != recomputed:
            return jsonify(
                {
                    "valid": False,
                    "checked_entries": len(rows),
                    "tampered_at_idx": r["idx"],
                    "message": f"TAMPERING DETECTED at ledger entry #{r['idx']}!",
                }
            )
        expected_prev = r["chain_hash"]

    return jsonify(
        {
            "valid": True,
            "checked_entries": len(rows),
            "tampered_at_idx": None,
            "head_hash": expected_prev,
            "message": f"All {len(rows)} ledger entries verified intact.",
        }
    )


@app.route("/export/ledger", methods=["GET"])
def export_ledger():
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM privacy_ledger ORDER BY idx ASC").fetchall()]
    return jsonify({"exported_at": int(time.time()), "entries": rows})


@app.route("/admin/simulate_tamper", methods=["POST"])
def simulate_tamper():
    with get_db() as conn:
        row = conn.execute(
            "SELECT idx, outbound_json FROM privacy_ledger ORDER BY idx DESC LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify({"error": "No ledger entries to tamper with yet"}), 400
        conn.execute(
            "UPDATE privacy_ledger SET outbound_json = ? WHERE idx = ?",
            ('{"TAMPERED_BY_ATTACKER":true}', row["idx"]),
        )
        conn.commit()
    return jsonify({"status": "tampered", "target_idx": row["idx"]})


@app.route("/admin/reset_demo", methods=["POST"])
def reset_demo():
    with get_db() as conn:
        conn.executescript(
            """
            DELETE FROM telemetry;
            DELETE FROM device_seq;
            DELETE FROM privacy_ledger;
            DELETE FROM sqlite_sequence;
            """
        )
        set_state(conn, "kill_switch", "0")
        set_led(conn, "GREEN", 1)
        now = int(time.time())
        conn.execute(
            "UPDATE permission_slips SET revoked = 0, expires_at = ? WHERE slip_id = 'slip-demo'",
            (now + 7200,),
        )
        conn.commit()
    return jsonify({"status": "demo_reset_clean"})


# --- Routes: Live Dashboard Status & Static UI ---
@app.route("/status", methods=["GET"])
def status():
    with get_db() as conn:
        insights, alerts = analyze_insights(conn)
        ledger = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM privacy_ledger ORDER BY idx DESC LIMIT 25"
            ).fetchall()
        ]
        slips = []
        for r in conn.execute("SELECT * FROM permission_slips").fetchall():
            d = dict(r)
            d["allowed_metrics"] = json.loads(d["allowed_metrics"])
            slips.append(d)

        total_bytes_out = conn.execute(
            "SELECT COALESCE(SUM(bytes_out), 0) AS b FROM privacy_ledger"
        ).fetchone()["b"]
        total_readings = conn.execute(
            "SELECT COUNT(*) AS c FROM telemetry WHERE forgotten = 0"
        ).fetchone()["c"]

        return jsonify(
            {
                "led_state": current_led_color(conn),
                "kill_switch": get_state(conn, "kill_switch", "0") == "1",
                "total_readings": total_readings,
                "total_bytes_out": total_bytes_out,
                "insights": insights,
                "alerts": alerts,
                "slips": slips,
                "ledger": ledger,
            }
        )


@app.route("/")
def serve_dashboard():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.route("/verify-ui")
def serve_verify_ui():
    return send_from_directory(DASHBOARD_DIR, "verify.html")


if __name__ == "__main__":
    init_db()
    print("\n=== DELPHI TIER-1 VAULT ONLINE ===")
    print("Dashboard : http://127.0.0.1:5000")
    print("ESP32 Poll: http://<ESP32_IP>:5000/esp32/state")  #leaving esp32 ip as blank still yet to be implemented.
    print("Verify API: http://127.0.0.1:5000/verify\n")
    app.run(host="0.0.0.0", port=5000, debug=False)