"""诊断浏览器端地图为什么加载失败：JSAPI 与静态图 API 分别探测。

    python tools/basemap_diag.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from lifecycle_metrics.env import baidu_credentials, load_env_file  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def probe_jsapi(ak: str, referer: str | None) -> dict:
    url = f"https://api.map.baidu.com/api?type=webgl&v=1.0&ak={ak}&callback=cb"
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=20)
    body = r.text[:400]
    return {"status": r.status_code, "bytes": len(r.content),
            "content_type": r.headers.get("content-type"),
            "looks_like_js": "BMapGL" in r.text or "bmap" in r.text.lower(),
            "body_head": body.replace("\n", " ")[:260]}


def probe_static(ak: str) -> dict:
    url = "https://api.map.baidu.com/staticimage/v2"
    params = {"ak": ak, "center": "121.41682,31.24793", "width": 512, "height": 512,
              "zoom": 16, "copyright": 0}
    r = requests.get(url, params=params, timeout=25)
    head = r.content[:4]
    is_png = head == b"\x89PNG"
    out = {"status": r.status_code, "bytes": len(r.content), "is_png": is_png,
           "content_type": r.headers.get("content-type")}
    if not is_png:
        try:
            out["json"] = r.json()
        except Exception:  # noqa: BLE001
            out["body_head"] = r.text[:200]
    return out


def main() -> int:
    load_env_file()
    creds = baidu_credentials()
    web_ak, server_ak = creds["web_ak"], creds["ak"]
    out = {
        "web_ak_suffix": (web_ak or "")[-6:],
        "server_ak_suffix": (server_ak or "")[-6:],
        "jsapi_with_referer_localhost": probe_jsapi(web_ak, "http://127.0.0.1:8000/") if web_ak else None,
        "jsapi_without_referer": probe_jsapi(web_ak, None) if web_ak else None,
        "static_api_server_ak": probe_static(server_ak) if server_ak else None,
        "static_api_web_ak": probe_static(web_ak) if web_ak else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
