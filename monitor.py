import os
import platform
import re
import socket
import ssl
import subprocess
import threading
import time
from datetime import datetime, timezone

import requests

import models

CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "30"))
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
PUSH_STALE_SEC = int(os.environ.get("PUSH_STALE_SEC", "90"))

FLAP_WINDOW = 10       # how many recent checks to look at
FLAP_THRESHOLD = 3     # state flips within the window counts as "flapping"
LATENCY_MULTIPLIER = 2.0
LATENCY_MIN_BASELINE_MS = 5.0
SSL_WARN_DAYS = 30

# Matches "...=15ms" or "...<1ms" regardless of the preceding word - Windows
# localizes "time" (e.g. Korean "시간"), but the "=NNms" part stays ASCII.
_LATENCY_RE = re.compile(r"[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_last_state = {}           # device_id -> True(up) / False(down)
_flapping_state = {}       # device_id -> bool (currently flapping)
_latency_alert_state = {}  # device_id -> bool (currently anomalous)
_ssl_days_left = {}        # device_id -> int days remaining (or None)
_ssl_alerted = set()       # device_ids already alerted for the current low-days episode


def _tcp_probe(ip, timeout_sec=2, ports=(443, 80, 22, 53)):
    """Fallback reachability check via TCP connect - works even where raw
    ICMP is blocked (common on PaaS containers like Render/Heroku)."""
    for port in ports:
        start = time.monotonic()
        try:
            with socket.create_connection((ip, port), timeout=timeout_sec):
                return True, round((time.monotonic() - start) * 1000, 1)
        except OSError:
            continue
    return False, None


def ping_once(ip, timeout_sec=2):
    """Real ICMP ping via the OS ping command, falling back to a TCP connect
    probe when ICMP is unavailable or blocked. Returns (is_up, latency_ms)."""
    if platform.system().lower() == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_sec * 1000), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout_sec), ip]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec + 2)
    except (subprocess.TimeoutExpired, OSError):
        return _tcp_probe(ip, timeout_sec)

    if result.returncode != 0:
        return _tcp_probe(ip, timeout_sec)

    match = _LATENCY_RE.search(result.stdout)
    latency = float(match.group(1)) if match else None
    return True, latency


def push_is_up(last_push_at):
    """Push/heartbeat devices: up if an agent checked in within PUSH_STALE_SEC."""
    if not last_push_at:
        return False
    last = datetime.strptime(last_push_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age <= PUSH_STALE_SEC


def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
    except requests.RequestException as exc:
        print(f"[monitor] discord alert failed: {exc}")


def _count_transitions(is_up_sequence):
    """is_up_sequence: most-recent-first 0/1 values. Counts UP<->DOWN flips."""
    return sum(1 for a, b in zip(is_up_sequence, is_up_sequence[1:]) if a != b)


def _check_flapping(device_id):
    checks = models.recent_checks(device_id, limit=FLAP_WINDOW)
    return _count_transitions([c["is_up"] for c in checks]) >= FLAP_THRESHOLD


def _check_latency_anomaly(device_id, current_latency):
    if current_latency is None:
        return False
    history = models.recent_checks(device_id, limit=10)
    latencies = [c["latency_ms"] for c in history if c["latency_ms"] is not None]
    if len(latencies) < 3:
        return False
    baseline = sum(latencies) / len(latencies)
    return baseline >= LATENCY_MIN_BASELINE_MS and current_latency > baseline * LATENCY_MULTIPLIER


def check_ssl_cert(hostname, port=443, timeout_sec=5):
    """Returns days remaining until cert expiry, or None if the check failed."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout_sec) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        expires_at = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return (expires_at - now_utc).days
    except (OSError, ssl.SSLError, ValueError, KeyError):
        return None


def is_flapping(device_id):
    return _flapping_state.get(device_id, False)


def is_latency_anomaly(device_id):
    return _latency_alert_state.get(device_id, False)


def ssl_days_left(device_id):
    return _ssl_days_left.get(device_id)


def check_all_devices():
    under_maintenance = models.devices_under_maintenance()

    for device in models.list_devices():
        device_id = device["id"]
        if device["check_type"] == "push":
            is_up, latency = push_is_up(device["last_push_at"]), None
        else:
            is_up, latency = ping_once(device["ip"])
        models.record_status(device_id, is_up, latency)

        previous = _last_state.get(device_id)
        _last_state[device_id] = is_up

        flapping_now = _check_flapping(device_id)
        was_flapping = _flapping_state.get(device_id, False)
        _flapping_state[device_id] = flapping_now

        anomaly_now = _check_latency_anomaly(device_id, latency)
        was_anomaly = _latency_alert_state.get(device_id, False)
        _latency_alert_state[device_id] = anomaly_now

        if device["ssl_host"]:
            days_left = check_ssl_cert(device["ssl_host"])
            _ssl_days_left[device_id] = days_left
            if days_left is not None:
                if days_left <= SSL_WARN_DAYS and device_id not in _ssl_alerted:
                    send_discord_alert(
                        f"\U0001F510 SSL 만료임박: {device['name']} ({device['ssl_host']}) D-{days_left}"
                    )
                    _ssl_alerted.add(device_id)
                elif days_left > SSL_WARN_DAYS:
                    _ssl_alerted.discard(device_id)

        if device_id in under_maintenance:
            continue  # planned work - don't alert

        if previous is True and is_up is False:
            send_discord_alert(f"\U0001F534 DOWN: {device['name']} ({device['ip']})")
        elif previous is False and is_up is True:
            send_discord_alert(f"\U0001F7E2 RECOVERED: {device['name']} ({device['ip']})")

        if flapping_now and not was_flapping:
            send_discord_alert(f"\U0001F7E1 UNSTABLE (flapping): {device['name']} ({device['ip']})")

        if anomaly_now and not was_anomaly:
            send_discord_alert(f"\U0001F40C SLOW: {device['name']} ({device['ip']}) 응답지연 급증")


def start_background_monitor():
    def loop():
        while True:
            try:
                check_all_devices()
            except Exception as exc:
                print(f"[monitor] check cycle failed: {exc}")
            time.sleep(CHECK_INTERVAL_SEC)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
