"""边际效益曲线：多建一处，到底还能多覆盖多少？

    python tools/marginal.py [community_id ...] [--element KEY] [--max-k 4]

回答评委一定会问的问题："为什么是 2 处？"——把 k=1/2/3/4 分别跑一遍选址优化，
给出每增加一处带来的**新增覆盖户数增量**与**单位效益（户/处）**，看递减拐点在哪。

纯本地计算（读已装配好的数据集），不联网、不耗任何地图额度。
输出 `out/<社区>/marginal.json`，报告第 6 节会读取它。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics import RoadGraph, diagnose                        # noqa: E402
from lifecycle_metrics.accessibility import build_circle_grid, compute_access  # noqa: E402
from lifecycle_metrics.config import load_communities, load_elements, repo_path  # noqa: E402
from lifecycle_metrics.optimize import apply_sites, optimize_sites       # noqa: E402
from lifecycle_metrics.pipeline import assemble_dataset                  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="选址优化的边际效益曲线（本地）")
    ap.add_argument("community_ids", nargs="*")
    ap.add_argument("--element", default=None, help="缺省=按体检结果自动选最短板的可落地要素")
    ap.add_argument("--max-k", type=int, default=4)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

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
        grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
        base = diagnose(ds, cfg, graph=graph, access=access, grid=grid)

        t0 = time.time()
        rows = []
        prev_covered = 0.0
        base_uncov = None
        element = args.element
        hh_size = cfg.get("avg_household_size", 2.6)
        for k in range(1, args.max_k + 1):
            opt = optimize_sites(ds, cfg, element=element, k=k, graph=graph, access=access)
            element = element or opt["element"]
            after_ds = apply_sites(ds, opt["sites"], opt["element"])
            after_access = compute_access(after_ds, graph, cutoff, speed)
            after = diagnose(after_ds, cfg, graph=graph, access=after_access, grid=grid)
            # 累计新增覆盖 = 基线"不可达人口" − 建 k 处后的"不可达人口"（并集口径，
            # 不能用各点位自报的新增户数相加 —— 那样会把同组团的重复覆盖重复计数）
            if base_uncov is None:
                base_uncov = float(opt["before"]["uncovered_population"])
            covered_pop = base_uncov - float(opt["after"]["uncovered_population"])
            covered_hh = covered_pop / hh_size
            self_reported = sum(s["newly_covered_households"] for s in opt["sites"])
            rows.append({
                "k": k,
                "coverage_rate": round(opt["after"]["coverage_rate"], 4),
                "uncovered_population": int(opt["after"]["uncovered_population"]),
                "newly_covered_households": int(round(covered_hh)),
                "marginal_households": int(round(covered_hh - prev_covered)),
                "composite_score": after["summary"]["composite_score"],
                "avg_households_per_site": int(round(covered_hh / k)),
                "self_reported_households": self_reported,
                "sites": [{"lon": round(s["lon"], 6), "lat": round(s["lat"], 6),
                           "tier": s["tier"], "carrier": (s["carrier"] or {}).get("name"),
                           "new_households": s["newly_covered_households"]}
                          for s in opt["sites"]],
            })
            prev_covered = covered_hh
        payload = {
            "community": cid, "name": community["name"], "element": element,
            "element_name": (base_element_name(cfg, element)),
            "baseline": {"coverage_rate": base["elements"] and
                         next(e["coverage_rate"] for e in base["elements"] if e["key"] == element),
                         "composite_score": base["summary"]["composite_score"]},
            "rows": rows,
            "seconds": round(time.time() - t0, 1),
            "note": "每行 = 建 k 处时的结果。累计新增覆盖用「基线不可达人口 − 建后不可达人口」的"
                    "并集口径计算；各点位自报的新增户数（self_reported_households）会重复计数同组团的"
                    "覆盖，仅供单点参考，不要相加。边际增量递减的位置就是「再建不划算」的拐点。",
        }
        if not args.no_write:
            (repo_path("out") / cid).mkdir(parents=True, exist_ok=True)
            (repo_path("out") / cid / "marginal.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        first = rows[0]["newly_covered_households"] if rows else 0
        last = rows[-1]["marginal_households"] if rows else 0
        print(f"{cid:16s} {payload['element_name']}：第 1 处覆盖 {first:,} 户 → "
              f"第 {len(rows)} 处再增 {last:,} 户（边际递减） | {payload['seconds']}s")
    return 0


def base_element_name(cfg: dict, key: str | None) -> str:
    for e in cfg.get("elements", []):
        if e["key"] == key:
            return e["name"]
    return key or "—"


if __name__ == "__main__":
    raise SystemExit(main())
