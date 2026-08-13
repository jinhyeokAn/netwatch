"""NetWatch heartbeat agent. Run this on a machine behind NAT/CGNAT that
the NetWatch server can't reach directly (home PC, phone via Termux, etc).
No external dependencies - just Python 3.

Set PUSH_URL below (copy from the device's edit page in NetWatch), then run:
    python heartbeat.py
"""

import time
import urllib.error
import urllib.request

PUSH_URL = "https://netwatch-aqbz.onrender.com/push/cqBTZhmOe7Slf9eX0035yw"
INTERVAL_SEC = 30

while True:
    try:
        urllib.request.urlopen(urllib.request.Request(PUSH_URL, method="POST"), timeout=5)
        print("checked in")
    except urllib.error.URLError as exc:
        print(f"check-in failed: {exc}")
    time.sleep(INTERVAL_SEC)
