"""误差分析：自算路网 vs 百度步行路径规划（抽样对照）。

为什么要做：本项目可达性由自建路网 + Dijkstra 计算（不烧配额、可解释），
必须回答"自算结果准不准"。做法是在同一批 OD 上调用百度步行路径规划，
对比距离、时间与 15 分钟阈值的判定一致性，并把结论写进报告的误差分析章节。

    python tools/calibrate_walk.py [community_id] [--samples 30]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics import RoadGraph, load_dataset                     # noqa: E402
from lifecycle_metrics.accessibility import compute_access                # noqa: E402
from lifecycle_metrics.config import (load_communities, load_elements,    # noqa: E402
                                      repo_path, resolve_community)
from lifecycle_metrics.geo import haversine_m                            # noqa: E402
from lifecycle_metrics.providers.baidu import walking_route              # noqa: E402


def engine_times(ds, graph, cutoff_s: float, walk_speed: float) -> dict:
    """每个居住组团 × 每个必需要素 → 引擎判定最近的设施与时间/距离。"""
    rows = []
    for rp in ds.residential:
        nid, snap_m = graph.nearest_node(rp.lon, rp.lat)
        if nid is None:
            continue
        t_dist = graph.dijkstra(nid, cutoff_s)
        m_dist = graph.dijkstra_length(nid, 10_000)
        rp_pen = snap_m / walk_speed
        for f in ds.facilities:
            fnid, fsnap = graph.nearest_node(f.lon, f.lat)
            if fnid is None:
                continue
            t = t_dist.get(fnid)
            if t is None:
                continue
            total_t = t + rp_pen + fsnap / walk_speed
            if total_t > cutoff_s:
                continue
            rows.append({
                "point_id": rp.id, "point_name": rp.name, "element": f.element,
                "facility_id": f.id, "facility_name": f.name,
                "engine_time_s": round(total_t, 1),
                "engine_route_m": round(m_dist.get(fnid, 0.0) + snap_m + fsnap, 1),
                "origin": [rp.lat, rp.lon], "destination": [f.lat, f.lon],
                "straight_m": round(haversine_m(rp.lon, rp.lat, f.lon, f.lat), 1),
            })
    # 每个(组团,要素)只留引擎认为最近的一处
    best: dict = {}
    for r in rows:
        key = (r["point_id"], r["element"])
        if key not in best or r["engine_time_s"] < best[key]["engine_time_s"]:
            best[key] = r
    return best


def sample_pairs(rows: list, samples: int) -> list:
    """按引擎时间分层抽样，覆盖短/中/长距离，避免只测"门口那家"。"""
    rows = sorted(rows, key=lambda r: r["engine_time_s"])
    if len(rows) <= samples:
        return rows
    step = len(rows) / samples
    return [rows[min(len(rows) - 1, int(i * step))] for i in range(samples)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="自算 vs 百度步行路径规划 抽样对照")
    ap.add_argument("community_id", nargs="?", default=None)
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--out", default="out")
    args = ap.parse_args(argv)

    community = resolve_community(load_communities(), args.community_id)
    cfg = load_elements(repo_path(community["elements_config"]))
    ds = load_dataset(repo_path(community["dataset"]))
    graph = RoadGraph.from_dataset(ds)
    cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = cfg.get("walk_speed_mps", 1.25)

    rows = list(engine_times(ds, graph, cutoff, speed).values())
    picked = sample_pairs(rows, args.samples)
    print(f"[info] 候选 OD {len(rows)} 组，抽样 {len(picked)} 组（覆盖 {picked[0]['engine_time_s']:.0f}s"
          f"–{picked[-1]['engine_time_s']:.0f}s）", flush=True)

    pairs = []
    for i, r in enumerate(picked, start=1):
        try:
            w = walking_route(tuple(r["origin"]), tuple(r["destination"]))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 第 {i} 组调用失败：{type(exc).__name__}: {exc}", flush=True)
            continue
        if not w.get("ok") or not w.get("duration_s"):
            print(f"[warn] 第 {i} 组无有效路径（百度返回 {w.get('distance_m')} m / "
                  f"{w.get('duration_s')} s），已跳过", flush=True)
            continue
        pair = dict(r)
        pair.update({"baidu_duration_s": w["duration_s"], "baidu_distance_m": w["distance_m"]})
        pair["time_ratio"] = round(r["engine_time_s"] / w["duration_s"], 3) if w["duration_s"] else None
        pair["distance_ratio"] = round(r["engine_route_m"] / w["distance_m"], 3) if w["distance_m"] else None
        pair["threshold_agree"] = ((r["engine_time_s"] <= cutoff) == (w["duration_s"] <= cutoff))
        pair["implied_baidu_speed_mps"] = (
            round(w["distance_m"] / w["duration_s"], 3) if w["duration_s"] else None)
        pairs.append(pair)
        print(f"  {i:>2}/{len(picked)} {r['point_name'][:14]:<14} → {r['facility_name'][:16]:<16} "
              f"自算 {r['engine_time_s']:>6.0f}s/{r['engine_route_m']:>5.0f}m  "
              f"百度 {w['duration_s']:>5}s/{w['distance_m']:>5}m  比值 {pair['time_ratio']}", flush=True)

    if not pairs:
        print(json.dumps({"ok": False, "error": "没有成功对照的样本（检查网络或 AK）"},
                         ensure_ascii=False))
        return 2

    ratios = sorted(p["time_ratio"] for p in pairs if p["time_ratio"])
    dist_ratios = sorted(p["distance_ratio"] for p in pairs if p["distance_ratio"])
    speeds = sorted(p["implied_baidu_speed_mps"] for p in pairs if p["implied_baidu_speed_mps"])
    diffs = sorted(abs(p["engine_time_s"] - p["baidu_duration_s"]) for p in pairs)
    within30 = sum(1 for r in ratios if 0.7 <= r <= 1.3) / max(len(ratios), 1)
    outliers = sorted((p for p in pairs if p["time_ratio"] and p["time_ratio"] > 2.0),
                      key=lambda p: -p["time_ratio"])

    def pct(vals, q):
        if not vals:
            return None
        return round(vals[min(len(vals) - 1, int(q * len(vals)))], 3)

    summary = {
        "ok": True,
        "community": community["id"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_pairs": len(pairs),
        "engine_walk_speed_mps": speed,
        "time_ratio_median": round(statistics.median(ratios), 3),
        "time_ratio_p10": pct(ratios, 0.10),
        "time_ratio_p90": pct(ratios, 0.90),
        "share_within_30pct": round(within30, 3),
        "n_outliers_gt_2x": len(outliers),
        "outlier_examples": [
            {"point": p["point_name"], "facility": p["facility_name"],
             "engine_s": p["engine_time_s"], "baidu_s": p["baidu_duration_s"],
             "ratio": p["time_ratio"], "note": "多因 OSM 与百度路网拓扑差异（缺人行桥/门禁/围墙）"}
            for p in outliers[:3]],
        "distance_ratio_median": round(statistics.median(dist_ratios), 3) if dist_ratios else None,
        "median_abs_time_diff_s": round(statistics.median(diffs), 1),
        "threshold_agreement_15min": round(sum(p["threshold_agree"] for p in pairs) / len(pairs), 3),
        "implied_baidu_speed_median_mps": round(statistics.median(speeds), 3) if speeds else None,
        "cutoff_s": cutoff,
        "note": "engine_time 含居住点与设施的离路吸附惩罚；百度为官方步行路径规划（已按 BD-09 换算请求）。",
    }

    out_dir = repo_path(args.out) / community["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "walk_calibration.json").write_text(
        json.dumps({"summary": summary, "pairs": pairs}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[ok] 已写出 {out_dir / 'walk_calibration.json'}（报告会自动收录该章节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
