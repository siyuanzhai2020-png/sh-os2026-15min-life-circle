"""百度静态底图的真实分辨率标定（权威口径）。

背景（这是本项目最容易踩的坑）：
    标准 Web Mercator 的每像素米数是 156543.03·cos(lat)/2^zoom，
    但**百度地图不是这个数**。实测百度 zoom 14–18 的分辨率为
        13.679 / 6.839 / 3.420 / 1.710 / 0.855  (lat 31.248)
    反推 mpp = 262300·cos(lat)/2^zoom（C=262300，与标准值差 1.675 倍）。
    用错会让静态底图被整体缩放：中心看着对得上，越往边缘越偏 —— 表现为"路网和底图错位"。

做法：起一个本地页面，让百度 JSAPI 自己报 getBounds 与 pointToPixel，
两条独立口径互相校验（另有 getDistance 做距离校验），结果 POST 回 /api/probe 落盘。

    python tools/basemap_scale_check.py [community_id]     # 需要服务已启动

也保留了一条不依赖浏览器的互相关测法（--mad），但会被地图标签重排干扰，仅供参考。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.basemap import BAIDU_MERCATOR_CONST, baidu_mpp, mercator_mpp  # noqa: E402
from lifecycle_metrics.config import load_communities, repo_path, resolve_community  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
PROBE_CACHE = repo_path("data") / "cache"


def find_browser() -> str | None:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def probe_via_jsapi(base_url: str, zoom: int) -> dict | None:
    """让 headless 浏览器打开 scale_probe.html，页面把结果 POST 回服务端。"""
    browser = find_browser()
    if not browser:
        return None
    for f in glob.glob(str(PROBE_CACHE / "probe-*.json")):
        Path(f).unlink()
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--virtual-time-budget=25000",
                    "--window-size=1200,1300",
                    "--screenshot=" + str(repo_path("out") / "_scale_probe.png"),
                    f"{base_url}/scale_probe.html?zoom={zoom}"],
                   capture_output=True, timeout=180)
    time.sleep(1.0)
    files = sorted(glob.glob(str(PROBE_CACHE / "probe-*.json")))
    if not files:
        return None
    return json.loads(Path(files[-1]).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="百度底图分辨率标定")
    ap.add_argument("community_id", nargs="?", default=None)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--zooms", default="14,15,16,17,18")
    ap.add_argument("--no-browser", action="store_true", help="只打印已标定的常数表")
    args = ap.parse_args(argv)

    community = resolve_community(load_communities(), args.community_id)
    c = community["center"]
    zooms = [int(z) for z in args.zooms.split(",") if z.strip()]

    out: dict = {"community": community["id"], "center_wgs84": [c["lon"], c["lat"]],
                 "constant_used": BAIDU_MERCATOR_CONST, "measurements": [],
                 "reference": {"web_mercator_constant": 156543.03392,
                               "ratio_baidu_over_mercator": round(BAIDU_MERCATOR_CONST / 156543.03392, 4)}}

    if not args.no_browser:
        for z in zooms:
            r = probe_via_jsapi(args.base_url, z)
            if not r:
                out["measurements"].append({"zoom": z, "error": "浏览器探测失败（服务未启动或无浏览器）"})
                continue
            mpp_bounds = r.get("mpp_from_bounds") or {}
            mpp_x = mpp_bounds.get("x") or r.get("measured_mpp_x")
            out["measurements"].append({
                "zoom": z, "mpp_x": round(mpp_x, 4) if mpp_x else None,
                "mpp_from_pointToPixel": round(r.get("measured_mpp_x", 0), 4),
                "ratio_vs_web_mercator": round(r.get("ratio_x", 0), 4),
                "implied_constant": round(mpp_x * (2 ** z) / math.cos(math.radians(c["lat"])), 0)
                if mpp_x else None,
                "get_distance_check_m": {k: round(v, 1) for k, v in (r.get("get_distance") or {}).items()
                                         if isinstance(v, (int, float))},
            })

    table = [{"zoom": z, "baidu_mpp": round(baidu_mpp(c["lat"], z), 4),
              "web_mercator_mpp": round(mercator_mpp(c["lat"], z), 4)} for z in zooms]
    out["table"] = table
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
