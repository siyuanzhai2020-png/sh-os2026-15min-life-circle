"""诊断百度检索：地理编码定位社区中心 + 检验各要素检索词的实际返回。

    python tools/baidu_diag.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.env import baidu_credentials, load_env_file   # noqa: E402
from lifecycle_metrics.providers.baidu import (BAIDU_KEYWORDS, _place_search,  # noqa: E402
                                              map_baidu_tags)

QUERIES = ["公交站", "地铁站", "社区卫生服务中心", "社区卫生服务站", "菜市场",
           "社区食堂", "公园", "社区文化活动中心", "小学", "幼儿园", "药店", "便利店"]


def geocode(ak: str, address: str) -> dict:
    import requests
    r = requests.get("https://api.map.baidu.com/geocoding/v3/",
                     params={"address": address, "output": "json", "ak": ak}, timeout=15)
    return r.json()


def main() -> int:
    load_env_file()
    creds = baidu_credentials()
    ak = creds["ak"]
    if not ak:
        print("缺少服务端 AK")
        return 2

    out: dict = {"geocode": {}, "place_search": {}}
    for addr in ["上海市普陀区曹杨新村街道办事处", "上海曹杨公园", "上海市普陀区曹杨新村街道"]:
        g = geocode(ak, addr)
        loc = (g.get("result") or {}).get("location") or {}
        out["geocode"][addr] = {"status": g.get("status"), "lat": loc.get("lat"),
                                "lng": loc.get("lng"),
                                "confidence": (g.get("result") or {}).get("confidence"),
                                "level": (g.get("result") or {}).get("level")}

    # 以曹杨公园为圆心检验各检索词（半径 1000 m）
    center = out["geocode"].get("上海曹杨公园") or {}
    lat = center.get("lat") or 31.2465
    lon = center.get("lng") or 121.3955
    for q in QUERIES:
        try:
            data = _place_search(ak, q, (lat, lon), 1000, page_num=0, sk=creds["sk"],
                                 cache_dir=ROOT / "data" / "cache-diag")
            results = data.get("results", []) or []
            mapped = {}
            for r in results:
                key = map_baidu_tags(r.get("name", ""), r.get("tag", ""), r.get("type", ""))
                mapped[key or "(未归类)"] = mapped.get(key or "(未归类)", 0) + 1
            out["place_search"][q] = {
                "status": data.get("status"), "count": len(results),
                "mapped": mapped,
                "samples": [{"name": r.get("name"), "type": r.get("type"),
                             "tag": r.get("tag")} for r in results[:3]],
            }
        except Exception as exc:  # noqa: BLE001
            out["place_search"][q] = {"error": f"{type(exc).__name__}: {exc}"[:160]}

    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
