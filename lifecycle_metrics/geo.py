"""几何工具：全部用纯 Python + 标准库实现，避免为了一个 haversine 引入 GIS 依赖。"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

EARTH_R = 6371008.8
M_PER_DEG_LAT = 111320.0


def m_per_deg_lon(lat_deg: float) -> float:
    return M_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def m_per_deg_lat(lat_deg: float = 0.0) -> float:
    """纬度方向每度米数（保留参数，便于日后替换为椭球模型）。"""
    return M_PER_DEG_LAT


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两点球面距离（米）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def offset_m(lon: float, lat: float, dx_m: float, dy_m: float) -> Tuple[float, float]:
    """以 (lon, lat) 为原点，向东 dx 米、向北 dy 米后的坐标。"""
    return lon + dx_m / m_per_deg_lon(lat), lat + dy_m / M_PER_DEG_LAT


def local_xy(lon0: float, lat0: float, lon: float, lat: float) -> Tuple[float, float]:
    """把经纬度投影到以 (lon0, lat0) 为原点的局部平面（米），用于绘图与几何运算。"""
    return (lon - lon0) * m_per_deg_lon(lat0), (lat - lat0) * M_PER_DEG_LAT


class GridIndex:
    """极简空间索引：按固定经纬网格分桶，查询时向外扩环。

    路网规模在几万节点以内时完全够用，省掉 scipy/rtree 依赖。
    """

    def __init__(self, points: Sequence[Tuple[str, float, float]], cell_deg: float = 0.0015):
        self.cell = cell_deg
        self.buckets: dict[Tuple[int, int], List[Tuple[str, float, float]]] = {}
        for pid, lon, lat in points:
            self.buckets.setdefault(self._key(lon, lat), []).append((pid, lon, lat))

    def _key(self, lon: float, lat: float) -> Tuple[int, int]:
        return int(math.floor(lon / self.cell)), int(math.floor(lat / self.cell))

    def nearest(self, lon: float, lat: float, max_rings: int = 60
                ) -> Tuple[str | None, float]:
        """返回 (id, 距离米)。找不到返回 (None, inf)。"""
        kx, ky = self._key(lon, lat)
        best_id, best_d = None, float("inf")
        for ring in range(max_rings):
            found_any = False
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if ring and max(abs(dx), abs(dy)) != ring:
                        continue
                    for pid, plon, plat in self.buckets.get((kx + dx, ky + dy), ()):
                        found_any = True
                        d = haversine_m(lon, lat, plon, plat)
                        if d < best_d:
                            best_id, best_d = pid, d
            # 已经找到候选点，且下一环不可能更近（环距 > 当前最优）时收敛
            if best_id is not None:
                ring_floor = (ring - 1) * self.cell * M_PER_DEG_LAT
                if ring_floor > best_d:
                    break
            elif ring > 8 and not found_any:
                break
        return best_id, best_d


def point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """射线法：判断点是否在单个多边形环内（环为 [[lon,lat], ...]）。"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xi = x1 + (lat - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
            if lon < xi:
                inside = not inside
    return inside


def dist_point_segment_m(lon: float, lat: float, a: Sequence[float], b: Sequence[float],
                         mlon: float, mlat: float) -> float:
    """点到线段的距离（米）；(mlon, mlat) 为该纬度上的每度米数。"""
    ax, ay = (a[0] - lon) * mlon, (a[1] - lat) * mlat
    bx, by = (b[0] - lon) * mlon, (b[1] - lat) * mlat
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom <= 1e-12 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
    return ((ax + t * dx) ** 2 + (ay + t * dy) ** 2) ** 0.5


def crosses_water(a: Sequence[float], b: Sequence[float],
                  water_polygons: Sequence[Sequence[Sequence[float]]],
                  water_lines: Sequence[Sequence[Sequence[float]]],
                  samples: int = 15, line_clear_m: float = 6.0) -> bool:
    """线段 a→b 是否被水体阻断（落在水面多边形内，或贴河道中线太近）。

    用途：给"断头路补连"把关 —— 两段路端点很近但中间隔着河时，绝不能连
    （连上就等于凭空造了一座桥）。
    """
    mlon, mlat = m_per_deg_lon(a[1]), M_PER_DEG_LAT
    bbox = [(min(p[0] for p in poly), min(p[1] for p in poly),
             max(p[0] for p in poly), max(p[1] for p in poly)) for poly in water_polygons]
    for i in range(samples + 1):
        t = i / samples
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        for poly, (x0, y0, x1, y1) in zip(water_polygons, bbox):
            if x0 <= x <= x1 and y0 <= y <= y1 and point_in_ring(x, y, poly):
                return True
        for line in water_lines:
            if len(line) < 2:
                continue
            xs = [p[0] for p in line]
            ys = [p[1] for p in line]
            if not (min(xs) - 0.001 <= x <= max(xs) + 0.001
                    and min(ys) - 0.001 <= y <= max(ys) + 0.001):
                continue
            if min(dist_point_segment_m(x, y, line[j], line[j + 1], mlon, mlat)
                   for j in range(len(line) - 1)) <= line_clear_m:
                return True
    return False


def weighted_gini(values: Iterable[float], weights: Iterable[float]) -> float:
    """加权 Gini 系数，用于衡量社区内部各居住组团达标情况的公平性。"""
    pairs = sorted(zip(values, weights), key=lambda p: p[0])
    if not pairs:
        return 0.0
    total_w = sum(w for _, w in pairs)
    total_vw = sum(v * w for v, w in pairs)
    if total_w <= 0 or total_vw <= 0:
        return 0.0
    cum_w = 0.0
    cum_vw = 0.0
    for v, w in pairs:
        cum_w += w
        cum_vw += v * w
        # 梯形积分，得到洛伦兹曲线下面积
    # 直接按定义计算：G = 1 - sum_i (W_i - W_{i-1}) * (V_i + V_{i-1}) / V_total
    cum_w = 0.0
    cum_vw = 0.0
    prev_v_share = 0.0
    area = 0.0
    for v, w in pairs:
        w_share = w / total_w
        cum_vw += v * w
        v_share = cum_vw / total_vw
        area += w_share * (v_share + prev_v_share) / 2.0
        prev_v_share = v_share
        cum_w += w_share
    return max(0.0, min(1.0, 1.0 - 2.0 * area))
