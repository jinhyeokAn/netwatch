import secrets
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "netwatch.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ip TEXT NOT NULL UNIQUE,
            subnet TEXT,
            location TEXT,
            owner TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS status_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_up INTEGER NOT NULL,
            latency_ms REAL
        );

        CREATE TABLE IF NOT EXISTS maintenance_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()

    # Migrations for columns added after the initial release - safe to
    # re-run, SQLite has no "ADD COLUMN IF NOT EXISTS" so we swallow the
    # duplicate-column error.
    try:
        conn.execute("ALTER TABLE devices ADD COLUMN ssl_host TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    for column, ddl in (
        ("check_type", "ALTER TABLE devices ADD COLUMN check_type TEXT DEFAULT 'ping'"),
        ("push_token", "ALTER TABLE devices ADD COLUMN push_token TEXT"),
        ("last_push_at", "ALTER TABLE devices ADD COLUMN last_push_at TEXT"),
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    conn.close()


# ---------- devices ----------

def list_devices():
    conn = get_db()
    rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
    conn.close()
    return rows


def add_device(name, ip, subnet, location, owner, ssl_host="", check_type="ping"):
    push_token = secrets.token_urlsafe(16) if check_type == "push" else None
    conn = get_db()
    conn.execute(
        "INSERT INTO devices (name, ip, subnet, location, owner, ssl_host, check_type, push_token) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, ip, subnet, location, owner, ssl_host or None, check_type, push_token),
    )
    conn.commit()
    conn.close()


def get_device(device_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    conn.close()
    return row


def get_device_by_token(token):
    conn = get_db()
    row = conn.execute("SELECT * FROM devices WHERE push_token = ?", (token,)).fetchone()
    conn.close()
    return row


def touch_push(token):
    """Records a heartbeat check-in from an agent. Returns the device row, or None if token is invalid."""
    conn = get_db()
    conn.execute(
        "UPDATE devices SET last_push_at = CURRENT_TIMESTAMP WHERE push_token = ?",
        (token,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM devices WHERE push_token = ?", (token,)).fetchone()
    conn.close()
    return row


def update_device(device_id, name, ip, subnet, location, owner, ssl_host="", check_type="ping"):
    conn = get_db()
    push_token = conn.execute(
        "SELECT push_token FROM devices WHERE id = ?", (device_id,)
    ).fetchone()["push_token"]
    if check_type == "push" and not push_token:
        push_token = secrets.token_urlsafe(16)
    elif check_type != "push":
        push_token = None
    conn.execute(
        "UPDATE devices SET name = ?, ip = ?, subnet = ?, location = ?, owner = ?, ssl_host = ?, "
        "check_type = ?, push_token = ? WHERE id = ?",
        (name, ip, subnet, location, owner, ssl_host or None, check_type, push_token, device_id),
    )
    conn.commit()
    conn.close()


def delete_device(device_id):
    conn = get_db()
    conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    conn.commit()
    conn.close()


# ---------- status checks ----------

def record_status(device_id, is_up, latency_ms):
    conn = get_db()
    conn.execute(
        "INSERT INTO status_checks (device_id, is_up, latency_ms) VALUES (?, ?, ?)",
        (device_id, int(is_up), latency_ms),
    )
    conn.commit()
    conn.close()


def latest_status_by_device():
    """Returns {device_id: last status_checks row} for the most recent check per device."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT sc.* FROM status_checks sc
        INNER JOIN (
            SELECT device_id, MAX(id) AS max_id FROM status_checks GROUP BY device_id
        ) latest ON sc.device_id = latest.device_id AND sc.id = latest.max_id
        """
    ).fetchall()
    conn.close()
    return {row["device_id"]: row for row in rows}


def recent_latency(device_id, limit=20):
    conn = get_db()
    rows = conn.execute(
        "SELECT checked_at, latency_ms FROM status_checks WHERE device_id = ? ORDER BY id DESC LIMIT ?",
        (device_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed(rows))


def recent_checks(device_id, limit=10):
    """Most-recent-first is_up/latency_ms rows - used for flapping/anomaly detection."""
    conn = get_db()
    rows = conn.execute(
        "SELECT is_up, latency_ms FROM status_checks WHERE device_id = ? ORDER BY id DESC LIMIT ?",
        (device_id, limit),
    ).fetchall()
    conn.close()
    return rows


def uptime_stats(days=30):
    """Per-device uptime% and MTTR (mean time to recovery) over the window."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    stats = []
    for d in conn.execute("SELECT * FROM devices ORDER BY id").fetchall():
        checks = conn.execute(
            "SELECT is_up, checked_at FROM status_checks WHERE device_id = ? AND checked_at >= ? ORDER BY id",
            (d["id"], since),
        ).fetchall()

        total = len(checks)
        up = sum(1 for c in checks if c["is_up"])
        uptime_pct = round(up / total * 100, 2) if total else None

        durations = []
        down_start = None
        for c in checks:
            ts = datetime.fromisoformat(c["checked_at"])
            if c["is_up"] == 0 and down_start is None:
                down_start = ts
            elif c["is_up"] == 1 and down_start is not None:
                durations.append((ts - down_start).total_seconds() / 60)
                down_start = None
        incident_count = len(durations) + (1 if down_start is not None else 0)
        mttr_minutes = round(sum(durations) / len(durations), 1) if durations else None

        stats.append(
            {
                "device": d,
                "total_checks": total,
                "uptime_pct": uptime_pct,
                "incident_count": incident_count,
                "mttr_minutes": mttr_minutes,
            }
        )
    conn.close()
    return stats


def incident_history(limit=50, search=None):
    """DOWN events, most recent first. `search` filters by device name/IP substring."""
    conn = get_db()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            """
            SELECT sc.checked_at, d.name, d.ip
            FROM status_checks sc
            JOIN devices d ON d.id = sc.device_id
            WHERE sc.is_up = 0 AND (d.name LIKE ? OR d.ip LIKE ?)
            ORDER BY sc.id DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT sc.checked_at, d.name, d.ip
            FROM status_checks sc
            JOIN devices d ON d.id = sc.device_id
            WHERE sc.is_up = 0
            ORDER BY sc.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conn.close()
    return rows


# ---------- maintenance windows ----------

def list_maintenance():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT mw.*, d.name AS device_name
        FROM maintenance_windows mw
        JOIN devices d ON d.id = mw.device_id
        ORDER BY mw.start_at DESC
        """
    ).fetchall()
    conn.close()
    return rows


def add_maintenance(device_id, title, start_at, end_at, note):
    conn = get_db()
    conn.execute(
        "INSERT INTO maintenance_windows (device_id, title, start_at, end_at, note) VALUES (?, ?, ?, ?, ?)",
        (device_id, title, start_at, end_at, note),
    )
    conn.commit()
    conn.close()


def get_maintenance(window_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM maintenance_windows WHERE id = ?", (window_id,)).fetchone()
    conn.close()
    return row


def update_maintenance(window_id, device_id, title, start_at, end_at, note):
    conn = get_db()
    conn.execute(
        "UPDATE maintenance_windows SET device_id = ?, title = ?, start_at = ?, end_at = ?, note = ? WHERE id = ?",
        (device_id, title, start_at, end_at, note, window_id),
    )
    conn.commit()
    conn.close()


# ---------- settings ----------

def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------- users / audit log ----------

def ensure_default_admin(username, password_hash):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    if count == 0:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def create_user(username, password_hash):
    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    conn.close()


def list_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return rows


def count_users():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    conn.close()
    return count


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def log_action(username, action, detail=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (username, action, detail) VALUES (?, ?, ?)",
        (username, action, detail),
    )
    conn.commit()
    conn.close()


def list_audit_log(limit=100):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


def devices_under_maintenance():
    """Returns set of device_ids currently inside a maintenance window."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    rows = conn.execute(
        "SELECT device_id FROM maintenance_windows WHERE start_at <= ? AND end_at >= ?",
        (now, now),
    ).fetchall()
    conn.close()
    return {row["device_id"] for row in rows}
