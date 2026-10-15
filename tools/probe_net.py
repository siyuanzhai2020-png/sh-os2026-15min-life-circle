"""Probe outbound HTTPS reachability for the data sources this project needs."""
import json
import socket
import sys
import time

import requests

TARGETS = [
    ("pypi", "https://pypi.org/simple/"),
    ("overpass-main", "https://overpass-api.de/api/status"),
    ("overpass-kumi", "https://overpass.kumi.systems/api/status"),
    ("overpass-ru", "https://overpass.openstreetmap.ru/api/status"),
    ("baidu-apimap", "https://api.map.baidu.com/"),
    ("osm-tile", "https://tile.openstreetmap.org/0/0/0.png"),
]

results = {}
for name, url in TARGETS:
    t0 = time.time()
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "lifecycle-demo-probe/0.1"})
        results[name] = {"ok": True, "status": r.status_code, "bytes": len(r.content),
                         "ms": int((time.time() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                         "ms": int((time.time() - t0) * 1000)}

# DNS sanity check
for host in ["overpass-api.de", "api.map.baidu.com", "pypi.org"]:
    try:
        results.setdefault("dns", {})[host] = socket.gethostbyname(host)
    except Exception as exc:  # noqa: BLE001
        results.setdefault("dns", {})[host] = f"FAIL {exc}"

print(json.dumps(results, indent=2, ensure_ascii=False))
sys.exit(0 if results.get("overpass-main", {}).get("ok") or results.get("overpass-kumi", {}).get("ok") else 2)
