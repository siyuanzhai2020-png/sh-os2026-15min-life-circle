"""OSM 数据源：Overpass 拉取真实步行路网 + POI，产出标准 Dataset。

- 体量控制：只取社区范围 bbox 内的道路与设施，原始响应落盘缓存（离线可复跑）。
- 分类映射集中在 map_osm_tags()，可单独测试，不依赖网络。
- 户数估算：优先 building:levels × 占地面积 / 35 m²，否则按建筑面积经验值；
  估算方法写进 tags，报告里会声明这是估算值。
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..geo import haversine_m, local_xy, m_per_deg_lat, m_per_deg_lon
from ..schema import Dataset, Edge, Facility, Node, ResidentialPoint

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",     # 实测最稳（2026-09 探测 3.7s）
    "https://overpass.osm.ch/api/interpreter",     # 备用镜像
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# 步行等效速度（m/s）：与 providers/synthetic.py 保持一致的口径
HIGHWAY_SPEED = {
    "footway": 1.32, "path": 1.28, "pedestrian": 1.30, "steps": 0.70,
    "living_street": 1.25, "residential": 1.28, "service": 1.15,
    "unclassified": 1.20, "tertiary": 1.18, "tertiary_link": 1.18,
    "secondary": 1.12, "secondary_link": 1.12, "primary": 1.05, "primary_link": 1.05,
    "cycleway": 1.30, "track": 1.10, "road": 1.10,
}
WALKABLE = set(HIGHWAY_SPEED)

# OSM 标签 → 要素 key（口径见 config/elements.json）
AMENITY_MAP = {
    "marketplace": "market", "pharmacy": "pharmacy", "kindergarten": "kindergarten",
    "school": "primary_school", "community_centre": "culture_center",
    "clinic": "community_health", "doctors": "community_health",
    "social_facility": "canteen", "bus_station": "bus_stop",
}
SHOP_MAP = {"greengrocer": "market", "convenience": "convenience",
            "supermarket": "market", "bakery": "market"}
LEISURE_MAP = {"park": "park", "garden": "park", "fitness_centre": "fitness",
               "pitch": "fitness", "track": "fitness", "sports_centre": "fitness"}
RAILWAY_MAP = {"station": "metro", "subway_entrance": "metro", "halt": "metro"}


def map_osm_tags(tags: Dict[str, str]) -> Optional[str]:
    """把 OSM 标签映射到要素 key；无法归类返回 None。"""
    if not tags:
        return None
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        return "bus_stop"
    if tags.get("amenity") in AMENITY_MAP:
        key = AMENITY_MAP[tags["amenity"]]
        if key == "primary_school":
            # 只把小学算进来（中学/大学不属 15 分钟生活圈必需要素）
            levels = (tags.get("isced:level") or "") + (tags.get("school:type") or "")
            if levels and "1" not in levels and "primary" not in levels.lower():
                return None
        return key
    if tags.get("shop") in SHOP_MAP:
        if tags.get("shop") == "supermarket" and tags.get("shop_area", "") in ("", "small"):
            return "convenience"
        return SHOP_MAP[tags["shop"]]
    if tags.get("leisure") in LEISURE_MAP:
        return LEISURE_MAP[tags["leisure"]]
    if tags.get("railway") in RAILWAY_MAP:
        return RAILWAY_MAP[tags["railway"]]
    if tags.get("landuse") == "retail" and "菜" in tags.get("name", ""):
        return "market"
    return None


def _overpass(query: str, cache_path: Optional[Path] = None, timeout: int = 180,
              attempts: int = 2) -> Dict[str, Any]:
    if cache_path and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    import requests  # 延迟导入：无网络环境下也能 import 本模块做单元测试
    import os
    urls = [u for u in os.environ.get("OVERPASS_URL", "").split(",") if u.strip()] or OVERPASS_URLS
    last_err: Optional[Exception] = None
    for attempt in range(attempts):
        for url in urls:
            for req_timeout in (timeout, min(timeout, 60)):
                try:
                    resp = requests.post(url, data={"data": query}, timeout=(15, req_timeout),
                                         headers={"User-Agent": "lifecycle-metrics/0.1 "
                                                                "(competition demo; contact via repo)"})
                    resp.raise_for_status()
                    data = resp.json()
                    # 不要把"空结果/报错"当成正常响应缓存下来：实测遇到过镜像超时后
                    # 返回 {"elements": []}，一旦落盘，之后每次重建都会得到一张空路网
                    # （表现为后续 AssertionError: self._index is not None，很难查）。
                    if data.get("remark"):
                        raise RuntimeError(f"Overpass 报错：{str(data['remark'])[:200]}")
                    if not data.get("elements"):
                        raise RuntimeError("Overpass 返回空结果（可能超时或镜像异常）")
                    if cache_path:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return data
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    time.sleep(1.0)
    raise RuntimeError(f"Overpass 全部镜像不可用（试过 {urls}）：{last_err}\n"
                       f"可用 OVERPASS_URL 环境变量指定镜像，例如 "
                       f"OVERPASS_URL=https://overpass.kumi.systems/api/interpreter")


def _bbox(community: Dict[str, Any], margin_m: float = 0.0
          ) -> Tuple[float, float, float, float]:
    """分析范围（或外扩后的抓取范围）的 (south, west, north, east)。

    margin_m > 0 时把范围往外扩这么多米 —— **抓取范围**用，**分析范围不用**。
    """
    c = community["center"]
    hw = (community["extent_m"]["half_width"] + margin_m) / m_per_deg_lon(c["lat"])
    hh = (community["extent_m"]["half_height"] + margin_m) / m_per_deg_lat()
    return c["lat"] - hh, c["lon"] - hw, c["lat"] + hh, c["lon"] + hw


def _centroid(coords: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not coords:
        return 0.0, 0.0
    return sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)


# ---- 户数估算口径（务必在报告里声明为估算） ----
# 户数 ≈ 建筑占地面积 × 层数 ÷ 91 m²（= 人均居住建筑面积 35 m² ÷ 2.6 人/户）
HOUSEHOLD_DIVISOR_M2 = 91.0
MIN_FOOTPRINT_M2 = 150.0            # 小于此占地面积的建筑不参与（车棚、门房等）
RESIDENTIAL_BUILDINGS = {"residential", "apartments", "house", "terrace", "dormitory",
                         "bungalow", "semidetached_house", "detached"}
SKIP_BUILDINGS = {"garage", "garages", "shed", "roof", "carport", "construction",
                  "warehouse", "industrial", "commercial", "retail", "office", "school",
                  "hospital", "church", "hotel", "kindergarten", "yes_part"}
CLUSTER_CELL_M = 120.0              # 居住组团聚合尺度

# 抓取范围外扩（**分析范围不变**）。
# 原因：15 分钟步行约 1125 m，比分析方框的半边长（800–900 m）还长，路网若只抓框内的，
# 贴边组团的 15 分钟圈会被"盒子边缘"硬切一刀（走得出去、但框外没有路可走）。
# 外扩 1200 m ≈ 一次 15 分钟步行，是让边界结论变准的最小外扩量。
OSM_FETCH_MARGIN_M = 1200.0


def _default_levels(building: str) -> float:
    if building in ("house", "terrace", "bungalow", "detached", "semidetached_house"):
        return 2.0                  # 旧式里弄/独栋多为 2 层，缺省给 6 层会严重高估人口
    return 5.0


def _polygon_area_m2(coords: List[Tuple[float, float]], lat0: float) -> float:
    """鞋带公式（局部平面近似），用于户数估算。"""
    if len(coords) < 3:
        return 0.0
    mlon, mlat = m_per_deg_lon(lat0), m_per_deg_lat()
    xy = [((lon - coords[0][0]) * mlon, (lat - coords[0][1]) * mlat) for lon, lat in coords]
    s = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _point_in_ring(lon: float, lat: float, ring: List[Tuple[float, float]]) -> bool:
    """射线法判断点是否在环内（不引入 shapely，保持 provider 轻依赖）。"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1
            if lon < xin:
                inside = not inside
    return inside


