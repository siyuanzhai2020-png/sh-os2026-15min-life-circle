"""路网连通性：把"地图上没接上的断头路"接起来，并如实标注接不上的地方。

要解决的问题（实测踩到）：
    小区内部路在 OSM 里画到离大马路 50 米的地方就停了（画图的人没 snap），
    我们的建图只按"端点坐标重合"合并，于是这段内部路成了一座孤岛。
    住在岛上的组团在 15 分钟里"一处设施都到不了" → 达标得分 0、缺口 11 项 ——
    这是数据假象，不是规划结论（那个小区离永和路只有 55 米，门口全是店）。

本模块提供两件事：
1. `stitch_components()` —— 把"断头端点相距很近、中间不隔水"的两段路接起来
   （只连端点、只连短的、跨水不连，并把每一处补连记录下来供人工复核）；
2. `analyze_network()` —— 体检这张图：几个连通块、哪些居住组团/设施掉进了
   "一处设施都没有"的小块里。掉进去的组团要如实标注为"路网未连通、无法评估"，
   而不是算成 0 分去污染覆盖率与问题清单。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .geo import GridIndex, crosses_water, haversine_m
from .graph import RoadGraph
from .schema import Dataset, Edge, Node

# 只补连"端点相距不超过这个距离"的缺口。实测各社区孤岛到主网 16.7–45.5 m，
# 60 m 一档即可覆盖；再大就有"在两条平行路之间造捷径、穿过街区"的风险。
STITCH_MAX_M = 60.0
# 连线贴河道中线多近就算隔水（不补连）
STITCH_WATER_CLEAR_M = 6.0
# 补连边的类型标记（便于统计与复核）
STITCH_KIND = "connector"


def undirected_components(graph: RoadGraph) -> Tuple[List[List[str]], Dict[str, int]]:
    """按"无向"连通性切分子网：行人能原路返回，所以单行不影响能否走出去。"""
    adj: Dict[str, set] = {n: set() for n in graph.coords}
    for u, nbrs in graph.adj.items():
        for v, _ in nbrs:
            adj[u].add(v)
            adj[v].add(u)
    seen: set = set()
    comps: List[List[str]] = []
    index: Dict[str, int] = {}
    for start in adj:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        ci = len(comps)
        for n in comp:
            index[n] = ci
        comps.append(comp)
    comps.sort(key=len, reverse=True)                     # 最大分量排第一
    index = {n: i for i, c in enumerate(comps) for n in c}
    return comps, index


def _candidate_pairs(graph: RoadGraph, index: Dict[str, int],
                     max_m: float) -> List[Tuple[float, str, str]]:
    """找出所有"跨连通块、相距 ≤ max_m"的节点对（按距离排序）。

    用 60 m 网格分桶，只比较邻近 9 个桶，避免 O(N²)。
    """
    cell = max_m / 111320.0
    buckets: Dict[Tuple[int, int], List[str]] = {}
    for nid, (lon, lat) in graph.coords.items():
        buckets.setdefault((int(lon // cell), int(lat // cell)), []).append(nid)
    pairs: List[Tuple[float, str, str]] = []
    for (bx, by), ids in buckets.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in buckets.get((bx + dx, by + dy), ()):
                    for nid in ids:
                        if nid >= other or index.get(nid) == index.get(other):
                            continue
                        d = haversine_m(*graph.coords[nid], *graph.coords[other])
                        if d <= max_m:
                            pairs.append((d, nid, other))
    pairs.sort()
    return pairs


def stitch_components(nodes: Sequence[Node], edges: Sequence[Edge],
                      water_polygons: Sequence[Sequence[Sequence[float]]] = (),
                      water_lines: Sequence[Sequence[Sequence[float]]] = (),
                      max_m: float = STITCH_MAX_M,
                      water_clear_m: float = STITCH_WATER_CLEAR_M) -> Tuple[List[Edge], List[Dict[str, Any]]]:
    """把断头路接起来；返回 (新边列表, 补连清单)。

    做法是按"最小生成森林"的思路：把所有**跨块**且相距 ≤ max_m 的路网节点对
    按距离从近到远排序，依次判断能不能连，直到所有能连的块都接上为止。于是
    补出来的每一处都是"最近的那个缺口"，不会多连、也不会绕远。

    规则（都在实测数据上定过）：
    - 只连**不同连通块**的节点（同块内本来就走得通）；
    - 长度 ≤ max_m（实测各社区孤岛到主网 16.7–45.5 m，60 m 一档即可覆盖）；
    - **跨水不连**：连线落在水面多边形内或贴河道中线 ≤ water_clear_m 就跳过
      （实测程家桥有一例正是隔水，硬连等于凭空造一座桥）；
    - 每处补连都记进清单（两端坐标/长度/连通块编号），报告里可逐条复核。
    """
    graph = RoadGraph(list(nodes), list(edges))
    comps, index = undirected_components(graph)
    if len(comps) <= 1:
        return list(edges), []

    pairs = _candidate_pairs(graph, index, max_m)
    parent = list(range(len(comps)))                      # 并查集：两个块之间连一次就够
    added: List[Edge] = []
    stitches: List[Dict[str, Any]] = []
    skipped_water = 0

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d, a, b in pairs:
        ca, cb = find(index[a]), find(index[b])
        if ca == cb:
            continue
        if crosses_water(graph.coords[a], graph.coords[b], water_polygons, water_lines,
                         line_clear_m=water_clear_m):
            skipped_water += 1
            continue
        parent[cb] = ca
        added.append(Edge(u=a, v=b, length_m=round(d, 2), kind=STITCH_KIND, speed_mps=1.2))
        stitches.append({"from": a, "to": b, "length_m": round(d, 1),
                         "from_lonlat": [round(c, 7) for c in graph.coords[a]],
                         "to_lonlat": [round(c, 7) for c in graph.coords[b]],
                         "components": [index[a], index[b]]})
    if stitches:
        stitches.append({"note": f"另有 {skipped_water} 处候选因跨水未连"
                                 f"（河道/水面阻隔，无法认定为步行通路）"})
    return list(edges) + added, stitches


def analyze_network(ds: Dataset, graph: RoadGraph) -> Dict[str, Any]:
    """路网连通性体检 + 逐组团/逐设施的"能不能评估"标记。

    `isolated` 的判定：组团吸附到的节点所在连通块里**一处设施都没有** ——
    这时"15 分钟可达 0 项"是路网数据缺口造成的，不能当成设施缺口。
    """
    comps, index = undirected_components(graph)
    fac_in: Dict[int, int] = {}
    for f in ds.facilities:
        nid, _ = graph.nearest_node(f.lon, f.lat)
        if nid is not None:
            fac_in[index[nid]] = fac_in.get(index[nid], 0) + 1

    def status_of(lon: float, lat: float) -> Tuple[str, int | None, int]:
        nid, _ = graph.nearest_node(lon, lat)
        if nid is None:
            return "no_node", None, 0
        ci = index.get(nid)
        if ci is None:
            return "no_node", None, 0
        return ("ok" if fac_in.get(ci, 0) > 0 else "isolated"), ci, len(comps[ci])

    points: Dict[str, Dict[str, Any]] = {}
    for rp in ds.residential:
        st, ci, size = status_of(rp.lon, rp.lat)
        points[rp.id] = {"status": st, "component": ci, "component_nodes": size,
                         "name": rp.name, "households": rp.households}
    facs = []
    for f in ds.facilities:
        st, ci, size = status_of(f.lon, f.lat)
        if st == "isolated":
            facs.append({"id": f.id, "name": f.name, "element": f.element,
                         "component": ci, "component_nodes": size})

    iso = {k: v for k, v in points.items() if v["status"] != "ok"}
    return {
        "components": len(comps),
        "component_sizes": [len(c) for c in comps[:8]],
        "largest_share": round(len(comps[0]) / max(1, len(graph.coords)), 4) if comps else None,
        "nodes": len(graph.coords),
        "isolated_points": iso,
        "isolated_households": sum(v["households"] for v in iso.values()),
        "isolated_facilities": facs,
        "points": points,
        "stitched_edges": len(ds.meta.get("stitched_edges") or []),
    }


def summary_line(net: Dict[str, Any], hh_size: float = 2.6) -> str:
    """给人看的一句话结论（报告与面板共用）。"""
    iso = net.get("isolated_points") or {}
    base = (f"路网 {net.get('nodes', 0)} 节点 / {net.get('components', 1)} 个连通块"
            f"（最大块占 {net.get('largest_share', 1) * 100:.1f}%）")
    if net.get("stitched_edges"):
        base += f"，已补连断头路 {net['stitched_edges']} 处"
    if not iso:
        return base + "；所有居住组团都能接入主路网。"
    pop = int(net.get("isolated_households", 0) * hh_size)
    return (base + f"；其中 {len(iso)} 个组团（{net.get('isolated_households', 0):,} 户 / "
                   f"{pop:,} 人）所在小路与主路网未连通、周边没有可达设施，"
                   f"已按「无法评估」处理，不计入覆盖率。")
