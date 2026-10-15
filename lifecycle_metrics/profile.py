"""新增设施画像：把"贪心算法挑出来的一个点"整理成一份能交给街道的选址建议。

优化器只回答"建在哪、覆盖多少户"；落到实施还要回答：
    这里建成什么形态？多大规模？多少居民受益、最远走几分钟？跟谁合署？谁来审批？
本模块把这些从已有数据里算出来（不新增任何假设数据），并与 config/elements.json 的
`build_notes`（工程建议，非标准强制条款）拼成结构化画像。

**不编数据**：服务人口 = 新增覆盖户数 × 平均户均人口；步行时间 = 路网最短路；
规模建议来自配置里的工程建议字段并显式标注"非标准强制条款"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import element_map
from .geo import haversine_m
from .graph import RoadGraph
from .schema import Dataset

# 与推荐点位"可合署"的判定半径：同址/隔壁铺面才谈得上合署
CO_LOCATION_RADIUS_M = 200.0
# 画像里给出"受益组团明细"的条数上限（面板/报告别太长）
MAX_LISTED_GROUPS = 40


def site_profile(ds: Dataset, graph: RoadGraph, elements_cfg: Dict[str, Any],
                 site: Dict[str, Any], walk_speed: Optional[float] = None,
                 cutoff_s: Optional[float] = None) -> Dict[str, Any]:
    """单个推荐点位的画像（JSON 可序列化）。"""
    speed = walk_speed or elements_cfg.get("walk_speed_mps", 1.25)
    cutoff = cutoff_s or elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    hh_size = elements_cfg.get("avg_household_size", 2.6)
    emap = element_map(elements_cfg)
    el = emap.get(site.get("element") or site.get("element_key") or "")
    element = (site.get("element") or (el or {}).get("key") or "")
    name = (el or {}).get("name", element)
    notes = (el or {}).get("build_notes") or {}

    # ---- 1) 受益组团 + 到该点的步行时间（一次 Dijkstra）----
    snode, snap_m = graph.nearest_node(site["lon"], site["lat"])
    dist = graph.dijkstra(snode, cutoff) if snode else {}
    covered_ids = set(site.get("newly_covered_points") or [])
    groups: List[Dict[str, Any]] = []
    for rp in ds.residential:
        nid, rs = graph.nearest_node(rp.lon, rp.lat)
        t = dist.get(nid) if nid else None
        if t is None:
            continue
        minutes = (t + (snap_m + rs) / max(speed, 0.1)) / 60.0
        groups.append({"id": rp.id, "name": rp.name, "households": rp.households,
                       "population": int(rp.households * hh_size),
                       "walk_min": round(minutes, 1),
                       "is_new": rp.id in covered_ids})
    groups.sort(key=lambda g: g["walk_min"])
    new_groups = [g for g in groups if g["is_new"]]
    new_pop = sum(g["population"] for g in new_groups)
    times = [g["walk_min"] for g in new_groups] or [g["walk_min"] for g in groups]

    # ---- 2) 服务能力核对（用配置里的工程假设值，明确标注）----
    per_unit = (el or {}).get("service_pop_per_unit")
    cap: Dict[str, Any] = {}
    if per_unit:
        need = new_pop / float(per_unit)
        cap = {
            "assumed_service_pop_per_unit": per_unit,
            "covered_population": new_pop,
            "units_implied": round(need, 2),
            "verdict": ("一处即可承担（新增覆盖人口低于该要素的单处服务人口假设）" if need <= 1.0
                        else f"按该假设偏紧：新增覆盖人口相当于 {need:.1f} 处的服务量，"
                             f"建议同步考虑合署扩容或分期实施"),
            "caveat": elements_cfg.get("service_pop_note", ""),
        }

    # ---- 3) 可合署的既有设施（同类要间距，异类可共享）----
    complement: List[Dict[str, Any]] = []
    for f in ds.facilities:
        if f.element == element or f.element not in emap:
            continue
        if (emap[f.element] or {}).get("co_location") != "compatible":
            continue
        d = haversine_m(site["lon"], site["lat"], f.lon, f.lat)
        if d <= CO_LOCATION_RADIUS_M:
            complement.append({"element": f.element, "element_name": emap[f.element]["name"],
                               "facility": f.name, "distance_m": round(d)})
    complement.sort(key=lambda c: c["distance_m"])

    same_kind = [f for f in ds.facilities if f.element == element]
    nearest_same = min((haversine_m(site["lon"], site["lat"], f.lon, f.lat) for f in same_kind),
                       default=None)

    return {
        "element": element, "element_name": name,
        "tier": site.get("tier"), "tier_name": site.get("tier_name"),
        "carrier": site.get("carrier"),
        "newly_covered_households": site.get("newly_covered_households"),
        "service_population": new_pop,
        "benefit_count": len(new_groups),
        "walk_stats": {
            "median_min": round(sorted(times)[len(times) // 2], 1) if times else None,
            "max_min": round(max(times), 1) if times else None,
            "within_10min_share": (round(sum(1 for t in times if t <= 10) / len(times), 3)
                                   if times else None),
        },
        "groups": new_groups[:MAX_LISTED_GROUPS],
        "groups_total": len(new_groups),
        "capacity": cap,
        "build": {
            "form": notes.get("form") or notes.get("build_form"),
            "space": notes.get("space") or notes.get("space_hint"),
            "approval": notes.get("approval"),
            "carrier_note": (site.get("carrier") or {}).get("note"),
            "caveat": notes.get("caveat"),
        },
        "complement": complement[:6],
        "existing_same_kind": {"count": len(same_kind),
                               "nearest_m": round(nearest_same) if nearest_same else None,
                               "required_gap_m": (site.get("constraints") or {}).get("same_kind_gap_m")},
        "reason": site.get("reason"),
    }


def enrich_optimization(ds: Dataset, graph: RoadGraph, elements_cfg: Dict[str, Any],
                        opt: Dict[str, Any], walk_speed: Optional[float] = None) -> Dict[str, Any]:
    """给 optimize_sites() 的结果里每个点位补上画像（就地修改并返回）。"""
    element = opt.get("element")
    for s in opt.get("sites") or []:
        s.setdefault("element", element)
        s["profile"] = site_profile(ds, graph, elements_cfg, s, walk_speed=walk_speed)
    return opt
