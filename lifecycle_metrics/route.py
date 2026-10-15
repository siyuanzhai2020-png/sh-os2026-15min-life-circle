"""一天动线：把"某类住户的一天"沿真实步行路网走一遍。

三个层次（报告的"规划"部分就建立在这上面）：

1. **画像层**：`config/personas.json` 里 5 类典型住户。每类给出
   出行链（chain）、停留时长、开放时段（windows）、步速、可接受单段步行时间。
   刻意**不用大模型现场判人**：规则表可核对、可复现、离线可用；界面上的滑杆
   再把它从"一类人"调成"这个人"。
2. **时序层**：设施不是全天可用。菜市场 12:00 收市、卫生站午间停诊、
   幼儿园 08:30 关门。到达时若已闭门 / 未开门，记成"服务时段冲突"并给出建议 ——
   这一步让动线从"几何串点"变成"真的过一天"。
3. **步速层**：时间 = 路网长度 / 该人群步速。同一条路，老人 15 分钟只能走
   765 m，成人能走 1125 m，所以不同画像的"15 分钟生活圈"大小天然不同。

口径提示：图上的路网时间是按体检口径的步速预算好的，换步速时**按比例缩放**
（time = length / v），不重建路网，保证两条口径算的是同一张图。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .geo import haversine_m
from .graph import RoadGraph
from .schema import Dataset

# 情景假设：一次到访的典型停留时长（分钟）。交通类站点只是"顺路经过"，不计停留。
DEFAULT_STAY_MIN: Dict[str, int] = {
    "market": 15, "canteen": 40, "kindergarten": 10, "primary_school": 8,
    "community_health": 20, "pharmacy": 8, "culture_center": 45, "fitness": 40,
    "park": 40, "convenience": 5, "bus_stop": 0, "metro": 0,
}

# 生活动线的自然顺序（早上买菜 → 送孩子 → 看病取药 → 午饭 → 下午活动 → 晚上散步）
DAY_ORDER: List[str] = ["market", "kindergarten", "primary_school", "community_health",
                        "pharmacy", "canteen", "culture_center", "fitness", "park",
                        "convenience", "bus_stop", "metro"]

DEFAULT_START_CLOCK = "07:30"
SEARCH_FACTOR = 2.0          # 缺口要素"最近一处在几分钟外"的搜索倍数
WAIT_TOLERANCE_MIN = 15.0    # 早到 15 分钟以内就地等，超过则算"服务时段冲突"


def _parse_clock(text: str) -> int:
    try:
        hh, mm = str(text).split(":")
        return int(hh) * 60 + int(mm)
    except Exception:                                            # noqa: BLE001
        return 7 * 60 + 30


def _fmt_clock(minutes: float) -> str:
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _windows_text(wins: Optional[Sequence[Sequence[int]]]) -> str:
    if not wins:
        return ""
    return " / ".join(f"{_fmt_clock(a)}–{_fmt_clock(b)}" for a, b in wins)


def _align_window(clock: float, wins: Optional[Sequence[Sequence[int]]]
                  ) -> tuple[float, float, Optional[Dict[str, Any]]]:
    """把到达时刻对齐到开放时段。

    返 (新时刻, 就地等待分钟, 冲突)。三种情形：
    - 已在时段内：原样通过；
    - 还没开门且等待 ≤ WAIT_TOLERANCE_MIN：就地等，计入 wait_min；
    - 还没开门但等待太久 / 已经闭门：**不等待**，按实际到达时刻继续，
      把这件事记成"服务时段冲突"由上层给出建议（不偷偷把一天拉长 2 小时）。
    """
    if not wins:
        return clock, 0.0, None
    for a, b in wins:
        if a <= clock <= b:
            return clock, 0.0, None
    nxt = min((a for a, _b in wins if a > clock), default=None)
    if nxt is not None:
        wait = nxt - clock
        if wait <= WAIT_TOLERANCE_MIN:
            return nxt, wait, None
        return clock, 0.0, {"kind": "early", "opens": _fmt_clock(nxt), "gap_min": round(wait, 1)}
    last_close = max(b for _a, b in wins)
    return clock, 0.0, {"kind": "closed", "closes": _fmt_clock(last_close),
                        "over_min": round(clock - last_close, 1)}


def _elements_of(ds: Dataset, elements_cfg: Dict[str, Any], requested: Optional[Sequence[str]],
                 include_optional: bool) -> List[Dict[str, Any]]:
    out = []
    for e in elements_cfg.get("elements", []):
        if requested:
            if e["key"] in requested:
                out.append(e)
        elif e.get("required") or include_optional:
            out.append(e)
    return out


def plan_day_route(ds: Dataset, graph: RoadGraph, point_id: str, elements_cfg: Dict[str, Any],
                   walk_speed: Optional[float] = None, elements: Optional[Sequence[str]] = None,
                   stay_min: Optional[Dict[str, float]] = None, order: str = "day",
                   include_optional: bool = False, start_clock: str = DEFAULT_START_CLOCK,
                   return_home: bool = True,
                   persona: Optional[Dict[str, Any]] = None,
                   order_keys: Optional[Sequence[str]] = None,
                   windows: Optional[Dict[str, Sequence[Sequence[int]]]] = None,
                   max_leg_min: Optional[float] = None) -> Dict[str, Any]:
    """规划一个居住组团的一天动线。返回 JSON 可序列化的完整情景（含假设、冲突与建议）。

    persona 给出时，其中的步速/出行链/停留时长/开放时段作为**默认值**，
    显式传入的 walk_speed / elements / stay_min / start_clock 仍然优先（界面滑杆用）。
    """
    p = persona or {}
    cfg_speed = float(elements_cfg.get("walk_speed_mps", 1.25))
    speed = float(walk_speed if walk_speed is not None
                  else p.get("walk_speed_mps") or cfg_speed)
    speed = max(speed, 0.25)
    # 路网时间是按 cfg_speed 预算的：换步速只做比例缩放，不重建图。
    scale = cfg_speed / speed
    cutoff = elements_cfg["cutoffs"]["circle_15min"]["time_s"]
    gcut = cutoff * speed / cfg_speed          # 真实 15 分钟折算成"图上的秒"
    if p.get("start_clock") and start_clock == DEFAULT_START_CLOCK:
        start_clock = p["start_clock"]          # 未显式指定出发时刻时用画像的默认值
    if p.get("return_home") is False and return_home is True:
        return_home = False

    chain = list(order_keys or p.get("chain") or [])
    want = list(elements or chain)
    if not want and p.get("optional_chain") and include_optional:
        want = list(chain) + list(p["optional_chain"])
    wins = windows if windows is not None else (p.get("windows") or {})
    leg_limit = max_leg_min if max_leg_min is not None else p.get("max_leg_min")

    stay = dict(DEFAULT_STAY_MIN)
    stay.update({k: float(v) for k, v in (p.get("stay_min") or {}).items()})
    stay.update({k: float(v) for k, v in (stay_min or {}).items()})

    rp = next((r for r in ds.residential if r.id == point_id), None)
    if rp is None:
        raise KeyError(f"没有居住组团 {point_id}")

    from .network import analyze_network
    net_status = analyze_network(ds, graph)["points"].get(point_id, {}).get("status", "ok")

    home_node, home_snap = graph.nearest_node(rp.lon, rp.lat)
    if home_node is None:
        raise RuntimeError("该组团附近没有路网节点")

    el_by_key = {e["key"]: e for e in elements_cfg.get("elements", [])}

    # ---------- 1) 每个要素收集"从家 15 分钟内可达"的全部候选 ----------
    wide = graph.dijkstra(home_node, gcut * SEARCH_FACTOR)      # 一次算出 2 倍阈值内的全部设施
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    missing: List[Dict[str, Any]] = []
    for el in _elements_of(ds, elements_cfg, want or None, include_optional):
        items: List[Dict[str, Any]] = []
        for f in ds.facilities:
            if f.element != el["key"]:
                continue
            fnid, fsnap = graph.nearest_node(f.lon, f.lat)
            if fnid is None:
                continue
            t = wide.get(fnid)
            if t is None:
                continue
            off_s = (home_snap + fsnap) / speed
            items.append({"element": el["key"], "element_name": el["name"],
                          "facility_id": f.id, "facility_name": f.name,
                          "address": (f.tags or {}).get("address") or "",
                          "source": f.source, "lon": f.lon, "lat": f.lat, "node": fnid,
                          "off_road_m": round(fsnap, 1),
                          "home_min": round((t * scale + off_s) / 60, 1),
                          "_home_s": t * scale + off_s})
        if not items:
            missing.append({"element": el["key"], "name": el["name"],
                            "reason": f"{cutoff * SEARCH_FACTOR / 60:.0f} 分钟内均不可达",
                            "nearest_min": None})
            continue
        near = min(items, key=lambda x: x["_home_s"])
        if near["_home_s"] > cutoff:
            missing.append({"element": el["key"], "name": el["name"],
                            "reason": f"超出 15 分钟阈值（按本画像步速，最近一处约 "
                                      f"{near['_home_s'] / 60:.1f} 分钟）",
                            "nearest_min": round(near["_home_s"] / 60, 1)})
            continue
        candidates[el["key"]] = sorted([x for x in items if x["_home_s"] <= cutoff],
                                       key=lambda x: x["_home_s"])

    if not candidates:
        return _empty_route(rp, missing, elements_cfg, speed, start_clock, cutoff,
                            isolated=net_status != "ok", persona=p,
                            chain=chain, leg_limit=leg_limit)

    # ---------- 2) 到访顺序：两种排法都在同一批候选里挑，保证可比 ----------
    clock0 = _parse_clock(start_clock)
    day_tour = _build_tour(graph, home_node, candidates, speed, scale, mode="day",
                           order_keys=chain)
    near_tour = _build_tour(graph, home_node, candidates, speed, scale, mode="nearest")
    picked = near_tour if order == "nearest" else day_tour
    day_tour_s = _tour_seconds(graph, home_node, home_snap, day_tour, speed, scale) * scale
    near_tour_s = _tour_seconds(graph, home_node, home_snap, near_tour, speed, scale) * scale

    # ---------- 3) 逐段沿路网展开（含开放时段对齐） ----------
    stops: List[Dict[str, Any]] = []
    clock = float(clock0)
    walk_s = walk_m = wait_s = 0.0
    prev_node, prev_lon, prev_lat = home_node, rp.lon, rp.lat
    for i, s in enumerate(picked):
        path = graph.shortest_path(prev_node, s["node"])
        if path is None:
            missing.append({"element": s["element"], "name": s["element_name"],
                            "reason": "路段不连通（路网中断），未能接入动线",
                            "nearest_min": s["home_min"]})
            continue
        leg_nodes = path["nodes"]
        leg_min = path["time_s"] * scale / 60.0
        # 离路段：上一站（或家）离路距离 + 本站离路距离（两者都已在吸附时量过）
        stub_before = haversine_m(prev_lon, prev_lat, *graph.coords[leg_nodes[0]])
        stub_after = s["off_road_m"]
        stub_min = (stub_before + stub_after) / speed / 60.0
        total_min = leg_min + stub_min
        clock += total_min
        arrive_raw = clock
        wins_el = wins.get(s["element"]) if wins else None
        clock, waited, conflict = _align_window(clock, wins_el)
        wait_s += waited * 60.0
        arrive = clock
        clock += stay.get(s["element"], 0)
        walk_s += path["time_s"] * scale + (stub_before + stub_after) / speed
        walk_m += path["length_m"] + stub_before + stub_after
        over = leg_limit is not None and total_min > float(leg_limit) + 1e-9
        stops.append({
            **{k: s[k] for k in ("element", "element_name", "facility_id", "facility_name",
                                 "address", "source", "lon", "lat", "off_road_m", "home_min")},
            "seq": len(stops) + 1,
            "walk_min": round(total_min, 1),
            "walk_m": round(path["length_m"] + stub_before + stub_after),
            "stay_min": stay.get(s["element"], 0),
            "wait_min": round(waited, 1),
            "arrive_raw": _fmt_clock(arrive_raw),
            "within_15min": total_min <= 15.0,
            "over_leg_limit": bool(over),
            "leg_limit_min": leg_limit,
            "window": _windows_text(wins_el),
            "window_source": (p.get("window_source") or "") if wins_el else "",
            "conflict": conflict,
            "arrive": _fmt_clock(arrive),
            "leave": _fmt_clock(clock),
            "leg_nodes": leg_nodes,
            "leg_coords": [list(graph.coords[n]) for n in leg_nodes],
            "stub_from": [prev_lon, prev_lat] if (stub_before > 5 or i == 0) else None,
        })
        prev_node, prev_lon, prev_lat = s["node"], s["lon"], s["lat"]

    # ---------- 4) 回家 ----------
    home_leg = None
    if return_home and stops:
        path = graph.shortest_path(prev_node, home_node)
        if path is not None:
            stub = haversine_m(prev_lon, prev_lat, *graph.coords[path["nodes"][0]])
            back = haversine_m(rp.lon, rp.lat, *graph.coords[path["nodes"][-1]])
            tmin = (path["time_s"] * scale + (stub + back) / speed) / 60.0
            clock += tmin
            walk_s += path["time_s"] * scale + (stub + back) / speed
            walk_m += path["length_m"] + stub + back
            home_leg = {"walk_min": round(tmin, 1), "walk_m": round(path["length_m"] + stub + back),
                        "within_15min": tmin <= 15.0,
                        "over_leg_limit": bool(leg_limit is not None and tmin > float(leg_limit) + 1e-9),
                        "leave": _fmt_clock(clock),
                        "leg_coords": [list(graph.coords[n]) for n in path["nodes"]],
                        "stub_from": [prev_lon, prev_lat],
                        "leg_nodes": path["nodes"]}

    conflicts = [{"seq": s["seq"], "name": s["facility_name"], "element": s["element"],
                  "element_name": s["element_name"], "arrive_raw": s["arrive_raw"],
                  **s["conflict"]}
                 for s in stops if s["conflict"]]
    over_legs = [s for s in stops if s["over_leg_limit"]]
    recs = _recommendations(stops, conflicts, over_legs, missing, leg_limit, p, return_home)

    return {
        "point": {"id": rp.id, "name": rp.name, "lon": rp.lon, "lat": rp.lat,
                  "households": rp.households,
                  "population": int(rp.households * elements_cfg.get("avg_household_size", 2.6))},
        "persona": _persona_echo(p),
        "stops": stops,
        "missing": missing,
        "home_leg": home_leg,
        "network_status": net_status,
        "conflicts": conflicts,
        "recommendations": recs,
        "totals": {
            "stops": len(stops),
            "walk_min": round(walk_s / 60.0, 1),
            "walk_m": round(walk_m),
            "stay_min": round(sum(s["stay_min"] for s in stops), 1),
            "wait_min": round(wait_s / 60.0, 1),
            "span_min": round(clock - clock0, 1),
            "start_clock": _fmt_clock(clock0),
            "end_clock": _fmt_clock(clock),
            "missing_count": len(missing),
            "conflict_count": len(conflicts),
            "over_leg_count": len(over_legs),
            "complete": not missing,
        },
        "alternatives": {
            "chosen_min": round(walk_s / 60.0, 1),
            "day_order_min": round(day_tour_s / 60.0, 1),
            "nearest_order_min": round(near_tour_s / 60.0, 1),
        },
        "order_used": ("画像出行链（" + "→".join(el_by_key.get(k, {}).get("name", k)
                                                 for k in chain if k in {s["element"] for s in stops})
                       + "）") if (order != "nearest" and chain) else
                      ("最短路序（每次去当前最近的一处）" if order == "nearest"
                       else "生活动线序（买菜→接送→就医→午饭→活动→散步）"),
        "assumptions": {
            "start_clock": _fmt_clock(clock0),
            "walk_speed_mps": round(speed, 2),
            "speed_scale": round(scale, 3),
            "stay_min": {s["element"]: s["stay_min"] for s in stops},
            "stay_defaults": dict(DEFAULT_STAY_MIN) if not p.get("stay_min")
                             else {k: float(v) for k, v in p["stay_min"].items()},
            "cutoff_min": cutoff / 60.0,
            "max_leg_min": leg_limit,
            "return_home": return_home,
            "persona": p.get("key"),
            "window_source": p.get("window_source") or "",
            "note": "停留时长与出发时刻为情景假设（可调）；15 分钟生活圈指"
                    "「从家出发 15 分钟能到达的设施集合」，不是「一天只走 15 分钟」。"
                    + ("本画像步速与该阈值共同决定可达范围，故与体检口径"
                       "（成人 1.25 m/s）下的生活圈大小不同。" if p else ""),
        },
    }


def _persona_echo(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not p:
        return None
    return {k: p.get(k) for k in ("key", "name", "emoji", "who", "story", "walk_speed_mps",
                                  "start_clock", "max_leg_min", "chain", "window_source")
            if p.get(k) is not None}


def _recommendations(stops: List[Dict[str, Any]], conflicts: List[Dict[str, Any]],
                     over_legs: List[Dict[str, Any]], missing: List[Dict[str, Any]],
                     leg_limit: Optional[float], p: Dict[str, Any],
                     return_home: bool) -> List[Dict[str, str]]:
    """把出行链中的异常项转写成可执行的规划建议。

    文案口径：只陈述事实（时刻、分钟数、阈值）并给出可选处置方式，
    不使用口语化表达，也不替使用方做决定。
    """
    recs: List[Dict[str, str]] = []
    who = p.get("name") or "该住户"
    for c in conflicts:
        if c["kind"] == "closed":
            recs.append({"level": "高", "title": f"{c['element_name']}：到达时刻晚于服务结束时刻",
                         "detail": f"到达 {c['arrive_raw']}，服务已于 {c['closes']} 结束"
                                   f"（晚 {c['over_min']:.0f} 分钟），该次到访无效。"
                                   f"可选处置：将其调整至出行链前段，"
                                   f"或推动该设施延长服务时段。"})
        else:
            recs.append({"level": "中", "title": f"{c['element_name']}：到达时刻早于服务开始时刻",
                         "detail": f"服务自 {c['opens']} 开始，早于到达时刻 {c['gap_min']:.0f} 分钟。"
                                   f"可选处置：调整至出行链后段，先行办理已开放的服务。"})
    for m in missing:
        recs.append({"level": "高", "title": f"15 分钟阈值内无「{m['name']}」",
                     "detail": f"{m['reason']}。"
                               + (f"最近一处约 {m['nearest_min']} 分钟，超出阈值。"
                                  if m.get("nearest_min") else "该要素在本画像出行链中不可达。")
                               + "对应「规划建议」中的同要素补点。"})
    for s in over_legs:
        recs.append({"level": "中", "title": f"第 {s['seq']} 段步行时长超出画像阈值",
                     "detail": f"至「{s['facility_name']}」一段步行 {s['walk_min']} 分钟，"
                               f"超过{who}可接受的 {leg_limit:.0f} 分钟。"
                               f"可选处置：就近改选同类设施，或在该方向补充设施点位。"})
    if not return_home and stops:
        recs.append({"level": "低", "title": "本次出行不含返程",
                     "detail": "该画像为「出门上班」情景，出行链在最后一个到访点结束，不计算返程。"})
    if not conflicts and not missing and not over_legs:
        recs.append({"level": "低", "title": f"{who}的出行链完整可达",
                     "detail": "全部到访设施均处于开放时段内，各段步行时长亦未超出该画像阈值，无需调整。"})
    return recs


def _empty_route(rp, missing: List[Dict[str, Any]], elements_cfg: Dict[str, Any],
                 speed: float, start_clock: str, cutoff: float, isolated: bool = False,
                 persona: Optional[Dict[str, Any]] = None,
                 chain: Optional[Sequence[str]] = None,
                 leg_limit: Optional[float] = None) -> Dict[str, Any]:
    """一项必需要素都够不着时的结果：动线为空，但结论必须完整给出。"""
    p = persona or {}
    clock0 = _parse_clock(start_clock)
    if isolated:
        order = "无（该组团所在小路与主路网未连通，属 OSM 路网数据缺口，无法评估）"
        note = ("该组团周边存在设施，但其吸附路段在 OpenStreetMap 中未与城市道路连通，"
                "无法计算通行路径，按「不可评估」处理，不计入覆盖率。详见 docs/metrics.md。")
    else:
        order = "无（按本画像步速，15 分钟内一项出行链要素都不可达）"
        note = "该组团在所用步速下 15 分钟内没有任何出行链要素可达，建议优先补设施。"
    return {
        "point": {"id": rp.id, "name": rp.name, "lon": rp.lon, "lat": rp.lat,
                  "households": rp.households,
                  "population": int(rp.households * elements_cfg.get("avg_household_size", 2.6))},
        "persona": _persona_echo(p),
        "stops": [], "missing": missing, "home_leg": None,
        "network_status": "isolated" if isolated else "ok",
        "conflicts": [],
        "recommendations": ([{"level": "高", "title": "本组团无任何必需服务可达",
                              "detail": "整条出行链都断了，不是个别设施的问题。"}]
                            if not isolated else
                            [{"level": "高", "title": "路网未连通，无法评估",
                              "detail": note}]),
        "totals": {"stops": 0, "walk_min": 0.0, "walk_m": 0, "stay_min": 0.0, "wait_min": 0.0,
                   "span_min": 0.0,
                   "start_clock": _fmt_clock(clock0), "end_clock": _fmt_clock(clock0),
                   "missing_count": len(missing), "conflict_count": 0, "over_leg_count": 0,
                   "complete": False},
        "alternatives": {"day_order_min": None, "nearest_order_min": None},
        "order_used": order,
        "assumptions": {"start_clock": _fmt_clock(clock0), "walk_speed_mps": round(speed, 2),
                        "stay_min": {}, "cutoff_min": cutoff / 60.0,
                        "max_leg_min": leg_limit, "persona": p.get("key"),
                        "window_source": p.get("window_source") or "",
                        "chain": list(chain or []), "note": note},
    }


def _build_tour(graph: RoadGraph, home_node: str, candidates: Dict[str, List[Dict[str, Any]]],
                speed: float, scale: float = 1.0, mode: str = "day",
                order_keys: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """在候选集里排出到访顺序。

    - mode="day"：**按出行链顺序**（画像给的是"买菜→活动→取药→午饭"，无画像时用
      通用生活动线序），但每一步在"当前所在位置"附近挑该要素最近的一处 ——
      顺序服从生活逻辑，具体去哪一家服从路网（否则动线会在城里来回折返）。
    - mode="nearest"：每一步在所有还没去过的要素里挑"当前最近的一处"，
      纯就近串点，用来回答"如果只图省腿脚，能省多少分钟"。
    """
    remaining = {k: list(v) for k, v in candidates.items()}
    base = list(order_keys if (mode == "day" and order_keys) else DAY_ORDER)
    order = [k for k in base if k in remaining] + \
            [k for k in remaining if k not in base]
    tour: List[Dict[str, Any]] = []
    cur = home_node
    dist: Dict[str, float] = {}
    while remaining:
        if not dist:
            dist = graph.dijkstra(cur, max(3600.0, 3600.0 * scale))
        keys = [k for k in order if k in remaining] if mode == "day" else list(remaining)
        best_key, best_item, best_t = None, None, float("inf")
        for key in keys:
            for item in remaining[key]:
                t = dist.get(item["node"])
                if t is None:
                    continue
                if t < best_t:
                    best_key, best_item, best_t = key, item, t
            if mode == "day" and best_item is not None:
                break                      # 出行链序：本要素挑到最近的一处就不再往后找
        if best_item is None:              # 剩下的都不连通，按原顺序附在后面
            for key in list(remaining):
                tour.extend(remaining.pop(key))
            break
        tour.append(best_item)
        del remaining[best_key]
        cur = best_item["node"]
        dist = {}
    return tour


def _tour_seconds(graph: RoadGraph, home_node: str, home_snap: float,
                  tour: List[Dict[str, Any]], speed: float, scale: float = 1.0) -> float:
    """一趟走完的总步行秒数（口径与主循环完全一致）：出门 + 各段路网 + 每处进出店 + 回家。

    注意每处设施要算**两次**离路距离（进去一次、出来一次），
    否则"进去就不出来了"，总时长会少算几分钟。返回值是**图上秒**（未乘 scale）。
    """
    v = max(speed, 0.1)
    total = home_snap / v
    cur = home_node
    for item in tour:
        path = graph.shortest_path(cur, item["node"])
        cur = item["node"]
        if path is None:
            continue
        total += path["time_s"] + 2 * item["off_road_m"] / v
    back = graph.shortest_path(cur, home_node)
    if back is not None:
        total += back["time_s"] + home_snap / v
    return total
