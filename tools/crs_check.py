"""坐标系统核验：百度（BD-09）与 OSM（WGS-84）在同一地物上的偏差。

    python tools/crs_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from lifecycle_metrics.env import baidu_credentials, load_env_file  # noqa: E402
from lifecycle_metrics.geo import haversine_m  # noqa: E402

NAMES = ["曹杨公园", "兰溪青年公园", "武宁公园", "曹杨新村街道办事处"]
OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:60];
(
  way["name"~"曹杨公园|兰溪青年公园|武宁公园"](31.2300,121.3950,31.2600,121.4350);
  node["name"~"曹杨公园|兰溪青年公园|武宁公园"](31.2300,121.3950,31.2600,121.4350);
);
out center tags;
"""


def main() -> int:
    load_env_file()
    ak = baidu_credentials()["ak"]
    out: dict = {"osm": {}, "baidu": {}, "delta_m": {}}

    r = requests.post(OVERPASS, data={"data": QUERY}, timeout=90,
                      headers={"User-Agent": "lifecycle-metrics/0.1 (crs check)"})
    for el in r.json().get("elements", []):
        name = (el.get("tags") or {}).get("name")
        if not name:
            continue
        if el.get("type") == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            c = el.get("center") or {}
            lon, lat = c.get("lon"), c.get("lat")
        if lon is not None:
            out["osm"][name] = [round(lon, 6), round(lat, 6)]

    for name in NAMES:
        g = requests.get("https://api.map.baidu.com/geocoding/v3/",
                         params={"address": f"上海市普陀区{name}", "output": "json", "ak": ak},
                         timeout=20).json()
        loc = (g.get("result") or {}).get("location") or {}
        if loc:
            out["baidu"][name] = [round(loc["lng"], 6), round(loc["lat"], 6)]

    for name in set(out["osm"]) & set(out["baidu"]):
        b, o = out["baidu"][name], out["osm"][name]
        out["delta_m"][name] = {
            "distance_m": round(haversine_m(b[0], b[1], o[0], o[1]), 1),
            "dlon_m": round((b[0] - o[0]) * 111320 * 0.854, 1),
            "dlat_m": round((b[1] - o[1]) * 111320, 1),
        }

    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("\n结论：" + ("百度与 OSM 存在系统性偏移 → 必须做 BD-09 ↔ WGS-84 转换"
                      if any(v["distance_m"] > 150 for v in out["delta_m"].values())
                      else "未发现明显偏移（可能百度 API 已返回 WGS-84）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
