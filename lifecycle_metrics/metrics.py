"""体检指标引擎 —— 六项指标 + 综合得分 + 问题清单。

每项指标的口径与出处见 docs/metrics.md，权重来自 config/elements.json 的
composite_weights，代码里不写死任何标准值。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .accessibility import (AccessResult, CircleGrid, build_circle_grid,  # noqa: F401
                            compute_access, element_access_grid)
from .config import element_map, required_elements
from .geo import M_PER_DEG_LAT, m_per_deg_lon, weighted_gini
from .graph import RoadGraph
from .network import analyze_network
from .schema import Dataset

GRADE_BANDS = [(85, "优良"), (70, "良好"), (55, "一般"), (0, "短板明显")]


def in_analysis_box(ds: Dataset, lon: float, lat: float, margin_m: float = 0.0) -> bool:
    """点是否落在社区**分析范围**内（可选外扩 margin_m）。

    为什么要区分：OSM 抓取范围外扩了 1200 m（让贴边组团的 15 分钟圈不被硬切一刀），
    于是路网和设施都会带进来一批框外的。**可达性必须用全部设施**（框外那家菜场
    居民照样走得到），但"本社区有多少处设施"这类统计口径要按分析范围算，
    否则不同社区的设施数没法横向比较。
    """
    c = ds.community.get("center") or {}
    e = ds.community.get("extent_m") or {}
    if not c or not e:
        return True
    hw = (e.get("half_width", 800) + margin_m) / max(1.0, m_per_deg_lon(c["lat"]))
    hh = (e.get("half_height", 800) + margin_m) / M_PER_DEG_LAT
    return (c["lon"] - hw <= lon <= c["lon"] + hw) and (c["lat"] - hh <= lat <= c["lat"] + hh)


def _grade(score: float) -> str:
    for lo, name in GRADE_BANDS:
        if score >= lo:
            return name
    return "短板明显"


def _wmean(pairs: List[tuple[float, float]]) -> float:
    """(value, weight) 加权平均；权重全 0 时退化为算术平均。"""
    tw = sum(w for _, w in pairs)
    if tw <= 0:
        return sum(v for v, _ in pairs) / max(len(pairs), 1)
    return sum(v * w for v, w in pairs) / tw


def diagnose(ds: Dataset, elements_cfg: Dict[str, Any], cutoff_s: Optional[float] = None,
             walk_speed: Optional[float] = None, graph: Optional[RoadGraph] = None,
             access: Optional[AccessResult] = None,
             access_5min: Optional[AccessResult] = None,
             grid: Optional[CircleGrid] = None) -> Dict[str, Any]:
    cutoff = cutoff_s or elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    near_cutoff = elements_cfg["cutoffs"]["circle_5min"]["time_s"]
    speed = walk_speed or elements_cfg.get("walk_speed_mps", 1.25)
    hh_size = elements_cfg.get("avg_household_size", 2.6)
    req = required_elements(elements_cfg)
    emap = element_map(elements_cfg)

    graph = graph or RoadGraph.from_dataset(ds)
    access = access or compute_access(ds, graph, cutoff, speed)
    access_5min = access_5min or compute_access(ds, graph, near_cutoff, speed)
    grid = grid if grid is not None else build_circle_grid(ds, graph, cutoff, walk_speed=speed)
    grid_counts = element_access_grid(ds, graph, elements_cfg, grid, cutoff, speed)

    total_hh = ds.total_households()
    hh = {r.id: r.households for r in ds.residential}
    facilities = ds.facilities_by_element()
    # 设施统计口径：分析范围内的设施（抓取范围外扩带进来的框外设施只参与可达性计算）
    in_box = {f.id for f in ds.facilities if in_analysis_box(ds, f.lon, f.lat)}

    # ---------------- 路网连通性：分清真缺口与"数据缺口" ----------------
    # 掉进"一处设施都没有"的孤立小路里的组团，其"15 分钟可达 0 项"是 OSM 路网没接上
    # 造成的假象（实测：小区内部路画到离大马路 50 m 处就停了）。这类组团按"无法评估"
    # 处理：不参与人口加权的分母，也不进问题清单，单列在 data_quality 里如实披露。
    net = analyze_network(ds, graph)
    assessable = {pid: v["status"] == "ok" for pid, v in net["points"].items()}
    assess_points = [p for p in ds.residential if assessable.get(p.id, True)]
    assess_hh = sum(hh[p.id] for p in assess_points)
    unassess_points = [p for p in ds.residential if not assessable.get(p.id, True)]
    unassess_hh = sum(hh[p.id] for p in unassess_points)

    # ---------------- 逐要素 ----------------
    element_rows: List[Dict[str, Any]] = []
    for e in elements_cfg["elements"]:
        key = e["key"]
        facs = facilities.get(key, [])
        covered_pts = [p.id for p in assess_points
                       if access.by_element_min_time.get(key, {}).get(p.id) is not None]
        near_pts = [p.id for p in assess_points
                    if access_5min.by_element_min_time.get(key, {}).get(p.id) is not None]
        covered_hh = sum(hh[p] for p in covered_pts)
        near_hh = sum(hh[p] for p in near_pts)
        times = sorted(access.by_element_min_time.get(key, {}).get(p.id)
                       for p in assess_points
                       if access.by_element_min_time.get(key, {}).get(p.id) is not None)
        multi = [p for p in assess_points
                 if len(access.elements_of(p.id).get(key, [])) >= 2]
        solo_hh = sum(hh[p.id] for p in assess_points
                      if len(access.elements_of(p.id).get(key, [])) == 1)

        supply_pop = sum(f.capacity for f in facs) * e.get("service_pop_per_unit", 0)
        demand_pop = assess_hh * hh_size
        ratio = (supply_pop / demand_pop) if demand_pop else 0.0
        gap_index = max(0.0, 1.0 - ratio) if e.get("required") else max(0.0, 1.0 - ratio) * 0.5

        element_rows.append({
            "key": key,
            "name": e["name"],
            "category": e.get("category", ""),
            "required": bool(e.get("required")),
            "source": e.get("source", ""),
            "facility_count": sum(1 for f in facs if f.id in in_box),
            "facility_count_fetched": len(facs),
            "coverage_rate": round(covered_hh / assess_hh, 4) if assess_hh else 0.0,
            "coverage_rate_points": round(len(covered_pts) / max(len(assess_points), 1), 4),
            "covered_households": covered_hh,
            "proximity_5min_rate": round(near_hh / assess_hh, 4) if assess_hh else 0.0,
            "single_dependency_rate": round(solo_hh / covered_hh, 4) if covered_hh else 0.0,
            "redundancy_points": len(multi),
            "median_time_s": round(times[len(times) // 2], 1) if times else None,
            "max_time_s": round(times[-1], 1) if times else None,
            "supply_pop": round(supply_pop, 1),
            "demand_pop": round(demand_pop, 1),
            "supply_demand_ratio": round(ratio, 3),
            "gap_index": round(gap_index, 3),
            "uncovered_points": [p.id for p in assess_points
                                 if access.by_element_min_time.get(key, {}).get(p.id) is None],
        })

    # ---------------- 逐居住组团 ----------------
    point_rows: List[Dict[str, Any]] = []
    for rp in ds.residential:
        have = access.elements_of(rp.id)
        missing = [e["key"] for e in req if not have.get(e["key"])]
        status = net["points"].get(rp.id, {}).get("status", "ok")
        score = 1.0 - len(missing) / max(len(req), 1)
        point_rows.append({
            "id": rp.id,
            "name": rp.name,
            "households": rp.households,
            "population": int(rp.households * hh_size),
            "across_river": bool(rp.tags.get("across_river")),
            "built_year": rp.built_year,
            "network_status": status,
            "assessable": status == "ok",
            "component_nodes": net["points"].get(rp.id, {}).get("component_nodes"),
            "compliance_score": round(score, 4),
            "missing": missing,
            "access_time_s": {e["key"]: access.by_element_min_time.get(e["key"], {}).get(rp.id)
                              for e in req},
        })

    # ---------------- 维度得分（只统计"可评估"的组团）----------------
    req_rows = [r for r in element_rows if r["required"]]
    scored_rows = [p for p in point_rows if p["assessable"]]

    # 每个必需要素等权（要素之间不比较重要性），要素内部按户数加权
    coverage = _wmean([(r["coverage_rate"], 1.0) for r in req_rows])
    compliance = _wmean([(p["compliance_score"], p["households"]) for p in scored_rows])
    supply_demand = _wmean([(1.0 - r["gap_index"], 1.0) for r in req_rows])
    redundancy = _wmean([(1.0 - r["single_dependency_rate"], 1.0) for r in req_rows])
    gini = weighted_gini([p["compliance_score"] for p in scored_rows],
                         [p["households"] for p in scored_rows])
    fairness = max(0.0, 1.0 - gini)
    proximity = _wmean([(r["proximity_5min_rate"], 1.0) for r in req_rows])

    dims = {
        "coverage": {"score": round(coverage, 4), "label": "15 分钟可达覆盖率",
                     "detail": "必需要素的居住人口加权覆盖比例"},
        "compliance": {"score": round(compliance, 4), "label": "要素达标率",
                       "detail": "必需要素全部可达的居住人口占比"},
        "supply_demand": {"score": round(supply_demand, 4), "label": "供需均衡度",
                          "detail": "设施服务能力与覆盖人口的匹配程度（1 - 缺口指数）"},
        "redundancy": {"score": round(redundancy, 4), "label": "冗余度（抗风险）",
                       "detail": "同一要素有 2 处以上可达的人口占比（1 - 单一依赖率）"},
        "fairness": {"score": round(fairness, 4), "label": "公平性",
                     "detail": "各居住组团达标得分的加权 Gini 补值"},
        "proximity_5min": {"score": round(proximity, 4), "label": "5 分钟近距便利度",
                           "detail": "5 分钟步行可达要素的人口占比（便利度梯度）"},
    }
    w = elements_cfg.get("composite_weights", {})
    composite = sum(dims[k]["score"] * w.get(k, 0.0) for k in dims) * 100 / max(sum(w.values()), 1e-9)

    # ---------------- 问题清单（只针对"可评估"的组团）----------------
    problems: List[Dict[str, Any]] = []
    for r in sorted(req_rows, key=lambda x: x["coverage_rate"]):
        if r["coverage_rate"] < 0.98:
            affected = sum(p["households"] for p in scored_rows if r["key"] in p["missing"])
            problems.append({
                "level": "高" if r["coverage_rate"] < 0.75 else "中",
                "element": r["key"],
                "title": f"{r['name']}：15 分钟可达覆盖率 {r['coverage_rate'] * 100:.1f}%",
                "detail": f"现有设施 {r['facility_count']} 处；覆盖人口的中位步行时间 "
                          f"{r['median_time_s'] if r['median_time_s'] is not None else '—'} 秒；"
                          f"{len(r['uncovered_points'])} 个居住组团在 15 分钟内不可达。",
                "affected_households": affected,
                "affected_population": int(affected * hh_size),
                "uncovered_points": r["uncovered_points"],
            })
    if gini > 0.22:
        problems.append({
            "level": "中", "element": "_fairness",
            "title": f"组团间达标水平差异显著（加权 Gini {gini:.2f}）",
            "detail": "各居住组团的达标得分离散度偏高，存在服务覆盖洼地。",
            "affected_households": sum(p["households"] for p in scored_rows
                                       if p["compliance_score"] < 1.0),
            "affected_population": int(sum(p["households"] for p in scored_rows
                                           if p["compliance_score"] < 1.0) * hh_size),
            "uncovered_points": [p["id"] for p in scored_rows if p["compliance_score"] < 1.0],
        })
    problems.sort(key=lambda p: -p["affected_households"])

    # ---------------- 数据质量（如实披露"评估不了"的部分）----------------
    data_quality = {
        "unassessable_points": [{"id": p.id, "name": p.name,
                                 "households": p.households,
                                 "component_nodes": net["points"].get(p.id, {}).get("component_nodes")}
                                for p in unassess_points],
        "unassessable_households": unassess_hh,
        "unassessable_population": int(unassess_hh * hh_size),
        "network": {
            "components": net["components"],
            "component_sizes": net["component_sizes"],
            "largest_share": net["largest_share"],
            "stitched_edges": len(ds.meta.get("stitched_edges") or []),
            "stitched_note": ds.meta.get("stitched_note"),
            "isolated_facilities": net["isolated_facilities"],
        },
        "note": ("掉进「路网未连通」小路的组团，其「可达 0 项」是 OSM 路网缺口造成的，"
                 "不能算作设施缺口：已按「无法评估」处理，不计入覆盖率与问题清单。"
                 "断头路补连规则见 lifecycle_metrics/network.py（只连 ≤60 m、跨水不连）。"),
    }

    return {
        "community": ds.community,
        "meta": ds.meta,
        "cutoff": {"time_s": cutoff, "label": elements_cfg["cutoffs"]["circle_15min"]["label"],
                   "walk_speed_mps": speed, "avg_household_size": hh_size},
        "summary": {
            "residential_points": len(ds.residential),
            "households": total_hh,
            "population": int(total_hh * hh_size),
            "assessable_points": len(assess_points),
            "assessable_households": assess_hh,
            "assessable_population": int(assess_hh * hh_size),
            "unassessable_points": len(unassess_points),
            "unassessable_households": unassess_hh,
            "unassessable_population": int(unassess_hh * hh_size),
            "facilities": len(in_box),
            "facilities_fetched": len(ds.facilities),
            "road_nodes": len(ds.nodes),
            "road_edges": len(ds.edges),
            "road_nodes_outside_box": sum(
                1 for n in ds.nodes if not in_analysis_box(ds, n.lon, n.lat)),
            "fetch_margin_m": ds.meta.get("fetch_margin_m"),
            "network_components": net["components"],
            "stitched_edges": len(ds.meta.get("stitched_edges") or []),
            "river_removed_crossings": ds.meta.get("removed_crossings"),
            "bridges": ds.meta.get("bridges"),
            "composite_score": round(composite, 1),
            "grade": _grade(composite),
            "gini": round(gini, 4),
            "circle_area_km2": round(grid.area_within(cutoff) / 1e6, 3),
        },
        "dimensions": dims,
        "elements": element_rows,
        "points": point_rows,
        "problems": problems,
        "data_quality": data_quality,
        "grid_counts": grid_counts,
        # 步行等时圈：每个栅格"从任一居住组团出发的最短步行时间"（秒，-1 = 15 分钟内不可达）。
        # 与 grid_counts 同栅格，前端可切换「可达要素数量 / 步行等时圈」两种底图；
        # 不着色的格子就是生活圈之外。
        "grid_times": {
            "times": [[(-1 if t == float("inf") else int(t)) for t in row] for row in grid.times],
            "bands_s": [300, 600, 900],
            "note": "并集口径：取所有居住组团中最早能走到该格的时间；-1 表示 15 分钟内不可达。",
        },
        "grid_meta": {"lon0": grid.lon0, "lat0": grid.lat0, "cell_m": grid.cell_m,
                      "ncols": grid.ncols, "nrows": grid.nrows},
        "grid": grid,
        "access": access,
    }


def strip_runtime(diag: Dict[str, Any]) -> Dict[str, Any]:
    """去掉不能 JSON 序列化的运行时对象（grid/access），用于落盘与 API 输出。"""
    return {k: v for k, v in diag.items() if k not in ("grid", "access")}
