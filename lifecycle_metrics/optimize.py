"""选址优化 —— 从"诊断"走到"处方"，并且**推荐的点位是真的能建的**。

模型：以居住组团为需求点、户数为权重，在"可建载体周边"的候选地块里选 K 处新增设施，
最大化 新增覆盖户数 × 梯队系数。

为什么不是纯覆盖最大化：上海市区的便民设施几乎不可能靠空地新建（未招标/荒地只在郊区出现），
所以候选点必须落在**既有载体**周边；并且要排除水体、党政机关/公安消防、校园与院区内部、
市政设施等不宜区。载体优先级来自赛题方的"优先选址排序"（五梯队）：

    第一梯队 地铁站/交通节点周边 50–150 m
    第二梯队 街道/社区服务中心、党群服务中心、居委会一楼
    第三梯队 商业综合体、超市、菜市场、写字楼大堂
    第四梯队 医院、图书馆、公园、广场、体育场馆入口（院区内部除外）
    第五梯队 公交枢纽、地下通道、过街连廊、闲置亭棚

打分：score = 新增覆盖户数 × tier_coef，tier_coef = {1:1.5, 2:1.35, 3:1.2, 4:1.1, 5:1.0}
（加权而非严格分层：兼顾"优先地铁口"与"覆盖更多人"。）

数据（都在 dataset.meta，由 providers/baidu.py 与 providers/osm.py 采集）：
- meta["carriers"] / meta["carrier_meta"]        五梯队载体与环带
- meta["exclusions"] / meta["exclusion_meta"]    禁建区与排除半径
- meta["water_polygons"] / meta["water_lines"]   水体与河道（OSM）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .accessibility import AccessResult
from .config import element_map
from .geo import haversine_m, m_per_deg_lat, m_per_deg_lon
from .graph import RoadGraph
from .schema import Dataset

TIER_COEF: Dict[int, float] = {1: 1.5, 2: 1.35, 3: 1.2, 4: 1.1}
# 说明：赛题方的"第五梯队（公交枢纽/地下通道/闲置亭棚）"已放弃——百度 POI 取不到
# 这类点位（曹杨新村实测 0 处）。保留 1–4 梯队编号以对应原始口径。
FALLBACK_BAND = (0.0, 80.0)
CO_LOCATION_GAP: Dict[str, float] = {"independent": 250.0, "compatible": 60.0}
DEFAULT_SAME_KIND_GAP = 220.0


@dataclass
class Candidate:
    lon: float
    lat: float
    node: str
    tier: int = 0                    # 0 = 无载体信息（合成数据/旧数据集）
    carrier_name: str = ""
    carrier_kind: str = ""
    carrier_dist_m: float = 0.0
    tier_name: str = ""
    tier_note: str = ""
    tier_relation: str = ""          # 邻近 / 合署 —— 决定卡片上说"邻近载体"还是"可合署载体"


# ---------------------------------------------------------------------------
# 不宜区判定
# ---------------------------------------------------------------------------
def _point_in_ring(lon: float, lat: float, ring: List[List[float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1
            if lon < xin:
                inside = not inside
    return inside


def _dist_to_polyline_m(lon: float, lat: float, line: List[List[float]]) -> float:
    if len(line) < 2:
        return float("inf")
    mlon, mlat = m_per_deg_lon(lat), m_per_deg_lat()
    best = float("inf")
    for i in range(len(line) - 1):
        x1 = (line[i][0] - lon) * mlon
        y1 = (line[i][1] - lat) * mlat
        x2 = (line[i + 1][0] - lon) * mlon
        y2 = (line[i + 1][1] - lat) * mlat
        dx, dy = x2 - x1, y2 - y1
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / denom))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def _water_block(lon: float, lat: float, meta: Dict[str, Any]) -> Optional[str]:
    for ring in meta.get("water_polygons") or []:
        if _point_in_ring(lon, lat, ring):
            return "落在水面范围内"
    for line in meta.get("water_lines") or []:
        if _dist_to_polyline_m(lon, lat, line) < 25.0:
            return "距河道/水体不足 25 m"
    return None


def _exclusion_block(lon: float, lat: float, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for e in meta.get("exclusions") or []:
        if haversine_m(lon, lat, e["lon"], e["lat"]) <= e.get("radius_m", 45):
            return e
    return None


def _same_kind_gap(element: str, emap: Dict[str, Dict[str, Any]],
                   override: Optional[float]) -> float:
    if override is not None:
        return float(override)
    cfg = emap.get(element, {})
    return CO_LOCATION_GAP.get(str(cfg.get("co_location", "")), DEFAULT_SAME_KIND_GAP)


# ---------------------------------------------------------------------------
# 候选点
# ---------------------------------------------------------------------------
def _candidates_from_carriers(ds: Dataset, graph: RoadGraph, cell_m: float,
                              max_snap_m: float) -> Tuple[List[Candidate], Dict[str, int]]:
    meta = ds.meta or {}
    carriers = meta.get("carriers") or []
    cmeta = meta.get("carrier_meta") or {}
    center = ds.community["center"]
    mlon, mlat = m_per_deg_lon(center["lat"]), m_per_deg_lat()
    seen: set = set()
    out: List[Candidate] = []
    per_tier: Dict[str, int] = {}

    for c in carriers:
        kind = c.get("kind", "")
        info = cmeta.get(kind) or {}
        dmin, dmax = (info.get("band") or FALLBACK_BAND)
        steps = max(1, int(math.ceil(dmax / cell_m)))
        for ix in range(-steps, steps + 1):
            for iy in range(-steps, steps + 1):
                dx, dy = ix * cell_m, iy * cell_m
                dist = math.hypot(dx, dy)
                if dist < dmin or dist > dmax:
                    continue
                lon = c["lon"] + dx / mlon
                lat = c["lat"] + dy / mlat
                key = (round(lon, 5), round(lat, 5))
                if key in seen:
                    continue
                seen.add(key)
                nid, snap_m = graph.nearest_node(lon, lat)
                if nid is None or snap_m > max_snap_m:
                    continue
                out.append(Candidate(lon=round(lon, 7), lat=round(lat, 7), node=nid,
                                     tier=int(info.get("tier", 5)),
                                     carrier_name=c.get("name", ""), carrier_kind=kind,
                                     carrier_dist_m=round(dist, 1),
                                     tier_name=info.get("name", ""), tier_note=info.get("note", ""),
                                     tier_relation=info.get("relation", "")))
                per_tier[kind] = per_tier.get(kind, 0) + 1
    return out, per_tier


def _candidates_from_grid(ds: Dataset, graph: RoadGraph, cell_m: float,
                          max_snap_m: float) -> Tuple[List[Candidate], Dict[str, int]]:
    """兜底：没有载体信息时退回均匀网格（合成社区/旧数据集）。"""
    center = ds.community["center"]
    half_w = ds.community["extent_m"]["half_width"]
    half_h = ds.community["extent_m"]["half_height"]
    mlon, mlat = m_per_deg_lon(center["lat"]), m_per_deg_lat()
    ncols, nrows = int(2 * half_w / cell_m), int(2 * half_h / cell_m)
    out: List[Candidate] = []
    for r in range(nrows):
        for c in range(ncols):
            lon = center["lon"] + (c + 0.5 - ncols / 2) * cell_m / mlon
            lat = center["lat"] + (r + 0.5 - nrows / 2) * cell_m / mlat
            nid, snap_m = graph.nearest_node(lon, lat)
            if nid is None or snap_m > max_snap_m:
                continue
            out.append(Candidate(lon=round(lon, 7), lat=round(lat, 7), node=nid))
    return out, {"(无载体信息)": len(out)}


def _coverage_of_site(graph: RoadGraph, ds: Dataset, site_node: str, cutoff_s: float,
                      walk_speed: float) -> Dict[str, float]:
    dist = graph.dijkstra(site_node, cutoff_s)
    if not dist:
        return {}
    covered: Dict[str, float] = {}
    for rp in ds.residential:
        nid, snap_m = graph.nearest_node(rp.lon, rp.lat)
        if nid is None:
            continue
        t = dist.get(nid)
        if t is None:
            continue
        total = t + snap_m / max(walk_speed, 0.1)
        if total <= cutoff_s:
            covered[rp.id] = round(total, 1)
    return covered


def apply_sites(ds: Dataset, sites: List[Dict[str, Any]], element: str,
                name_prefix: str = "推荐新增") -> Dataset:
    """把推荐点位加成设施，返回新的 Dataset（用于计算"优化后"的覆盖率与覆盖图）。

    居住组团、路网都不变，所以可以直接复用同一张生活圈栅格，只重算可达性与指标。
    """
    from .schema import Facility
    added = [
        Facility(id=f"NEW{i:02d}", name=f"{name_prefix}·{s.get('carrier', {}).get('name') or element}",
                 element=element, lon=s["lon"], lat=s["lat"], capacity=1.0, source="planned",
                 tags={"tier": s.get("tier"), "carrier": (s.get("carrier") or {}).get("name", ""),
                       "reason": s.get("reason", ""), "planned": True})
        for i, s in enumerate(sites, start=1)
    ]
    meta = dict(ds.meta or {})
    meta["planned_sites"] = [{"id": f.id, "name": f.name, "element": element,
                              "lon": f.lon, "lat": f.lat, "tier": f.tags.get("tier")}
                             for f in added]
    return Dataset(community=ds.community, nodes=ds.nodes, edges=ds.edges,
                   residential=ds.residential, facilities=list(ds.facilities) + added, meta=meta)


def optimize_sites(ds: Dataset, elements_cfg: Dict[str, Any], element: Optional[str] = None,
                   k: int = 2, graph: Optional[RoadGraph] = None,
                   access: Optional[AccessResult] = None, cutoff_s: Optional[float] = None,
                   walk_speed: Optional[float] = None, cell_m: float = 40.0,
                   min_gap_m: Optional[float] = None, max_snap_m: float = 70.0,
                   use_carriers: bool = True) -> Dict[str, Any]:
    cutoff = cutoff_s or elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = walk_speed or elements_cfg.get("walk_speed_mps", 1.25)
    emap = element_map(elements_cfg)
    graph = graph or RoadGraph.from_dataset(ds)
    if access is None:
        from .accessibility import compute_access
        access = compute_access(ds, graph, cutoff, speed)

    hh = {r.id: r.households for r in ds.residential}
    sitable = [e for e in elements_cfg["elements"]
               if e.get("required") and e.get("sitable", False)]
    if element is None:
        ranked = sorted(sitable or elements_cfg["elements"],
                        key=lambda e: sum(hh[p.id] for p in ds.residential
                                          if access.by_element_min_time.get(e["key"], {}).get(p.id) is None),
                        reverse=True)
        element = ranked[0]["key"]

    src_counts = {p.id: len(access.elements_of(p.id).get(element, [])) for p in ds.residential}
    before_uncovered = [p.id for p in ds.residential if src_counts[p.id] == 0]
    before_hh = sum(hh[p] for p in before_uncovered)
    total_hh = ds.total_households()

    # ---------------- 候选点：载体环带 + 硬约束 ----------------
    meta = ds.meta or {}
    if use_carriers and meta.get("carriers"):
        raw_candidates, per_tier = _candidates_from_carriers(ds, graph, cell_m, max_snap_m)
        mode = "carriers"
    else:
        raw_candidates, per_tier = _candidates_from_grid(ds, graph, cell_m, max_snap_m)
        mode = "grid"

    gap = _same_kind_gap(element, emap, min_gap_m)
    existing_same = [f for f in ds.facilities if f.element == element]
    rejected = {"water": 0, "exclusion": 0, "same_kind_gap": 0}
    exclusion_samples: List[Dict[str, Any]] = []
    water_samples: List[Dict[str, Any]] = []
    candidates: List[Candidate] = []
    for c in raw_candidates:
        w = _water_block(c.lon, c.lat, meta)
        if w:
            rejected["water"] += 1
            if len(water_samples) < 3:
                water_samples.append({"lon": c.lon, "lat": c.lat, "reason": w})
            continue
        e = _exclusion_block(c.lon, c.lat, meta)
        if e:
            rejected["exclusion"] += 1
            if len(exclusion_samples) < 3:
                exclusion_samples.append({"lon": c.lon, "lat": c.lat, "reason": e.get("reason"),
                                          "near": e.get("name")})
            continue
        if any(haversine_m(c.lon, c.lat, f.lon, f.lat) < gap for f in existing_same):
            rejected["same_kind_gap"] += 1
            continue
        candidates.append(c)

    # ---------------- 贪心（加权得分） ----------------
    site_cov: Dict[int, Dict[str, float]] = {
        idx: _coverage_of_site(graph, ds, cand.node, cutoff, speed)
        for idx, cand in enumerate(candidates)}

    def coef_of(cand: Candidate) -> float:
        return TIER_COEF.get(cand.tier, 1.0) if cand.tier else 1.0

    base_covered = set(p.id for p in ds.residential if src_counts[p.id] > 0)
    chosen: List[int] = []
    for _ in range(max(0, k)):
        best_idx, best_score, best_new = None, -1.0, []
        for idx, cand in enumerate(candidates):
            if idx in chosen:
                continue
            if any(haversine_m(cand.lon, cand.lat, candidates[j].lon, candidates[j].lat) < gap
                   for j in chosen):
                continue
            new_pts = [pid for pid in site_cov[idx] if pid not in base_covered]
            gain = sum(hh[pid] for pid in new_pts)
            if gain <= 0:
                continue
            score = gain * coef_of(cand)
            if score > best_score:
                best_idx, best_score, best_new = idx, score, new_pts
        if best_idx is None:
            break
        chosen.append(best_idx)
        base_covered.update(best_new)

    def total_score(sel: List[int]) -> Tuple[float, set]:
        pts = set(p.id for p in ds.residential if src_counts[p.id] > 0)
        score = 0.0
        for idx in sel:
            new_pts = [pid for pid in site_cov[idx] if pid not in pts]
            score += sum(hh[pid] for pid in new_pts) * coef_of(candidates[idx])
            pts.update(new_pts)
        return score, pts

    best_val, best_pts = total_score(chosen)
    improved, rounds = True, 0
    while improved and rounds < 3 and candidates:
        improved, rounds = False, rounds + 1
        for slot in range(len(chosen)):
            for idx in range(len(candidates)):
                if idx in chosen:
                    continue
                trial = list(chosen)
                trial[slot] = idx
                val, pts = total_score(trial)
                if val > best_val:
                    chosen, best_val, best_pts, improved = trial, val, pts, True
                    break

    # ---------------- 输出 ----------------
    sites: List[Dict[str, Any]] = []
    for idx in chosen:
        c = candidates[idx]
        new_pts = [pid for pid in site_cov[idx] if src_counts[pid] == 0]
        gain = sum(hh[p] for p in new_pts)
        coef = coef_of(c)
        reason = (f"第 {c.tier} 梯队（{c.tier_name}）：{c.tier_relation or '邻近'}"
                  f"「{c.carrier_name}」约 {c.carrier_dist_m:.0f} m。{c.tier_note}。" if c.tier
                  else "未提供可建载体信息，按均匀网格给出候选点，仅供演示。")
        nearest_existing = min([haversine_m(c.lon, c.lat, f.lon, f.lat) for f in existing_same],
                               default=None)
        sites.append({
            "lon": c.lon, "lat": c.lat, "node": c.node,
            "tier": c.tier, "tier_name": c.tier_name, "tier_coef": coef,
            "carrier": {"name": c.carrier_name, "kind": c.carrier_kind,
                        "distance_m": c.carrier_dist_m, "note": c.tier_note,
                        "relation": c.tier_relation or "邻近"},
            "reason": reason,
            "constraints": {
                "in_water": False, "in_exclusion": False, "same_kind_gap_m": gap,
                "nearest_existing_same_m": round(nearest_existing) if nearest_existing else None,
            },
            "score": round(gain * coef, 1),
            "covered_points": sorted(site_cov[idx].keys()),
            "newly_covered_points": sorted(new_pts),
            "newly_covered_households": gain,
        })
    sites.sort(key=lambda s: -s["score"])

    after_uncovered = [p for p in before_uncovered if p not in best_pts]
    after_hh = sum(hh[p] for p in after_uncovered)
    water_features = len(meta.get("water_polygons") or []) + len(meta.get("water_lines") or [])
    return {
        "element": element,
        "element_name": emap.get(element, {}).get("name", element),
        "k_requested": k,
        "k_selected": len(sites),
        "candidate_mode": mode,
        "candidates_considered": len(candidates),
        "candidates_by_tier": per_tier,
        "rejected": rejected,
        "rejected_samples": {"water": water_samples, "exclusion": exclusion_samples},
        "siting_basis": {
            "carriers_available": len(meta.get("carriers") or []),
            "carrier_counts": meta.get("carrier_counts") or {},
            "exclusions_available": len(meta.get("exclusions") or []),
            "water_features": water_features,
            "same_kind_gap_m": gap,
            "co_location": emap.get(element, {}).get("co_location", ""),
            "tier_coef": TIER_COEF,
            "note": meta.get("siting_note", ""),
        },
        "cutoff_s": cutoff,
        "before": {
            "uncovered_points": before_uncovered,
            "uncovered_households": before_hh,
            "uncovered_population": int(before_hh * elements_cfg.get("avg_household_size", 2.6)),
            "coverage_rate": round(1 - before_hh / total_hh, 4) if total_hh else 0.0,
        },
        "after": {
            "uncovered_points": after_uncovered,
            "uncovered_households": after_hh,
            "uncovered_population": int(after_hh * elements_cfg.get("avg_household_size", 2.6)),
            "coverage_rate": round(1 - after_hh / total_hh, 4) if total_hh else 0.0,
        },
        "gain": {
            "households": before_hh - after_hh,
            "population": int((before_hh - after_hh) * elements_cfg.get("avg_household_size", 2.6)),
            "coverage_rate_pp": round(((before_hh - after_hh) / total_hh) * 100, 1) if total_hh else 0.0,
        },
        "sitable": bool(emap.get(element, {}).get("sitable", False)),
        "sites": sites,
        "method": ("候选点为可建载体影响范围内的路网可达点（按 1–4 梯队赋权）；"
                   "硬约束为不落水体、不进禁建区、与同类既有设施间距达标；"
                   "排序依据为贪心最大覆盖 × 梯队系数，并以 1-opt 局部搜索改进。"),
    }
