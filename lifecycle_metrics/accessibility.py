"""可达性计算：路口吸附、单源等时、设施可达矩阵、生活圈栅格。

三个刻意的设计决定（都要在报告里说明，评委会问）：
1. 可达性自己算，不依赖地图厂商的批量算路配额 —— 配额被卡项目也不会停摆；
2. 设施吸附到最近路网节点，并把"离路距离/步行速度"作为惩罚时间计入，避免把
   藏在小区深处的设施算成"门口就有"；
3. 生活圈范围 = 所有居住组团 15 分钟可达区域的并集，而不是社区中心点的一个圆。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

from .geo import (M_PER_DEG_LAT, crosses_water, dist_point_segment_m,  # noqa: F401
                  m_per_deg_lat, m_per_deg_lon, point_in_ring)
from .graph import RoadGraph
from .schema import Dataset, Facility, ResidentialPoint


@dataclass
class FacilityReach:
    facility_id: str
    element: str
    time_s: float
    snap_m: float


@dataclass
class PointAccess:
    point_id: str
    snap_node: str | None
    snap_m: float
    reachable: List[FacilityReach] = field(default_factory=list)

    def by_element(self) -> Dict[str, List[FacilityReach]]:
        out: Dict[str, List[FacilityReach]] = {}
        for r in self.reachable:
            out.setdefault(r.element, []).append(r)
        for v in out.values():
            v.sort(key=lambda r: r.time_s)
        return out


@dataclass
class AccessResult:
    cutoff_s: float
    points: Dict[str, PointAccess]
    by_element_min_time: Dict[str, Dict[str, float]]  # element -> point_id -> best time

    def elements_of(self, point_id: str) -> Dict[str, List[FacilityReach]]:
        return self.points[point_id].by_element()


def snap(graph: RoadGraph, lon: float, lat: float, walk_speed: float) -> Tuple[str | None, float, float]:
    """返回 (最近节点, 离路距离米, 计入的惩罚时间秒)。"""
    nid, d = graph.nearest_node(lon, lat)
    return nid, d, d / max(walk_speed, 0.1)


def compute_access(ds: Dataset, graph: RoadGraph, cutoff_s: float,
                   walk_speed: float = 1.25) -> AccessResult:
    points: Dict[str, PointAccess] = {}
    fac_nodes: Dict[str, Tuple[str | None, float]] = {}
    for f in ds.facilities:
        nid, snap_m = graph.nearest_node(f.lon, f.lat)
        fac_nodes[f.id] = (nid, snap_m / max(walk_speed, 0.1))

    by_elem_min: Dict[str, Dict[str, float]] = {}
    for rp in ds.residential:
        nid, snap_m, penalty = snap(graph, rp.lon, rp.lat, walk_speed)
        dist = graph.dijkstra(nid, max(0.0, cutoff_s - penalty)) if nid else {}
        pa = PointAccess(point_id=rp.id, snap_node=nid, snap_m=round(snap_m, 1))
        for f in ds.facilities:
            fnid, fpen = fac_nodes[f.id]
            if fnid is None:
                continue
            t = dist.get(fnid)
            if t is None:
                continue
            total = t + penalty + fpen
            if total <= cutoff_s:
                pa.reachable.append(FacilityReach(
                    f.id, f.element, round(total, 1), round(fpen * walk_speed, 1)))
                cur = by_elem_min.setdefault(f.element, {})
                if rp.id not in cur or total < cur[rp.id]:
                    cur[rp.id] = round(total, 1)
        points[rp.id] = pa
    return AccessResult(cutoff_s=cutoff_s, points=points, by_element_min_time=by_elem_min)


# ----------------------------------------------------------------------
@dataclass
class CircleGrid:
    """生活圈栅格：每个格子的最短步行时间（取所有居住组团的并集下界）。"""
    lon0: float
    lat0: float
    cell_m: float
    ncols: int
    nrows: int
    times: List[List[float]]          # 秒；inf 表示不可达
    cell_area_m2: float

    def cells(self) -> Iterable[Tuple[int, int, float, float, float]]:
        """yield (row, col, lon, lat, time_s)"""
        mlon = m_per_deg_lon(self.lat0)
        for r in range(self.nrows):
            for c in range(self.ncols):
                t = self.times[r][c]
                if t == float("inf"):
                    continue
                yield r, c, self.lon0 + (c + 0.5) * self.cell_m / mlon, \
                    self.lat0 + (r + 0.5) * self.cell_m / m_per_deg_lat(), t

    def area_within(self, cutoff_s: float) -> float:
        n = sum(1 for r in range(self.nrows) for c in range(self.ncols) if self.times[r][c] <= cutoff_s)
        return n * self.cell_area_m2


def build_circle_grid(ds: Dataset, graph: RoadGraph, cutoff_s: float = 900.0,
                      cell_m: float = 40.0, walk_speed: float = 1.25) -> CircleGrid:
    half_w = ds.community.get("extent_m", {}).get("half_width", 800)
    half_h = ds.community.get("extent_m", {}).get("half_height", 800)
    mlon, mlat = m_per_deg_lon(ds.community["center"]["lat"]), m_per_deg_lat()
    lon0 = ds.community["center"]["lon"] - half_w / mlon
    lat0 = ds.community["center"]["lat"] - half_h / mlat
    ncols = int(2 * half_w / cell_m)
    nrows = int(2 * half_h / cell_m)

    # 每个居住组团一次 Dijkstra，再把格子吸附到最近节点的最短时间取最小值
    point_dist: List[Tuple[str | None, float, Dict[str, float]]] = []
    for rp in ds.residential:
        nid, snap_m, penalty = snap(graph, rp.lon, rp.lat, walk_speed)
        if nid is None:
            continue
        point_dist.append((nid, penalty, graph.dijkstra(nid, cutoff_s)))

    times = [[float("inf")] * ncols for _ in range(nrows)]
    for r in range(nrows):
        for c in range(ncols):
            lon = lon0 + (c + 0.5) * cell_m / mlon
            lat = lat0 + (r + 0.5) * cell_m / mlat
            nid, snap_m = graph.nearest_node(lon, lat)
            if nid is None:
                continue
            pen = snap_m / max(walk_speed, 0.1)
            best = float("inf")
            for _src, src_pen, dist in point_dist:
                t = dist.get(nid)
                if t is None:
                    continue
                total = t + src_pen + pen
                if total < best:
                    best = total
            times[r][c] = best
    return CircleGrid(lon0=lon0, lat0=lat0, cell_m=cell_m, ncols=ncols, nrows=nrows,
                      times=times, cell_area_m2=cell_m * cell_m)


def circle_polygon(ds: Dataset, grid: CircleGrid, cutoff_s: float):
    """把可达栅格并成多边形（GeoJSON 用）。缺 shapely 时返回 None，不影响其它功能。"""
    try:
        from shapely.geometry import box
        from shapely.ops import unary_union
    except Exception:  # pragma: no cover
        return None
    mlon, mlat = m_per_deg_lon(grid.lat0), m_per_deg_lat()
    cells = []
    for r in range(grid.nrows):
        for c in range(grid.ncols):
            if grid.times[r][c] <= cutoff_s:
                x0 = grid.lon0 + c * grid.cell_m / mlon
                y0 = grid.lat0 + r * grid.cell_m / mlat
                cells.append(box(x0, y0, x0 + grid.cell_m / mlon, y0 + grid.cell_m / mlat))
    if not cells:
        return None
    return unary_union(cells)


def nearest_facility_time(ds: Dataset, access: AccessResult, element: str) -> Dict[str, float]:
    return dict(access.by_element_min_time.get(element, {}))


# ----------------------------------------------------------------------
# 覆盖底图的"居住用地蒙版"
#
# 为什么要有这一层：可达要素栅格是按路网铺开的，如果直接整片铺色，色块会盖到
# 河道、校园、商场、待建地上 —— 看图的人会问"这块颜色跟居民区对不上"。
# 体检的对象是居民，所以色块只画在"有人住的地方"：
#   ① 居住组团（体检的分析单元）周边 resid_buffer_m 以内；
#   ② 水体（OSM 水面/河道）不参与着色。
# 蒙版只影响显示，不参与任何指标计算（覆盖率、达标率、可达时间都不用它）。
# ----------------------------------------------------------------------
def _point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """（保留旧名，实现已移到 geo.py）"""
    return point_in_ring(lon, lat, ring)


def _dist_point_segment_m(lon: float, lat: float, a: Sequence[float], b: Sequence[float],
                          mlon: float, mlat: float) -> float:
    """（保留旧名，实现已移到 geo.py）"""
    return dist_point_segment_m(lon, lat, a, b, mlon, mlat)


def build_livable_mask(ds: Dataset, grid: "CircleGrid", resid_buffer_m: float = 150.0,
                       water_line_buffer_m: float = 12.0) -> Tuple[List[List[bool]], Dict[str, int]]:
    """返回 (mask, stats)：mask[r][c] = 该格是否属于"居民实际居住处"（可着色）。"""
    nrows, ncols, cell = grid.nrows, grid.ncols, grid.cell_m
    keep = [[False] * ncols for _ in range(nrows)]
    stats = {"resid_buffer_m": resid_buffer_m, "masked_no_resident": 0, "masked_water": 0}

    # ① 居住组团周边缓冲：按"组团→格子"的方向铺，避免 格子数×组团数 的双重循环
    mlon, mlat = m_per_deg_lon(grid.lat0), m_per_deg_lat()
    rad_cells = int(resid_buffer_m / cell) + 1
    for rp in ds.residential:
        c0 = int(((rp.lon - grid.lon0) * mlon) / cell)
        r0 = int(((rp.lat - grid.lat0) * mlat) / cell)
        for r in range(max(0, r0 - rad_cells), min(nrows, r0 + rad_cells + 1)):
            lat = grid.lat0 + (r + 0.5) * cell / mlat
            for c in range(max(0, c0 - rad_cells), min(ncols, c0 + rad_cells + 1)):
                if keep[r][c]:
                    continue
                if rp.lat == rp.lat:      # 防 NaN
                    lon = grid.lon0 + (c + 0.5) * cell / mlon
                    d = ((lon - rp.lon) * mlon) ** 2 + ((lat - rp.lat) * mlat) ** 2
                    if d <= resid_buffer_m ** 2:
                        keep[r][c] = True

    stats["resid_cells"] = sum(1 for row in keep for v in row if v)
    polys = ds.meta.get("water_polygons") or []
    lines = ds.meta.get("water_lines") or []
    poly_bbox = []
    for poly in polys:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        poly_bbox.append((min(xs), min(ys), max(xs), max(ys)))
    for r in range(nrows):
        lat = grid.lat0 + (r + 0.5) * cell / mlat
        for c in range(ncols):
            if not keep[r][c]:
                continue
            lon = grid.lon0 + (c + 0.5) * cell / mlon
            wet = False
            for poly, (x0, y0, x1, y1) in zip(polys, poly_bbox):
                if x0 <= lon <= x1 and y0 <= lat <= y1 and _point_in_ring(lon, lat, poly):
                    wet = True
                    break
            if not wet and lines:
                for line in lines:
                    if len(line) < 2:
                        continue
                    xs = [p[0] for p in line]
                    ys = [p[1] for p in line]
                    if not (min(xs) - 0.001 <= lon <= max(xs) + 0.001
                            and min(ys) - 0.001 <= lat <= max(ys) + 0.001):
                        continue
                    d = min(_dist_point_segment_m(lon, lat, line[i], line[i + 1], mlon, mlat)
                            for i in range(len(line) - 1))
                    if d <= water_line_buffer_m:
                        wet = True
                        break
            if wet:
                keep[r][c] = False
                stats["masked_water"] += 1
    stats["livable_cells"] = sum(1 for row in keep for v in row if v)
    return keep, stats


def element_access_grid(ds: Dataset, graph: RoadGraph, elements_cfg: Dict,
                        grid: CircleGrid, cutoff_s: float = 900.0,
                        walk_speed: float = 1.25, max_sources: int = 1500,
                        max_cell_snap_m: float = 220.0, resid_buffer_m: float = 150.0,
                        mask_non_residential: bool = True) -> Dict:
    """每个栅格位置"15 分钟内可达的必需要素数量" —— 体检图底图的核心图层。

    比"最短步行时间"更有信息量：时间并集几乎处处都是绿色（因为总有一个组团很近），
    而"可达要素数量"能直接暴露出服务洼地。

    近似说明：只为"至少有一个栅格吸附到它"的路网节点做精确计算（去重后最多 max_sources
    个），超限时按等间隔抽样，被抽掉的栅格复用**最近**已计算节点的结果；离路网超过
    max_cell_snap_m 的栅格不参与着色（河中、公园深处等）。
    另外，只有"居民实际居住处"（居住组团 resid_buffer_m 缓冲内、且非水体）才着色，
    避免色块铺到河道/校园/商场上，看图的人会觉得"色块跟居民区对不上"。
    """
    req_keys = {e["key"] for e in elements_cfg["elements"] if e.get("required")}
    total = len(req_keys)
    fac_nodes = []
    for f in ds.facilities:
        nid, snap_m = graph.nearest_node(f.lon, f.lat)
        if nid is not None:
            fac_nodes.append((f.element, nid, snap_m / max(walk_speed, 0.1)))

    mlon, mlat = m_per_deg_lon(grid.lat0), m_per_deg_lat()

    def cell_center(r: int, c: int) -> Tuple[float, float]:
        return (grid.lon0 + (c + 0.5) * grid.cell_m / mlon,
                grid.lat0 + (r + 0.5) * grid.cell_m / mlat)

    cell_node: Dict[Tuple[int, int], Tuple[str | None, float]] = {}
    for r in range(grid.nrows):
        for c in range(grid.ncols):
            cell_node[(r, c)] = graph.nearest_node(*cell_center(r, c))

    needed = sorted({nid for nid, _ in cell_node.values() if nid is not None})
    step = max(1, len(needed) // max_sources + (1 if len(needed) % max_sources else 0))
    computed: Dict[str, int] = {}
    for nid in needed[::step]:
        dist = graph.dijkstra(nid, cutoff_s)
        covered = set()
        for element, fnid, fpen in fac_nodes:
            if element not in req_keys:
                continue
            t = dist.get(fnid)
            if t is not None and t + fpen <= cutoff_s:
                covered.add(element)
        computed[nid] = len(covered)

    counts: List[List[int]] = [[-1] * grid.ncols for _ in range(grid.nrows)]
    # 抽样超限时，未精确计算的格子复用"地理上最近"的已计算节点结果
    # （旧实现取的是列表里第一个已计算节点，会让半个图共用同一个数 —— 已修正）
    computed_pos = [(*graph.coords[nid], computed[nid])
                    for nid in needed[::step] if nid in computed]
    for (r, c), (nid, snap_m) in cell_node.items():
        if nid is None or snap_m > max_cell_snap_m:
            continue
        if nid in computed:
            counts[r][c] = computed[nid]
        elif computed_pos:
            lon, lat = cell_center(r, c)
            counts[r][c] = min(computed_pos,
                               key=lambda p: (p[0] - lon) ** 2 + (p[1] - lat) ** 2)[2]

    mask_stats: Dict[str, int] = {}
    if mask_non_residential:
        keep, mask_stats = build_livable_mask(ds, grid, resid_buffer_m=resid_buffer_m)
        masked = 0
        for r in range(grid.nrows):
            for c in range(grid.ncols):
                if counts[r][c] >= 0 and not keep[r][c]:
                    counts[r][c] = -1
                    masked += 1
        mask_stats["masked_total"] = masked
        mask_stats["masked_no_resident"] = masked - mask_stats.get("masked_water", 0)
    return {"counts": counts, "required_total": total,
            "sources_used": len(computed), "sources_needed": len(needed),
            "mask": mask_stats}
