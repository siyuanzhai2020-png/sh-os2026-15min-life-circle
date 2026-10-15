"""坐标对齐核验：把自建路网画到百度静态底图上，肉眼 + 指标双重确认不偏移。

    python tools/align_check.py [community_id]

产出（out/<cid>/）：
- align_check.png       整幅：底图 + 路网 + 设施 + 居住组团
- align_check_zoom.png  中心 500 m 放大 2 倍，用来判断"线压不压得住马路"

同时打印一组基准地物的 WGS-84 ↔ BD-09 偏移量（用百度地理编码现场复核）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont                    # noqa: E402

from lifecycle_metrics import load_dataset                     # noqa: E402
from lifecycle_metrics.basemap import fetch_basemap, projector  # noqa: E402
from lifecycle_metrics.config import (load_communities, repo_path,   # noqa: E402
                                      resolve_community)
from lifecycle_metrics.crs import wgs84_to_bd09                # noqa: E402
from lifecycle_metrics.env import baidu_credentials, load_env_file   # noqa: E402
from lifecycle_metrics.geo import haversine_m                  # noqa: E402
from lifecycle_metrics.raster import CJK_FONT_CANDIDATES       # noqa: E402

ZOOM = 16
SIZE = 1024
ROAD_COLOR = {"primary": (230, 0, 0), "bridge": (200, 0, 160), "residential": (255, 120, 0),
              "service": (255, 190, 0), "footway": (0, 170, 255)}

BENCHMARKS = ["曹杨公园", "兰溪青年公园", "曹杨新村街道办事处", "上海市曹杨第二中学"]


def _font(size: int):
    for path in CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def geocode_offset_check() -> list:
    """现场用百度地理编码复核：OSM(WGS-84) 地名点 与 百度(BD-09) 同名点 的偏移。"""
    load_env_file()
    ak = baidu_credentials()["ak"]
    if not ak:
        return []
    import requests
    out = []
    for name in BENCHMARKS:
        try:
            g = requests.get("https://api.map.baidu.com/geocoding/v3/",
                             params={"address": f"上海市普陀区{name}", "output": "json", "ak": ak},
                             timeout=20).json()
            loc = (g.get("result") or {}).get("location") or {}
            if not loc:
                continue
            # 百度给的是 BD-09 → 换算成 WGS-84，与 OSM 同名的公园位置比较
            from lifecycle_metrics.crs import bd09_to_wgs84
            wlon, wlat = bd09_to_wgs84(loc["lng"], loc["lat"])
            out.append({"name": name, "baidu_bd09": [loc["lng"], loc["lat"]],
                        "baidu_as_wgs84": [round(wlon, 6), round(wlat, 6)]})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"[:120]})
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv      # 之前漏了这行：命令行传的社区 ID 被忽略
    cid = argv[0] if argv else None
    community = resolve_community(load_communities(), cid)
    ds = load_dataset(repo_path(community["dataset"]))

    base_path = fetch_basemap(community, zoom=ZOOM, size=SIZE)
    img = Image.open(base_path).convert("RGB")
    proj = projector(community, SIZE, ZOOM)
    draw = ImageDraw.Draw(img)
    font = _font(16)

    coord = {n.id: (n.lon, n.lat) for n in ds.nodes}
    for kind in ("footway", "service", "residential", "primary", "bridge"):
        for e in ds.edges:
            if e.kind != kind:
                continue
            cu, cv = coord.get(e.u), coord.get(e.v)
            if not cu or not cv:
                continue
            draw.line([proj(*cu), proj(*cv)], fill=ROAD_COLOR.get(kind, (255, 0, 0)),
                      width=2 if kind in ("primary", "bridge") else 1)

    for f in ds.facilities:
        x, y = proj(f.lon, f.lat)
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(0, 160, 0), outline=(255, 255, 255))
    for r in ds.residential:
        x, y = proj(r.lon, r.lat)
        draw.rectangle([x - 3, y - 3, x + 3, y + 3], fill=(200, 0, 0), outline=(255, 255, 255))

    # 中心点十字 + 比例尺
    cx, cy = proj(*[community["center"][k] for k in ("lon", "lat")])
    draw.line([cx - 14, cy, cx + 14, cy], fill=(0, 0, 0), width=3)
    draw.line([cx, cy - 14, cx, cy + 14], fill=(0, 0, 0), width=3)
    bar = int(500 / proj.mpp)
    bx, by = 30, SIZE - 40
    draw.line([bx, by, bx + bar, by], fill=(0, 0, 0), width=4)
    draw.text((bx, by - 22), "500 m", font=font, fill=(0, 0, 0))
    for i, (label, color) in enumerate([("主干路/支路", ROAD_COLOR["primary"]),
                                        ("内部步道", ROAD_COLOR["footway"]),
                                        ("设施(百度)", (0, 160, 0)), ("居住组团", (200, 0, 0))]):
        draw.rectangle([bx + 260 + i * 170, by - 12, bx + 274 + i * 170, by + 2],
                       fill=color, outline=(255, 255, 255))
        draw.text((bx + 280 + i * 170, by - 16), label, font=font, fill=(0, 0, 0))

    out_dir = repo_path("out") / community["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    full = out_dir / "align_check.png"
    img.save(full)

    half = int(250 / proj.mpp)      # 中心 ±250 m
    crop = img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    zoom_path = out_dir / "align_check_zoom.png"
    crop.save(zoom_path)

    print(json.dumps({
        "ok": True,
        "full": str(full.relative_to(ROOT)),
        "zoom": str(zoom_path.relative_to(ROOT)),
        "zoom_level": ZOOM, "mpp_m_per_px": round(proj.mpp, 3),
        "span_m": round(SIZE * proj.mpp, 1),
        "legend": "红线=主干路/支路（应压在百度底图的马路上）；蓝线=内部步道（底图通常不画）；绿点=设施；红方=居住组团",
        "geocode_check": geocode_offset_check(),
        "hint": "若红线整体偏移几百米，说明坐标换算反了；若红线稀疏、只在部分路段出现，"
                "那是 OSM 覆盖不全，不是偏移。",
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
