"""自选社区：按地址（百度地理编码）或经纬度新建一个分析社区。

    python tools/add_community.py --name "长风新村街道" --address "上海市普陀区长风新村街道办事处"
    python tools/add_community.py --name "XX社区" --lon 121.42 --lat 31.23 --half-width 700
    python tools/add_community.py --name "XX社区" --address "..." --population 98000 --area 2.14

说明：
- 地址 → 百度地理编码（BD-09）→ 转 WGS-84 作为社区中心；必要时人工核对后再用。
- 数据装配走 providers/baidu.py：百度地点检索拿 POI + Overpass 拿步行路网与建筑，
  因此**需要外网**；整个过程 20–60 秒，原始响应会缓存，重复运行不再耗时耗配额。
- 可用 --population/--area 给出七普常住人口与街道面积，系统按人口密度对户数做总量校准。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import (load_communities, load_elements,   # noqa: E402
                                      repo_path, save_communities,
                                      slugify_community)
from lifecycle_metrics.pipeline import assemble_dataset                  # noqa: E402
from lifecycle_metrics.providers.baidu import geocode_wgs84              # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新建自选社区")
    ap.add_argument("--name", required=True, help="社区名称，如「长风新村街道」")
    ap.add_argument("--address", help="用于地理编码的地址（与 --lon/--lat 二选一）")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--half-width", type=int, default=800, help="分析范围半边长（米），默认 800")
    ap.add_argument("--provider", default="baidu", choices=["baidu", "osm", "synthetic"])
    ap.add_argument("--population", type=float, help="常住人口（用于总量校准，建议填七普街道数据）")
    ap.add_argument("--area", type=float, help="街道面积 km²（与 --population 一起用于算密度）")
    ap.add_argument("--no-build", action="store_true", help="只写配置，不立即构建数据集")
    args = ap.parse_args(argv)

    cfg = load_communities()
    ids = [c["id"] for c in cfg["communities"]]
    cid = slugify_community(args.name, ids)

    if args.lon is None or args.lat is None:
        if not args.address:
            print("需要 --address 或 --lon/--lat")
            return 2
        t0 = time.time()
        lon, lat, meta = geocode_wgs84(args.address)
        print(f"[geocode] {args.address} → WGS-84 ({lon:.6f}, {lat:.6f})  "
              f"置信度={meta.get('confidence')} 级别={meta.get('level')}  {time.time() - t0:.1f}s")
    else:
        lon, lat, meta = args.lon, args.lat, {"source": "user"}

    community = {
        "id": cid,
        "name": args.name,
        "district": meta.get("district") or "",
        "center": {"lon": round(lon, 6), "lat": round(lat, 6)},
        "center_note": f"WGS-84；来源={meta.get('source', 'user')}",
        "extent_m": {"half_width": args.half_width, "half_height": args.half_width},
        "provider": args.provider,
        "dataset": f"data/{cid}.dataset.json",
        "elements_config": "config/elements.json",
        "cutoff_element": "circle_15min",
        "data_disclaimer": "自选社区：POI 来自百度地图开放平台，步行路网与建筑来自 OpenStreetMap；"
                           "户数为估算/校准值，仅用于赛题演示。",
    }
    if args.population and args.area:
        community["population_calibration"] = {
            "density_per_km2": round(args.population / args.area, 1),
            "basis": f"常住人口 {args.population:,.0f} 人 ÷ 面积 {args.area} km² "
                     f"= {args.population / args.area:,.0f} 人/km²，再乘分析范围 "
                     f"{(2 * args.half_width / 1000) ** 2:.2f} km²",
            "sources": ["用户提供的常住人口与面积（建议注明七普或街道年报）"],
            "caveat": "分析范围是方框，可能包含行政边界外的区域；总量为密度推算近似值。",
        }

    cfg["communities"].append(community)
    path = save_communities(cfg)
    print(f"[config] 已写入 {path.relative_to(ROOT)}   id={cid}")

    if args.no_build:
        print(json.dumps(community, ensure_ascii=False, indent=1))
        return 0

    elements_cfg = load_elements(repo_path(community["elements_config"]))
    t0 = time.time()
    ds, info = assemble_dataset(community, elements_cfg, rebuild=True)
    print(f"[build] {len(ds.nodes)} 节点 / {len(ds.edges)} 边 / {len(ds.residential)} 组团 / "
          f"{len(ds.facilities)} 设施  {time.time() - t0:.1f}s")
    print(json.dumps({"community": community, "build": info,
                      "households": ds.total_households()}, ensure_ascii=False, indent=1))
    print(f"\n下一步：python tools/build_demo.py {cid}     # 出报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
