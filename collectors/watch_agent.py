import asyncio
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from bleak import BleakClient, BleakScanner

HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

INGEST_URL = os.getenv("DELPHI_INGEST_URL", "http://127.0.0.1:8000/ingest")
HMAC_SECRET = os.getenv("DELPHI_HMAC_SECRET", "dev-secret-change-me")
DEVICE_ID = os.getenv("DELPHI_DEVICE_ID", "samsung-galaxy-watch")
SCAN_TIMEOUT = 8.0
RECONNECT_DELAY = 3.0


def parse_hr_measurement(data: bytearray) -> tuple[int, list[float], str]:
    flags = data[0]
    is_16bit = bool(flags & 0x01)
    contact_bits = (flags >> 1) & 0x03
    energy_present = bool(flags & 0x08)
    rr_present = bool(flags & 0x10)

    idx = 1
    if is_16bit:
        bpm = int.from_bytes(data[idx : idx + 2], byteorder="little")
        idx += 2
    else:
        bpm = data[idx]
        idx += 1

    if energy_present:
        idx += 2

    rr_ms: list[float] = []
    if rr_present:
        while idx + 1 < len(data):
            raw_rr = int.from_bytes(data[idx : idx + 2], byteorder="little")
            rr_ms.append(round(raw_rr * 1000.0 / 1024.0, 1))
            idx += 2

    contact_map = {
        0: "unsupported",
        1: "unsupported",
        2: "no_contact",
        3: "contact_ok",
    }
    return bpm, rr_ms, contact_map[contact_bits]


def post_signed_sync(payload: dict) -> tuple[bool, str]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(HMAC_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        INGEST_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Delphi-Signature": f"sha256={sig}",
            "X-Delphi-Device": DEVICE_ID,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


async def ingest_worker(queue: asyncio.Queue[dict]) -> None:
    while True:
        payload = await queue.get()
        try:
            ok, detail = await asyncio.to_thread(post_signed_sync, payload)
            tag = "SENT" if ok else "POST_ERR"
            print(
                f"[{payload['ts_iso'][11:19]}] HR={payload['bpm']:>3} BPM "
                f"({payload['contact']}) -> {tag}: {detail}"
            )
        finally:
            queue.task_done()


async def find_hr_watch():
    discovered = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
    ranked = sorted(discovered.values(), key=lambda x: x[1].rssi, reverse=True)
    for device, adv in ranked:
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if HR_SERVICE_UUID in uuids:
            return device, adv.rssi
    return None, None


async def run_collector() -> None:
    if HMAC_SECRET == "dev-secret-change-me":
        print("Warning: using default dev HMAC secret. Set DELPHI_HMAC_SECRET before demo.")
    print(f"Target ingest endpoint: {INGEST_URL}")

    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    worker = asyncio.create_task(ingest_worker(queue))
    loop = asyncio.get_running_loop()

    try:
        while True:
            print(f"Scanning for BLE Heart Rate Service (0x180D)...")
            target, rssi = await find_hr_watch()
            if target is None:
                print(f"No 0x180D watch found. Retrying in {RECONNECT_DELAY:.0f}s...")
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            disconnect_event = asyncio.Event()

            def on_disconnect(_client) -> None:
                loop.call_soon_threadsafe(disconnect_event.set)

            def on_hr_notification(_sender, data: bytearray) -> None:
                bpm, rr_ms, contact = parse_hr_measurement(data)
                if bpm <= 0 or bpm > 240:
                    return

                now = datetime.now(timezone.utc)
                payload = {
                    "device_id": DEVICE_ID,
                    "source": "ble_0x180d",
                    "ts_unix": round(time.time(), 3),
                    "ts_iso": now.isoformat(),
                    "bpm": bpm,
                    "rr_ms": rr_ms,
                    "contact": contact,
                }
                if queue.full():
                    try:
                        queue.get_nowait()
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(payload)

            print(f"Found {target.name or 'Watch'} ({target.address}, {rssi} dBm). Connecting...")
            try:
                async with BleakClient(
                    target,
                    timeout=20.0,
                    disconnected_callback=on_disconnect,
                ) as client:
                    print("Connected. Streaming non-zero BPM (Ctrl+C to stop)...")
                    await client.start_notify(HR_MEASUREMENT_UUID, on_hr_notification)
                    await disconnect_event.wait()
                    print("Watch disconnected. Re-scanning...")
            except Exception as e:
                print(f"BLE session error: {e}. Retrying in {RECONNECT_DELAY:.0f}s...")
                await asyncio.sleep(RECONNECT_DELAY)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        print("\nCollector stopped.")
