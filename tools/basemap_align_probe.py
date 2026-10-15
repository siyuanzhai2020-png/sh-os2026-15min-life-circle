"""底图对齐核验（不依赖浏览器 JSAPI 的绝对口径）。

回答两个问题，都是"错位/对不上"排查的第一步：

1. **比例尺对不对** —— 同一 zoom 下请求两张中心相差 Δ 经度（或纬度）的静态图，
   FFT 相位相关求像素位移 → `mpp = 地面距离 / 像素位移`。这是唯一不依赖任何
   "常数假设"的绝对口径（JSAPI 的 getBounds 与静态图 API 是两个服务，zoom 语义未必一致）。
2. **残余平移有多大** —— 把 OSM 路网按标定出的 mpp 投影成掩膜，与底图上的道路像素
   （白 + 黄）做 FFT 互相关，峰值位置就是还需要平移多少像素/米。峰值在 (0,0) 附近
   即说明"底图 ↔ 数据集"对齐。

    python tools/basemap_align_probe.py [community_id] [--no-network]

判读：
- 比例尺偏离 >1%：底图上地物会随离中心距离线性放大/缩小 → 表现为"中心还行、边上越来越偏"。
- 平移 >2 个像素（约 7 m）：坐标系换算或中心点有问题，检查 crs.py 与社区 center。
- 相关峰很平（score 明显低于 0.2）：OSM 路网在该区域覆盖太差，结论不可用，换社区复核。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.basemap import (BAIDU_MERCATOR_CONST, STATIC_URL,  # noqa: E402
                                       baidu_mpp, fetch_basemap, mercator_mpp)
from lifecycle_metrics import load_dataset                               # noqa: E402
from lifecycle_metrics.config import load_communities, repo_path, resolve_community  # noqa: E402
from lifecycle_metrics.crs import wgs84_to_bd09                          # noqa: E402
from lifecycle_metrics.env import baidu_credentials, load_env_file       # noqa: E402

ZOOM, SIZE, DS = 16, 1024, 2
DELTA_DEG = 0.004          # ≈ 380 m 经度 / 445 m 纬度
GREEN, YELLOW = (255, 255, 255), (255, 216, 107)


def _fetch(ak: str, lon: float, lat: float, zoom: int) -> Image.Image:
    bd = wgs84_to_bd09(lon, lat)
    r = requests.get(STATIC_URL, params={"ak": ak, "center": f"{bd[0]:.6f},{bd[1]:.6f}",
                                         "width": SIZE, "height": SIZE, "zoom": zoom}, timeout=30)
    if r.content[:4] != b"\x89PNG":
        raise RuntimeError(f"静态图返回异常：{r.text[:160]}")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _peak(a: np.ndarray, b: np.ndarray) -> tuple[float, int, int]:
    """归一化互相关峰 → (score, dx, dy)。a、b 需已去均值。"""
    cc = np.fft.irfft2(np.fft.rfft2(a) * np.conj(np.fft.rfft2(b)), s=a.shape)
    norm = math.sqrt(float((a ** 2).sum()) * float((b ** 2).sum())) + 1e-9
    dy, dx = np.unravel_index(int(np.argmax(cc)), cc.shape)
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    return float(cc.max()) / norm, int(dx), int(dy)


def _gray(img: Image.Image) -> np.ndarray:
    g = np.asarray(img.convert("L")).astype(float)
    return g - g.mean()


def _blur(mask: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    im = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(sigma))
    v = np.asarray(im).astype(float) / 255
    return v - v.mean()


def _road_mask(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    white = (r > 246) & (g > 246) & (b > 246)
    yellow = (r > 243) & (g > 185) & (b < 210) & (g - b > 35)
    m = (white | yellow).astype(np.uint8)
    return np.asarray(Image.fromarray(m * 255).resize((SIZE // DS, SIZE // DS), Image.BOX),
                      dtype=float) / 255


def _osm_road_mask(ds, center, mpp) -> np.ndarray:
    im = Image.new("L", (SIZE, SIZE), 0)
    d = ImageDraw.Draw(im)
    mlon, mlat = 111320 * math.cos(math.radians(center["lat"])), 111320.0
    coord = {n.id: (n.lon, n.lat) for n in ds.nodes}
    for e in ds.edges:
        if e.kind == "footway":
            continue
        cu, cv = coord.get(e.u), coord.get(e.v)
        if not cu or not cv:
            continue
        d.line([(SIZE / 2 + (cu[0] - center["lon"]) * mlon / mpp,
                 SIZE / 2 - (cu[1] - center["lat"]) * mlat / mpp),
                (SIZE / 2 + (cv[0] - center["lon"]) * mlon / mpp,
                 SIZE / 2 - (cv[1] - center["lat"]) * mlat / mpp)], fill=255, width=3)
    return np.asarray(im.resize((SIZE // DS, SIZE // DS), Image.BOX), dtype=float) / 255


def measure_scale(ak: str, center: dict, zoom: int = ZOOM) -> dict:
    """静态图 API 的绝对分辨率：两图互相关。

    容差判读：偏离假定常数 >2% 才算异常（实测 0.6% 以内，来自像素取整）。
    """
    mlon = 111320 * math.cos(math.radians(center["lat"]))
    mpp_assumed = baidu_mpp(center["lat"], zoom)
    expect_x = DELTA_DEG * mlon / mpp_assumed
    expect_y = DELTA_DEG * 111320.0 / mpp_assumed

    dx = dy = 0
    for attempt, delta in enumerate((DELTA_DEG, DELTA_DEG * 1.5)):
        base = _fetch(ak, center["lon"], center["lat"], zoom)
        east = _fetch(ak, center["lon"] + delta, center["lat"], zoom)
        north = _fetch(ak, center["lon"], center["lat"] + delta, zoom)
        _, dx, _ = _peak(_gray(base), _gray(east))
        _, _, dy = _peak(_gray(base), _gray(north))
        if abs(dx) > 0.5 * expect_x * (delta / DELTA_DEG) and abs(dy) > 0.5 * expect_y:
            break
    out = {"zoom": zoom, "shift_px": [dx, dy], "attempts": attempt + 1,
           "expected_shift_px": [round(expect_x, 1), round(expect_y, 1)],
           "mpp_x": round(DELTA_DEG * mlon / abs(dx), 4) if dx else None,
           "mpp_y": round(DELTA_DEG * 111320.0 / abs(dy), 4) if dy else None,
           "mpp_assumed": round(mpp_assumed, 4),
           "mpp_web_mercator": round(mercator_mpp(center["lat"], zoom), 4)}
    if out["mpp_x"]:
        ratio = out["mpp_x"] / out["mpp_assumed"]
        out["scale_error_pct"] = round((ratio - 1) * 100, 2)
        out["ok"] = abs(ratio - 1) < 0.02
    else:
        out["ok"] = False
        out["error"] = "两次请求返回同一张图（API 抖动），本项结论不可用"
    return out


def measure_shift(community, ds, base_path: Path) -> dict:
    """底图道路 与 OSM 路网 的残余平移（比例尺固定为标定值）。"""
    img = Image.open(base_path)
    br = _blur(_road_mask(img))
    c = community["center"]
    best = None
    for s in (0.98, 0.99, 1.0, 1.01, 1.02):
        mpp = baidu_mpp(c["lat"], ZOOM) * s
        om = _blur(_osm_road_mask(ds, c, mpp))
        score, dx, dy = _peak(br, om)
        rec = {"scale": s, "mpp": round(mpp, 4), "score": round(score, 4),
               "shift_px": [dx * DS, dy * DS],
               "shift_m": [round(dx * DS * mpp), round(dy * DS * mpp)]}
        if best is None or rec["score"] > best["score"]:
            best = rec
    best["ok"] = abs(best["shift_px"][0]) <= 4 and abs(best["shift_px"][1]) <= 4
    best["note"] = "峰值偏离 (0,0) 超过 ±2 像素（≈±7 m）就说明底图与数据集存在系统性平移"
    return best


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="底图对齐核验（比例尺 + 残余平移）")
    ap.add_argument("community_id", nargs="?", default=None)
    ap.add_argument("--no-network", action="store_true", help="只用缓存底图，不请求静态图 API")
    args = ap.parse_args(argv)

    community = resolve_community(load_communities(), args.community_id)
    cid = community["id"]
    ds = load_dataset(repo_path(community["dataset"]))
    base = Path(fetch_basemap(community, zoom=ZOOM, size=SIZE)).resolve()

    report: dict = {"community": cid, "zoom": ZOOM, "size": SIZE,
                    "assumed_constant": BAIDU_MERCATOR_CONST,
                    "basemap": str(base.relative_to(ROOT)),
                    "web_mercator_constant": 156543.03392}
    if not args.no_network:
        load_env_file()
        ak = baidu_credentials()["ak"]
        if ak:
            try:
                report["scale_measurement"] = measure_scale(ak, community["center"])
            except Exception as exc:                       # noqa: BLE001
                report["scale_measurement"] = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            report["scale_measurement"] = {"error": "未配置服务端 AK，跳过比例尺实测"}
    report["shift_measurement"] = measure_shift(community, ds, base)
    report["verdict"] = (
        "比例尺与残余平移均在容差内：底图可直接叠加数据集（无需再调 mpp）"
        if report["shift_measurement"].get("ok")
        and (report.get("scale_measurement", {}).get("ok", True))
        else "存在超差项，先看上面两项的实测值再决定调哪里")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
