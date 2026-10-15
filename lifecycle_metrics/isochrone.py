"""单组团步行等时圈（"风圈"）。

与体检底图的关系：
- 体检底图的口径是**全社区并集**——"从最近的居住组团出发多久能走到这里"，
  回答"生活圈画到哪儿"；
- 这里换一个中心：**以某一个组团的家为圆心**，"我 5 / 10 / 15 分钟能走到哪"，
  回答"我这一圈的边界在哪、圈外被什么切掉了"。

为什么不做成台风那样的同心圆：台风圈是理想圆，这里是**真实路网上的等时圈**。
同样 15 分钟，隔一条河、一条快速路，形状完全不同——这恰恰是本项目不愿意放弃的
技术差异点。所以借的是风圈的**视觉语言**（分层色带 + 等时线 + 圈上刻度 + 可达面积比），
而不是圆本身。

同时给出三个可回答"为什么圈长这样"的量：
- 可达面积比 = 实际可达面积 / 理想圆面积（两者都在同一张栅格上取并集，可比）；
- 16 个方向的**可达半径**，找出被切断的方向（河、铁路、快速路、断头路）；
- 每一圈内**凑齐了几项服务**——同心圈上最该有的刻度就是"到这一圈为止能办成几件事"。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geo import M_PER_DEG_LAT, m_per_deg_lon
from .graph import RoadGraph
from .schema import Dataset

BAND_SECONDS: List[int] = [300, 600, 900]
BAND_LABELS: List[str] = ["5 分钟圈", "10 分钟圈", "15 分钟圈"]

# 16 方位（0° = 正北，顺时针）
COMPASS: List[str] = ["北", "北北东", "东北", "东北东", "东", "东南东", "东南", "南南东",
                      "南", "南南西", "西南", "西南西", "西", "西北西", "西北", "北北西"]

BLIND_RATIO = 0.55          # 可达半径低于"最长方向"的这个比例，算被切断的方向


def _row_encode(times: Sequence[Sequence[float]], bands: Sequence[int]) -> List[str]:
    """把每格的秒数压成一行字符：'0'..'2' = 第几圈，'3' = 15 分钟外或不可达。

    用字符而不是 JSON 数组：一张 90×90 的栅格从 ~200 KB 降到 ~8 KB，
    前端也只需要 indexOf 级别的解析。
    """
    rows: List[str] = []
    for row in times:
        buf = []
        for t in row:
            if t == float("inf"):
                buf.append("3")
                continue
            idx = "3"
            for i, ub in enumerate(bands):
                if t <= ub:
                    idx = str(i)
                    break
            buf.append(idx)
        rows.append("".join(buf))
    return rows


def focus_isochrone(ds: Dataset, graph: RoadGraph, elements_cfg: Dict[str, Any],
                    point_id: str, walk_speed: Optional[float] = None,
                    cell_m: Optional[float] = None) -> Dict[str, Any]:
    """以某个居住组团为圆心，算出它的 5/10/15 分钟步行等时圈与配套指标。"""
    rp = next((r for r in ds.residential if r.id == point_id), None)
    if rp is None:
        raise KeyError(f"没有居住组团 {point_id}")
    cfg_speed = float(elements_cfg.get("walk_speed_mps", 1.25))
    speed = max(float(walk_speed or cfg_speed), 0.25)
    scale = cfg_speed / speed
    cutoff = float(elements_cfg["cutoffs"]["circle_15min"]["time_s"])
    gcut = cutoff * speed / cfg_speed

    home_node, home_snap = graph.nearest_node(rp.lon, rp.lat)
    if home_node is None:
        raise RuntimeError("该组团附近没有路网节点")
    dist = graph.dijkstra(home_node, gcut)

    half_w = float(ds.community.get("extent_m", {}).get("half_width", 800))
    half_h = float(ds.community.get("extent_m", {}).get("half_height", 800))
    c = ds.community["center"]
    mlon, mlat = m_per_deg_lon(c["lat"]), M_PER_DEG_LAT
    if cell_m is None:
        cell_m = max(20.0, 2 * max(half_w, half_h) / 90.0)
    lon0 = c["lon"] - half_w / mlon
    lat0 = c["lat"] - half_h / mlat
    ncols = max(1, int(2 * half_w / cell_m))
    nrows = max(1, int(2 * half_h / cell_m))

    # ---- 逐格采样：格子 → 最近路网节点 → 家到该节点的时间 + 两端离路惩罚 ----
    times: List[List[float]] = []
    for r in range(nrows):
        row: List[float] = []
        for cc in range(ncols):
            lon = lon0 + (cc + 0.5) * cell_m / mlon
            lat = lat0 + (r + 0.5) * cell_m / mlat
            nid, snap_m = graph.nearest_node(lon, lat)
            t = dist.get(nid) if nid is not None else None
            row.append(float("inf") if t is None
                       else t * scale + (home_snap + snap_m) / speed)
        times.append(row)

    cell_area_km2 = (cell_m * cell_m) / 1e6
    hx = (rp.lon - lon0) * mlon        # 家在栅格坐标里的位置（米）
    hy = (rp.lat - lat0) * mlat

    # ---- 每圈：实际面积 vs 理想圆面积（同一张栅格、同样取并集，可比） ----
    n_within = [0] * len(BAND_SECONDS)
    n_ideal = [0] * len(BAND_SECONDS)
    for r in range(nrows):
        for cc in range(ncols):
            t = times[r][cc]
            for i, ub in enumerate(BAND_SECONDS):
                if t <= ub:
                    n_within[i] += 1
            dx = (cc + 0.5) * cell_m - hx
            dy = (r + 0.5) * cell_m - hy
            d = math.hypot(dx, dy)
            for i, ub in enumerate(BAND_SECONDS):
                if d <= speed * ub:
                    n_ideal[i] += 1

    # ---- 每圈内凑齐了哪几项服务 ----
    req = [e for e in elements_cfg.get("elements", []) if e.get("required")]
    fac_best: Dict[str, Dict[str, Any]] = {}
    for f in ds.facilities:
        fnid, fsnap = graph.nearest_node(f.lon, f.lat)
        if fnid is None:
            continue
        t = dist.get(fnid)
        if t is None:
            continue
        real = t * scale + (home_snap + fsnap) / speed
        cur = fac_best.get(f.element)
        if cur is None or real < cur["time_s"]:
            fac_best[f.element] = {"facility_id": f.id, "facility_name": f.name,
                                   "time_s": real, "off_road_m": round(fsnap, 1),
                                   "lon": f.lon, "lat": f.lat}
    elem_in: List[List[str]] = []
    for ub in BAND_SECONDS:
        elem_in.append([e["key"] for e in req
                        if fac_best.get(e["key"], {}).get("time_s", 1e9) <= ub])

    bands: List[Dict[str, Any]] = []
    for i, ub in enumerate(BAND_SECONDS):
        bands.append({
            "index": i,
            "max_s": ub,
            "label": BAND_LABELS[i],
            "radius_m": round(speed * ub),
            "area_km2": round(n_within[i] * cell_area_km2, 3),
            "ideal_area_km2": round(n_ideal[i] * cell_area_km2, 3),
            "fill_pct": round(n_within[i] / max(n_ideal[i], 1), 3),
            "elements_count": len(elem_in[i]),
            "elements_total": len(req),
            "elements": elem_in[i],
            "new_elements": [k for k in elem_in[i] if k not in elem_in[i - 1]] if i else elem_in[0],
        })
    missing_15 = [{"element": e["key"], "name": e["name"],
                   "nearest_min": (round(fac_best[e["key"]]["time_s"] / 60, 1)
                                   if e["key"] in fac_best else None)}
                  for e in req if e["key"] not in elem_in[-1]]

    # ---- 16 个方向的可达半径 → 找出被切断的方向 ----
    # 关键区分：走向分析范围之外而停下的，是**口径边界**，不是路网阻隔。不区分的话，
    # 贴着分析范围边缘的组团会被误报成"南侧被切断 190 m"（其实只是走到了范围边上），
    # 而这条误报会直接进入报告与选址提示。
    directions: List[Dict[str, Any]] = []
    step = max(cell_m * 0.5, 10.0)
    for k in range(16):
        ang = math.radians(k * 22.5)
        ux, uy = math.sin(ang), math.cos(ang)          # 0° = 北 = +y
        reach = 0.0
        limited_by = "network"
        d = step
        while d <= speed * cutoff * 1.4:
            # 必须用 floor：射线走到家的西/南侧时坐标是负的，int() 向零截断会把
            # -0.5 格算成第 0 格，让射线多走最多一整格才判越界。
            cc = math.floor((hx + ux * d) / cell_m)
            rr = math.floor((hy + uy * d) / cell_m)
            if not (0 <= cc < ncols and 0 <= rr < nrows):
                limited_by = "extent"
                break
            if times[rr][cc] > cutoff:
                break
            reach = d
            d += step
        directions.append({"deg": k * 22.5, "label": COMPASS[k], "reach_m": round(reach),
                           "limited_by": limited_by})

    reaches = [x["reach_m"] for x in directions]
    # 判定"被切断"只用路网限制的方向；若整圈都被分析范围限制，退回用全部方向
    net_reaches = [x["reach_m"] for x in directions if x["limited_by"] == "network"]
    max_reach = max(net_reaches or reaches or [0])
    mean_reach = sum(reaches) / len(reaches) if reaches else 0
    blind = sorted([x for x in directions
                    if x["limited_by"] == "network" and x["reach_m"] < BLIND_RATIO * max_reach],
                   key=lambda x: x["reach_m"]) if max_reach else []
    extent_limited = [x["label"] for x in directions if x["limited_by"] == "extent"]

    ideal_15 = n_ideal[-1] * cell_area_km2
    actual_15 = n_within[-1] * cell_area_km2
    return {
        "point": {"id": rp.id, "name": rp.name, "lon": rp.lon, "lat": rp.lat,
                  "households": rp.households,
                  "population": int(rp.households * elements_cfg.get("avg_household_size", 2.6))},
        "walk_speed_mps": round(speed, 2),
        "network_status": "ok",
        "bands": bands,
        "compactness": {
            "actual_km2": round(actual_15, 3),
            "ideal_km2": round(ideal_15, 3),
            "ratio": round(actual_15 / max(ideal_15, 1e-9), 3),
            "lost_km2": round(max(0.0, ideal_15 - actual_15), 3),
            "radius_15_m": round(speed * cutoff),
            "note": "理想圆为以组团质心为圆心、半径等于步速 × 15 分钟的圆；两者在同一栅格上取并集，因此可比。"
                    "可达面积比低于 100% 的部分，即受河道、快速路、封闭小区、断头路折减的空间。"
                    "注意两者都只在分析范围内统计：贴着分析范围边缘的组团，其绝对面积会被边界截断，"
                    "但比值受同一截断影响，仍可用于横向比较。",
        },
        "directions": directions,
        "blind_directions": blind[:4],
        "extent_limited_directions": extent_limited,
        "mean_reach_m": round(mean_reach),
        "max_reach_m": round(max_reach),
        "missing": missing_15,
        "grid": {
            "lon0": lon0, "lat0": lat0, "cell_m": cell_m,
            "ncols": ncols, "nrows": nrows,
            "bands_s": BAND_SECONDS,
            "rows": _row_encode(times, BAND_SECONDS),
            "note": "rows[0] 是最南一行；'0'..'2' = 第几圈，'3' = 15 分钟外或不可达。",
        },
        "note": "等时圈以居住组团质心为圆心（组团为 120 m 网格聚合的分析单元，非单栋建筑）；"
                "时间为步行最短路加两端离路惩罚，未建模过街等待与坡度。",
    }
