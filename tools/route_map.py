"""一天动线图（PNG）+ 单组团等时圈 + 住户画像对比：报告第 5 章的三个素材。

    python tools/route_map.py [community_id] [--point CR050] [--mode before|after] [--out 文件名]

不指定组团时自动挑"最适合当示例"的那一个。
纯本地计算，不联网。

产出（落在 out/<community_id>/）：
- map_route.png / day_route.json      某个组团的一天动线（全要素口径）
- map_focus.png / focus_iso.json      以同一组团为圆心的同心等时圈（"风圈"）
- personas.json                       同一组团、五类住户的一天对比
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics import RoadGraph, diagnose, load_dataset              # noqa: E402
from lifecycle_metrics.accessibility import build_circle_grid, compute_access  # noqa: E402
from lifecycle_metrics.config import (load_communities, load_elements,       # noqa: E402
                                      load_personas, repo_path)
from lifecycle_metrics.isochrone import focus_isochrone                      # noqa: E402
from lifecycle_metrics.optimize import apply_sites                           # noqa: E402
from lifecycle_metrics.pipeline import assemble_dataset                      # noqa: E402
from lifecycle_metrics.raster import render_map_png, render_route_png        # noqa: E402
from lifecycle_metrics.route import plan_day_route                           # noqa: E402


def pick_point(diag: dict, community: dict, ds=None) -> str:
    """挑一个"最适合当示例"的组团：**在社区中部**、有 1–3 项缺口（故事性）、户数较大。

    为什么不挑"缺口最多"的：那种组团往往贴在分析框边缘，动线被挤在边界上，图不好看也
    不代表社区常态；缺 1–3 项的中间态既能看到日常动线，又能看到短板。
    """
    from lifecycle_metrics.geo import haversine_m
    c = community["center"]
    coord = {r.id: (r.lon, r.lat) for r in ds.residential} if ds is not None else {}
    pts = [p for p in diag["points"] if p.get("assessable", True) and p["id"] in coord]
    if not pts:
        return diag["points"][0]["id"]
    mid = [p for p in pts if 1 <= len(p["missing"]) <= 3] or pts
    mid.sort(key=lambda p: (haversine_m(c["lon"], c["lat"], *coord[p["id"]]), -p["households"]))
    return mid[0]["id"]


def _trim_route(route: dict) -> dict:
    return {k: v for k, v in route.items() if k != "stops"} | {
        "stops": [{kk: vv for kk, vv in s.items() if kk != "leg_coords"}
                  for s in route.get("stops", [])]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成一天动线图 / 风圈图 / 住户画像对比")
    ap.add_argument("community_ids", nargs="*")
    ap.add_argument("--point", default=None)
    ap.add_argument("--mode", default="before", choices=("before", "after"))
    ap.add_argument("--k", type=int, default=2)
    args = ap.parse_args(argv)

    personas = load_personas().get("personas") or []
    wanted = set(args.community_ids)
    for community in load_communities()["communities"]:
        cid = community["id"]
        if wanted and cid not in wanted:
            continue
        if not repo_path(community["dataset"]).exists():
            continue
        cfg = load_elements(repo_path(community["elements_config"]))
        ds, _ = assemble_dataset(community, cfg)
        graph = RoadGraph.from_dataset(ds)
        cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
        speed = cfg.get("walk_speed_mps", 1.25)
        access = compute_access(ds, graph, cutoff, speed)
        diag = diagnose(ds, cfg, graph=graph, access=access)

        sites = []
        if args.mode == "after":
            from lifecycle_metrics.optimize import optimize_sites
            opt = optimize_sites(ds, cfg, k=args.k, graph=graph, access=access)
            sites = opt.get("sites") or []
            if sites:
                ds = apply_sites(ds, sites, opt["element"])
                graph = RoadGraph.from_dataset(ds)

        pid = args.point or pick_point(diag, community, ds)
        route = plan_day_route(ds, graph, pid, cfg)
        route["mode"] = args.mode
        out_dir = repo_path("out") / cid
        out_dir.mkdir(parents=True, exist_ok=True)
        path = render_route_png(ds, diag, route, out_dir / "map_route.png")
        (out_dir / "day_route.json").write_text(
            json.dumps(_trim_route(route), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{cid:16s} {route['point']['name']}：{route['totals']['stops']} 处停靠 / "
              f"步行 {route['totals']['walk_min']} 分钟 / 缺口 {len(route['missing'])} 项 → "
              f"{path.relative_to(ROOT)}")

        # ---- 风圈：以同一组团为圆心 ----
        iso = focus_isochrone(ds, graph, cfg, pid)
        render_map_png(ds, diag, out_dir / "map_focus.png", opt=None,
                       raster="isochrone", focus=iso)
        (out_dir / "focus_iso.json").write_text(
            json.dumps({k: v for k, v in iso.items()
                        if k != "grid"} | {"grid_meta": {k: v for k, v in iso["grid"].items()
                                                         if k != "rows"}},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        cp = iso["compactness"]
        print(f"{'':16s} 风圈 {(iso['point']['name'])}：5/10/15 分钟圈内 "
              f"{'/'.join(str(b['elements_count']) for b in iso['bands'])} 项，"
              f"可达面积比 {cp['ratio'] * 100:.0f}% → map_focus.png")

        # ---- 住户画像对比：同一栋楼，五类住户的一天 ----
        rows = []
        for p in personas:
            r = plan_day_route(ds, graph, pid, cfg, persona=p)
            rows.append({"key": p["key"], "name": p["name"], "emoji": p.get("emoji", ""),
                         "who": p.get("who", ""), "walk_speed_mps": p["walk_speed_mps"],
                         "totals": r["totals"], "missing": r["missing"],
                         "conflicts": r["conflicts"],
                         "recommendations": r["recommendations"]})
            print(f"{'':16s} {p['name']:14s} 停靠 {r['totals']['stops']:>2} / "
                  f"步行 {r['totals']['walk_min']:>6.1f} 分钟 / "
                  f"冲突 {r['totals']['conflict_count']} / 缺口 {r['totals']['missing_count']}")
        (out_dir / "personas.json").write_text(
            json.dumps({"point_id": pid, "point_name": route["point"]["name"],
                        "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
