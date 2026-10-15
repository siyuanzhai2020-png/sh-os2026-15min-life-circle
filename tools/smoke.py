"""开发期冒烟脚本：生成数据 → 体检 → 选址优化，打印关键结果。

    python tools/smoke.py [community_id]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics import RoadGraph, diagnose                      # noqa: E402
from lifecycle_metrics.accessibility import build_circle_grid, compute_access  # noqa: E402
from lifecycle_metrics.config import (load_communities, load_elements,   # noqa: E402
                                      repo_path, resolve_community)
from lifecycle_metrics.metrics import strip_runtime                     # noqa: E402
from lifecycle_metrics.optimize import optimize_sites                   # noqa: E402
from lifecycle_metrics.providers import get_provider                    # noqa: E402


def main() -> int:
    community_id = sys.argv[1] if len(sys.argv) > 1 else None
    communities = load_communities()
    community = resolve_community(communities, community_id)
    elements_cfg = load_elements(repo_path(community["elements_config"]))

    t0 = time.time()
    provider = get_provider(community.get("provider", "synthetic"))
    ds = provider(community)
    graph = RoadGraph.from_dataset(ds)
    cutoff = elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = elements_cfg.get("walk_speed_mps", 1.25)

    t1 = time.time()
    access = compute_access(ds, graph, cutoff, speed)
    access5 = compute_access(ds, graph, elements_cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    t2 = time.time()
    diag = diagnose(ds, elements_cfg, graph=graph, access=access, access_5min=access5, grid=grid)
    t3 = time.time()
    opt = optimize_sites(ds, elements_cfg, k=2, graph=graph, access=access)
    t4 = time.time()

    print(json.dumps({
        "timing_s": {"build": round(t1 - t0, 2), "access": round(t2 - t1, 2),
                     "diagnose": round(t3 - t2, 2), "optimize": round(t4 - t3, 2)},
        "summary": diag["summary"],
        "dimensions": {k: v["score"] for k, v in diag["dimensions"].items()},
        "elements": [{k: r[k] for k in ("key", "facility_count", "coverage_rate",
                                        "single_dependency_rate", "gap_index")}
                     for r in diag["elements"]],
        "problems": [{k: p[k] for k in ("level", "title", "affected_households")}
                     for p in diag["problems"]],
        "optimize": {k: opt[k] for k in ("element", "element_name", "k_selected",
                                         "before", "after", "gain")},
        "sites": opt["sites"],
        "strip_ok": bool(strip_runtime(diag)),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
