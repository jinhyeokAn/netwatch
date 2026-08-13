"""NetWatch heartbeat agent. Run this on a machine behind NAT/CGNAT that
the NetWatch server can't reach directly (home PC, phone via Termux, etc).
No external dependencies - just Python 3.

Add each device's check-in URL below (copy from its edit page in NetWatch),
then run:
    python heartbeat.py
"""

import time
import urllib.request

PUSH_URLS = [
    "https://netwatch-aqbz.onrender.com/push/-LxzexKF4u8YchNmDjE4JQ",  # 집컴퓨터 = 이 PC
]
INTERVAL_SEC = 30

while True:
    for url in PUSH_URLS:
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10)
            print(f"checked in: {url}")
        except Exception as exc:
            print(f"check-in failed ({url}): {exc}")
    time.sleep(INTERVAL_SEC)
