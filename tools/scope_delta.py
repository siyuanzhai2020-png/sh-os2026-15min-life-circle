"""分析范围口径核对：逐社区统计落在分析范围之外的居住组团，并给出重建前后差异。

百度地点检索是**圆形**、半径 = max(半宽, 半高) × 1.3，比方形分析范围大一圈
（半边长 800 m → 半径 1040 m，四角更远）。框外的住宅区 POI 一旦被当成分析单元，
就会出现"地图上有小区、栅格上却没有色块"，并系统性拉低覆盖率（设施只统计分析范围内
的）。OSM 侧一直有这道判定，百度补充路径曾漏掉 —— 本工具用于核对修复结果。

    python tools/scope_delta.py                只看当前已缓存的数据集（不联网、不写盘）
    python tools/scope_delta.py --rebuild      重新装配数据集后对照（走本地 POI/OSM 缓存）
    python tools/scope_delta.py --ids a,b      只查指定社区
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 管道默认 GBK，口径文本里的 "km²"（U+00B2）编码不了 → 强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from lifecycle_metrics.accessibility import build_circle_grid, compute_access   # noqa: E402
from lifecycle_metrics.config import load_communities, load_elements, repo_path  # noqa: E402
from lifecycle_metrics.pipeline import assemble_dataset                          # noqa: E402
from lifecycle_metrics.providers.baidu import in_analysis_box, search_area       # noqa: E402
from lifecycle_metrics.schema import Dataset                                     # noqa: E402


def outside_box(community: Dict[str, Any], ds: Dataset) -> List[Any]:
    return [r for r in ds.residential
            if not in_analysis_box(community, r.lon, r.lat)]


def scan(community: Dict[str, Any], ds: Dataset) -> Dict[str, Any]:
    out = outside_box(community, ds)
    return {
        "residential": len(ds.residential),
        "households": ds.total_households(),
        "outside": len(out),
        "outside_households": sum(r.households for r in out),
        "outside_osm": sum(1 for r in out if r.id.startswith("R")),
        "outside_baidu": sum(1 for r in out if r.id.startswith("BR")),
        "outside_ids": [r.id for r in out][:8],
        "outside_poi_named": ds.meta.get("residential_poi_outside_box"),
    }


def score_of(community: Dict[str, Any], ds: Dataset) -> Dict[str, Any]:
    from lifecycle_metrics import RoadGraph, diagnose
    elements_cfg = load_elements(repo_path(community["elements_config"]))
    graph = RoadGraph.from_dataset(ds)
    cutoff = elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = elements_cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cutoff, speed)
    access5 = compute_access(ds, graph, elements_cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    diag = diagnose(ds, elements_cfg, graph=graph, access=access,
                    access_5min=access5, grid=grid)
    return {"score": diag["summary"]["composite_score"],
            "households": diag["summary"]["households"]}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分析范围口径核对")
    ap.add_argument("--rebuild", action="store_true", help="重新装配数据集（走本地缓存）")
    ap.add_argument("--ids", default="", help="逗号分隔的社区 id，缺省=全部")
    args = ap.parse_args(argv)

    want = {s.strip() for s in args.ids.split(",") if s.strip()}
    communities = [c for c in load_communities().get("communities", [])
                   if not want or c["id"] in want]
    rows = []
    for c in communities:
        path = repo_path(c["dataset"])
        if not path.exists():
            rows.append({"community": c["name"], "id": c["id"], "note": "数据集不存在"})
            continue
        before_ds = Dataset.from_dict(json.loads(path.read_text(encoding="utf-8")))
        before = scan(c, before_ds)
        _, radius = search_area(c)
        row: Dict[str, Any] = {
            "community": c["name"], "id": c["id"],
            "box_m": 2 * c["extent_m"]["half_width"], "search_radius_m": radius,
            "before": before,
        }
        if args.rebuild:
            elements_cfg = load_elements(repo_path(c["elements_config"]))
            ds, info = assemble_dataset(c, elements_cfg, rebuild=True)
            after = scan(c, ds)
            row["after"] = after
            row["source"] = info["source"]
            if before["outside"]:
                row["score"] = {"before": score_of(c, before_ds)["score"],
                                "after": score_of(c, ds)["score"]}
        rows.append(row)

    ok = all(r.get("after", r.get("before", {})).get("outside", 0) == 0
             for r in rows if "before" in r)
    print(json.dumps({"rows": rows, "all_in_box": ok}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
