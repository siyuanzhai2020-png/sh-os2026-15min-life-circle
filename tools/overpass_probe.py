"""Overpass 镜像探测：每个镜像发一个极小查询，报告延迟与可用性。

    python tools/overpass_probe.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
QUERY = '[out:json][timeout:20];way["highway"](31.2470,121.4150,31.2480,121.4160);out count;'


def main() -> int:
    out = {}
    best = None
    for url in MIRRORS:
        t0 = time.time()
        try:
            r = requests.post(url, data={"data": QUERY}, timeout=(8, 30),
                              headers={"User-Agent": "lifecycle-metrics/0.1 (probe)"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ms = int((time.time() - t0) * 1000)
            ok = r.status_code == 200 and "elements" in body
            out[url] = {"ok": ok, "status": r.status_code, "ms": ms}
            if ok and best is None:
                best = url
        except Exception as exc:  # noqa: BLE001
            out[url] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120],
                        "ms": int((time.time() - t0) * 1000)}
        print(f"{'OK ' if out[url].get('ok') else 'FAIL'} {url} {json.dumps(out[url], ensure_ascii=False)}",
              flush=True)
    print(json.dumps({"best": best, "hint": f"OVERPASS_URL={best}" if best else "无可用镜像"},
                     ensure_ascii=False))
    return 0 if best else 2


if __name__ == "__main__":
    raise SystemExit(main())
