"""一键跑通全链路：生成/加载数据 → 可达性 → 体检 → 选址优化 → 报告。

    python tools/build_demo.py [community_id] [--k 2] [--out out] [--no-optimize]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台/管道的默认编码是 GBK，而户数校准口径里含 "km²"（U+00B2）—— GBK 编码不了
# 它。`demoall` 把本脚本的输出接到管道（`| Out-Null`）时会 UnicodeEncodeError 并以非零码
# 结束，看起来像"构建失败"，其实报告在 print 之前就写完了。强制 UTF-8 输出即可。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from lifecycle_metrics import RoadGraph, diagnose                        # noqa: E402
from lifecycle_metrics.accessibility import build_circle_grid, compute_access  # noqa: E402
from lifecycle_metrics.config import (load_communities, load_elements,   # noqa: E402
                                      repo_path, resolve_community)
from lifecycle_metrics.optimize import apply_sites, optimize_sites        # noqa: E402
from lifecycle_metrics.pipeline import assemble_dataset                  # noqa: E402
from lifecycle_metrics.report import build_report                        # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成社区生活圈体检演示")
    ap.add_argument("community_id", nargs="?", default=None)
    ap.add_argument("--k", type=int, default=2, help="选址优化新增设施数量")
    ap.add_argument("--out", default="out", help="输出目录")
    ap.add_argument("--no-optimize", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="忽略已缓存数据集，重新生成")
    args = ap.parse_args(argv)

    communities = load_communities()
    community = resolve_community(communities, args.community_id)
    elements_cfg = load_elements(repo_path(community["elements_config"]))
    out_dir = repo_path(args.out) / community["id"]
    dataset_path = repo_path(community["dataset"])

    t0 = time.time()
    ds, build_info = assemble_dataset(community, elements_cfg, rebuild=args.rebuild)
    src = build_info["source"]
    t1 = time.time()

    graph = RoadGraph.from_dataset(ds)
    cutoff = elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = elements_cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cutoff, speed)
    access_5 = compute_access(ds, graph, elements_cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    t2 = time.time()

    diag = diagnose(ds, elements_cfg, graph=graph, access=access, access_5min=access_5, grid=grid)
    opt = None if args.no_optimize else optimize_sites(ds, elements_cfg, k=args.k,
                                                       graph=graph, access=access)
    if opt:
        from lifecycle_metrics.profile import enrich_optimization
        enrich_optimization(ds, graph, elements_cfg, opt, walk_speed=speed)   # 点位画像
    # 新增设施后的体检：把推荐点位代进去重算，用于报告的"优化后覆盖图"与得分对比
    after_diag = None
    if opt and opt.get("sites"):
        after_ds = apply_sites(ds, opt["sites"], opt["element"])
        after_access = compute_access(after_ds, graph, cutoff, speed)
        after_access5 = compute_access(after_ds, graph,
                                       elements_cfg["cutoffs"]["circle_5min"]["time_s"], speed)
        after_diag = diagnose(after_ds, elements_cfg, graph=graph, access=after_access,
                              access_5min=after_access5, grid=grid)
    t3 = time.time()

    files = build_report(ds, diag, elements_cfg, out_dir, opt=opt, run_optimize=False,
                         after_diag=after_diag)
    t4 = time.time()

    print(json.dumps({
        "dataset": str(dataset_path.relative_to(ROOT)),
        "dataset_source": src,
        "community": community["name"],
        "household_provenance": {
            "overrides": build_info["household_overrides"],
            "calibration": build_info["population_calibration"],
        },
        "summary": diag["summary"],
        "dimensions": {k: v["score"] for k, v in diag["dimensions"].items()},
        "top_problems": [p["title"] for p in diag["problems"][:5]],
        "prescription": None if opt is None else {
            "element": opt["element_name"],
            "coverage_before": opt["before"]["coverage_rate"],
            "coverage_after": opt["after"]["coverage_rate"],
            "gain_pp": opt["gain"]["coverage_rate_pp"],
            "sites": [{"lon": s["lon"], "lat": s["lat"],
                       "new_households": s["newly_covered_households"]} for s in opt["sites"]],
        },
        "timing_s": {"load_or_build": round(t1 - t0, 2), "access": round(t2 - t1, 2),
                     "diagnose": round(t3 - t2, 2), "report": round(t4 - t3, 2)},
        "files": {k: str(v.relative_to(ROOT)) for k, v in files.items()},
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
