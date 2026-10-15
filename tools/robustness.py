"""结论稳健性检验：把关键假设各动一动，看结论会不会翻盘。

    python tools/robustness.py [community_id ...] [--json]

三类扰动（都是**本地重算**，不联网、不耗任何地图额度）：
    ① 步行速度 1.10 / 1.25 / 1.35 m/s（标定显示百度隐含 1.08 m/s，本项目取 1.25）
    ② 生活圈阈值 ±10%（810 / 900 / 990 s）
    ③ 两者同时取保守端（1.10 m/s + 810 s）与宽松端（1.35 m/s + 990 s）

输出：`out/<社区>/robustness.json`（报告第 10 节会读取它）。
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
from lifecycle_metrics.pipeline import assemble_dataset                  # noqa: E402

SPEEDS = [1.10, 1.25, 1.35]
CUTOFF_SCALES = [0.9, 1.0, 1.1]


def run_one(community, cfg_base, speed: float, cutoff_s: float) -> dict:
    cfg = dict(cfg_base)
    cfg["cutoffs"] = {k: dict(v) for k, v in cfg_base["cutoffs"].items()}
    cfg["cutoffs"]["circle_15min"]["time_s"] = cutoff_s
    ds, _ = assemble_dataset(community, cfg)
    graph = RoadGraph.from_dataset(ds)
    access = compute_access(ds, graph, cutoff_s, speed)
    access5 = compute_access(ds, graph, cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff_s, cell_m=40.0, walk_speed=speed)
    diag = diagnose(ds, cfg, graph=graph, access=access, access_5min=access5, grid=grid)
    s = diag["summary"]
    req = [e for e in diag["elements"] if e["required"]]
    worst = min(req, key=lambda e: e["coverage_rate"]) if req else {}
    return {"speed_mps": speed, "cutoff_s": cutoff_s,
            "composite_score": s["composite_score"], "grade": s["grade"],
            "coverage": diag["dimensions"]["coverage"]["score"],
            "compliance": diag["dimensions"]["compliance"]["score"],
            "proximity_5min": diag["dimensions"]["proximity_5min"]["score"],
            "worst_element": worst.get("name"), "worst_coverage": worst.get("coverage_rate"),
            "uncovered_population": int(round((1 - diag["dimensions"]["coverage"]["score"])
                                              * s["assessable_population"]))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="结论稳健性检验（本地重算）")
    ap.add_argument("community_ids", nargs="*")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    wanted = set(args.community_ids)
    out = {}
    for community in load_communities()["communities"]:
        cid = community["id"]
        if wanted and cid not in wanted:
            continue
        path = repo_path(community["dataset"])
        if not path.exists():
            continue
        cfg = load_elements(repo_path(community["elements_config"]))
        base_cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
        t0 = time.time()
        rows = []
        for sp in SPEEDS:
            rows.append(run_one(community, cfg, sp, base_cutoff))
        for sc in CUTOFF_SCALES:
            if sc == 1.0:
                continue
            rows.append(run_one(community, cfg, 1.25, base_cutoff * sc))
        # 两端同时取保守/宽松值
        rows.append(run_one(community, cfg, SPEEDS[0], base_cutoff * CUTOFF_SCALES[0]))
        rows.append(run_one(community, cfg, SPEEDS[-1], base_cutoff * CUTOFF_SCALES[-1]))
        scores = [r["composite_score"] for r in rows]
        worsts = {r["worst_element"] for r in rows}
        payload = {
            "community": cid, "name": community["name"],
            "baseline": {"speed_mps": 1.25, "cutoff_s": base_cutoff},
            "rows": rows,
            "score_min": min(scores), "score_max": max(scores),
            "score_span": round(max(scores) - min(scores), 1),
            "grade_stable": len({r["grade"] for r in rows}) == 1,
            "worst_element_stable": len(worsts) == 1,
            "worst_element": rows[0].get("worst_element"),
            "seconds": round(time.time() - t0, 1),
            "note": "速度与阈值扰动下的重算结果；分数区间越小说明结论越不依赖假设。"
                    "官方抽样标定显示百度隐含步速约 1.08 m/s，故速度下界取 1.10 m/s。",
        }
        out[cid] = payload
        if not args.no_write:
            (repo_path("out") / cid).mkdir(parents=True, exist_ok=True)
            (repo_path("out") / cid / "robustness.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{cid:16s} {payload['score_min']}–{payload['score_max']} 分"
              f"（跨度 {payload['score_span']}）等级{'稳定' if payload['grade_stable'] else '会变'}"
              f" | {payload['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