def cluster_residential(points: List[ResidentialPoint],
                        cell_m: float = CLUSTER_CELL_M) -> List[ResidentialPoint]:
    """把单栋建筑聚合成居住组团：户数求和，取第一个有正式名称的建筑名。"""
    if not points:
        return []
    mlon, mlat = m_per_deg_lon(points[0].lat), m_per_deg_lat()
    buckets: Dict[Tuple[int, int], List[ResidentialPoint]] = {}
    for p in points:
        key = (int(p.lon * mlon // cell_m), int(p.lat * mlat // cell_m))
        buckets.setdefault(key, []).append(p)
    out: List[ResidentialPoint] = []
    for i, (_, group) in enumerate(sorted(buckets.items())):
        named = [p.name for p in group if not p.name.startswith("居住建筑")]
        lon = sum(p.lon for p in group) / len(group)
        lat = sum(p.lat for p in group) / len(group)
        out.append(ResidentialPoint(
            id=f"CR{i:03d}",
            name=named[0] if named else f"居住组团 {i + 1}",
            lon=round(lon, 7), lat=round(lat, 7),
            households=sum(p.households for p in group),
            tags={"source": "osm", "buildings": len(group),
                  "estimate": f"占地×层数/{HOUSEHOLD_DIVISOR_M2:.0f}m² 后按 {cell_m:.0f}m 网格聚合"}))
    return out


def build_osm_dataset(community: Dict[str, Any], cache_dir: str | Path = "data/cache",
                      include_facilities: bool = True,
                      fetch_margin_m: float = OSM_FETCH_MARGIN_M) -> Dataset:
    # 抓取范围 = 分析范围 + 外扩；分析范围（决定栅格、地图视野、统计口径）保持原样
    south, west, north, east = _bbox(community, fetch_margin_m)
    cs, cw, cn, ce = _bbox(community)
    box = f"{south},{west},{north},{east}"
    cache = Path(cache_dir) / f"osm-{community['id']}.json"
    # 注意：必须是 out body geom（或 out geom），否则 way 没有 geometry，路网会是空的
    query = f"""
[out:json][timeout:180];
(
  way["highway"]({box});
  node["highway"="bus_stop"]({box});
  node["amenity"]({box});
  node["shop"]({box});
  node["leisure"]({box});
  node["railway"]({box});
  way["amenity"]({box});
  way["shop"]({box});
  way["leisure"]({box});
  way["building"]({box});
  way["landuse"="residential"]({box});
  way["natural"="water"]({box});
  way["natural"="wetland"]({box});
  way["waterway"]({box});
  way["landuse"~"reservoir|basin"]({box});
  way["water"]({box});
);
out body geom;
"""
    raw = _overpass(query, cache)
    elements = raw.get("elements", [])
    if not any(e.get("tags", {}).get("highway") in WALKABLE for e in elements
               if e.get("type") == "way"):
        raise RuntimeError(
            f"Overpass 返回的数据里没有可步行道路（社区 {community.get('id')}，"
            f"bbox={box}）。多为镜像超时或返回空结果，删掉 data/cache/osm-*.json 后重试，"
            f"或用 OVERPASS_URL 指定其它镜像。")

    nodes: Dict[str, Node] = {}
    edges: List[Edge] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        highway = tags.get("highway")
        if highway not in WALKABLE:
            continue
        geom = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if "lon" in p]
        if len(geom) < 2:
            continue
        kind = ("bridge" if tags.get("bridge") not in (None, "no") else
                ("footway" if highway in ("footway", "path", "pedestrian", "steps") else
                 ("primary" if highway in ("primary", "secondary", "tertiary") else "residential")))
        speed = HIGHWAY_SPEED.get(highway, 1.2)
        # 行人单行只认 oneway:foot。
        # OSM 的 oneway=yes 约束的是**机动车**（单行道两侧人行道照样双向通行），
        # 早期直接读 oneway 会造出"能走进公园、走不出来"的人工断头路：
        # 实测曹杨路网 278/2107 条边被判成单行，导致从健身点去公园/公交站的最短路直接不存在，
        # "一天动线"走到一半断掉。行人网络必须按 oneway:foot 判定。
        oneway = tags.get("oneway:foot") in ("yes", "true", "1")
        ids: List[str] = []
        for i, (lon, lat) in enumerate(geom):
            nid = f"w{el['id']}_{i}"
            nodes.setdefault(nid, Node(id=nid, lon=round(lon, 7), lat=round(lat, 7)))
            ids.append(nid)
        for a, b in zip(ids, ids[1:]):
            na, nb = nodes[a], nodes[b]
            edges.append(Edge(u=a, v=b, length_m=round(haversine_m(na.lon, na.lat, nb.lon, nb.lat), 2),
                              kind=kind, speed_mps=speed, oneway=oneway))

    residential: List[ResidentialPoint] = []
    facs: List[Facility] = []
    seen_fac: set[Tuple[str, int, int]] = set()
    idx_r = idx_f = 0

    # 预扫：①居住用地多边形（把 building=yes 的住宅也纳入户数估算）
    #       ②水体多边形/河道中线（选址时排除"建在河里"，并作为地图底衬）
    resid_areas: List[List[Tuple[float, float]]] = []
    water_polygons: List[List[Tuple[float, float]]] = []
    water_lines: List[List[Tuple[float, float]]] = []
    for el in elements:
        tags = el.get("tags", {})
        if el.get("type") != "way":
            continue
        is_water_poly = (tags.get("natural") in ("water", "wetland")
                         or tags.get("waterway") in ("riverbank", "dock")
                         or tags.get("landuse") in ("reservoir", "basin")
                         or bool(tags.get("water")))
        is_water_line = tags.get("waterway") in ("river", "stream", "canal", "ditch", "drain")
        if is_water_poly or is_water_line:
            ring = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if "lon" in p]
            if is_water_poly and len(ring) >= 4:
                water_polygons.append(ring)
            elif len(ring) >= 2:
                water_lines.append(ring)
            continue
        if tags.get("landuse") == "residential":
            ring = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if "lon" in p]
            if len(ring) >= 4:
                resid_areas.append(ring)
    from_landuse = 0

    for el in elements:
        tags = el.get("tags", {})
        if el.get("type") == "way":
            geom = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if "lon" in p]
            center = el.get("center") or {}
            if geom:
                lon, lat = _centroid(geom)
            elif center:
                lon, lat = center.get("lon"), center.get("lat")
            else:
                continue
            if lon is None or lat is None:
                continue
            btype = tags.get("building")
            in_resid_area = bool(resid_areas) and any(
                _point_in_ring(lon, lat, ring) for ring in resid_areas)
            tagged_resid = (btype in RESIDENTIAL_BUILDINGS and btype not in SKIP_BUILDINGS) or \
                (btype == "yes" and tags.get("landuse") == "residential")
            # building=yes 本身不带住宅含义，但如果落在居住用地内就按住宅计入
            in_area_resid = in_resid_area and btype is not None and btype not in SKIP_BUILDINGS
            is_resid = tagged_resid or in_area_resid
            # 居住组团只取分析范围内的：外扩区只补路网与设施，
            # 否则户数/人口口径会随抓取范围漂移（不同社区的"分析范围"就不可比了）
            in_core = cw <= lon <= ce and cs <= lat <= cn
            if is_resid and geom and in_core:
                area = _polygon_area_m2(geom, lat)
                if area < MIN_FOOTPRINT_M2:
                    continue
                if in_area_resid and not tagged_resid:
                    from_landuse += 1
                levels = float(tags.get("building:levels") or _default_levels(btype))
                households = int(max(8, min(2000, area * levels / HOUSEHOLD_DIVISOR_M2)))
                idx_r += 1
                residential.append(ResidentialPoint(
                    id=f"R{idx_r:03d}", name=tags.get("name") or f"居住建筑 {idx_r}",
                    lon=round(lon, 7), lat=round(lat, 7), households=households,
                    tags={"source": "osm", "building": btype, "area_m2": round(area, 1),
                          "levels": levels, "via_landuse": bool(in_area_resid and not tagged_resid),
                          "estimate": f"占地×层数/{HOUSEHOLD_DIVISOR_M2:.0f}m²"}))
            if include_facilities:
                key = map_osm_tags(tags)
                if key:
                    dedup = (key, round(lon * 10 ** 4), round(lat * 10 ** 4))
                    if dedup not in seen_fac:
                        seen_fac.add(dedup)
                        idx_f += 1
                        facs.append(Facility(id=f"F{idx_f:03d}", name=tags.get("name") or key,
                                             element=key, lon=round(lon, 7), lat=round(lat, 7),
                                             source="osm", tags={"osm_id": el.get("id")}))
        elif el.get("type") == "node" and include_facilities:
            key = map_osm_tags(tags)
            if not key:
                continue
            lon, lat = el.get("lon"), el.get("lat")
            dedup = (key, round(lon * 10 ** 4), round(lat * 10 ** 4))
            if dedup in seen_fac:
                continue
            seen_fac.add(dedup)
            idx_f += 1
            facs.append(Facility(id=f"F{idx_f:03d}", name=tags.get("name") or key, element=key,
                                 lon=round(lon, 7), lat=round(lat, 7), source="osm",
                                 tags={"osm_id": el.get("id")}))

    # 合并同位置的重复节点（OSM 相邻 way 共享端点但 id 不同）
    merge: Dict[Tuple[int, int], str] = {}
    remap: Dict[str, str] = {}
    for nid, nd in list(nodes.items()):
        key = (round(nd.lon * 10 ** 5), round(nd.lat * 10 ** 5))
        if key in merge:
            remap[nid] = merge[key]
        else:
            merge[key] = nid
    if remap:
        kept: List[Node] = [n for nid, n in nodes.items() if nid not in remap]
        nodes = {n.id: n for n in kept}
        edges = [Edge(u=remap.get(e.u, e.u), v=remap.get(e.v, e.v), length_m=e.length_m,
                      kind=e.kind, speed_mps=e.speed_mps, oneway=e.oneway)
                 for e in edges if remap.get(e.u, e.u) != remap.get(e.v, e.v)]

    # 补连"地图上没接上的断头路"：小区内部路常常画到离大马路几十米处就停了，
    # 只按坐标重合合并会让它变成孤岛 —— 岛上的组团会"一处设施都到不了"（假缺口）。
    # 规则见 network.py：只连端点、只连 ≤60 m、跨水不连，并逐条记录。
    from ..network import stitch_components
    edges, stitches = stitch_components(list(nodes.values()), edges,
                                       water_polygons=water_polygons, water_lines=water_lines)

    # 单栋建筑 → 居住组团（户数求和），避免把 400 栋楼当成 400 个"小区"
    raw_buildings = len(residential)
    residential = cluster_residential(residential)

    meta = {"source": "osm",
            "overpass_elements": len(elements),
            "fetch_margin_m": fetch_margin_m,
            "analysis_bbox": {"south": round(cs, 6), "west": round(cw, 6),
                              "north": round(cn, 6), "east": round(ce, 6)},
            "fetch_bbox": {"south": round(south, 6), "west": round(west, 6),
                           "north": round(north, 6), "east": round(east, 6)},
            "nodes_outside_analysis_box": sum(
                1 for n in nodes.values() if not (cw <= n.lon <= ce and cs <= n.lat <= cn)),
            "residential_buildings_raw": raw_buildings,
            "residential_from_landuse": from_landuse,
            "residential_areas": len(resid_areas),
            "residential_clusters": len(residential),
            "water_polygons": water_polygons,
            "water_lines": water_lines,
            "stitched_edges": stitches,
            "stitched_note": "OSM 里「小区内部路画到离大马路几十米处就停了」的情况很常见，"
                             "只按端点坐标重合合并会让它成为孤岛、把数据缺口误报成设施缺口；"
                             "这里对断头端点做了 ≤60 m 的就近补连，跨水不连，逐条记录备查。",
            "note": "路网与 POI 来自 OpenStreetMap（ODbL）。户数为按建筑轮廓×层数的估算值，"
                    "并按 120 m 网格聚合为居住组团；有实测人口数据时应替换。"}
    return Dataset(community=community, nodes=list(nodes.values()), edges=edges,
                   residential=residential, facilities=facs, meta=meta)
