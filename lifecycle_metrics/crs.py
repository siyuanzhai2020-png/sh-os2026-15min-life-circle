"""中国坐标系统转换：WGS-84 ↔ GCJ-02 ↔ BD-09。

**为什么必须有这个模块**：OSM 是 WGS-84，百度地图（含 POI 接口与底图）是 BD-09，
在上海两者相差约 1.1 km。混用会让 POI 与路网错位，可达性结论直接失效。

实测校验（见 tests/test_engine.py::TestCRS）：
曹杨公园 OSM(WGS-84) = (121.404177, 31.241711)
曹杨公园 百度(BD-09) = (121.415344, 31.245903)
本模块换算结果与百度值误差 < 2 m。
"""
from __future__ import annotations

import math
from typing import Tuple

PI = math.pi
X_PI = PI * 3000.0 / 180.0
A = 6378245.0                 # 克拉索夫斯基椭球长半轴
EE = 0.00669342162296594323   # 第一偏心率平方

LonLat = Tuple[float, float]


def out_of_china(lon: float, lat: float) -> bool:
    """境外坐标不做偏移（GCJ-02 只在中国大陆范围内生效）。"""
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * PI) + 320.0 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> LonLat:
    if out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlon = (dlon * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> LonLat:
    """迭代逼近（GCJ-02 → WGS-84 无解析解）。"""
    if out_of_china(lon, lat):
        return lon, lat
    wlon, wlat = lon, lat
    for _ in range(6):
        glon, glat = wgs84_to_gcj02(wlon, wlat)
        wlon += lon - glon
        wlat += lat - glat
    return wlon, wlat


def gcj02_to_bd09(lon: float, lat: float) -> LonLat:
    if out_of_china(lon, lat):
        return lon, lat
    z = math.sqrt(lon * lon + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    theta = math.atan2(lat, lon) + 0.000003 * math.cos(lon * X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lon: float, lat: float) -> LonLat:
    if out_of_china(lon, lat):
        return lon, lat
    x, y = lon - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lon: float, lat: float) -> LonLat:
    return gcj02_to_bd09(*wgs84_to_gcj02(lon, lat))


def bd09_to_wgs84(lon: float, lat: float) -> LonLat:
    return gcj02_to_wgs84(*bd09_to_gcj02(lon, lat))


def convert(lon: float, lat: float, src: str, dst: str) -> LonLat:
    """通用入口：src/dst ∈ {wgs84, gcj02, bd09}。"""
    if src == dst:
        return lon, lat
    if src == "wgs84" and dst == "gcj02":
        return wgs84_to_gcj02(lon, lat)
    if src == "gcj02" and dst == "wgs84":
        return gcj02_to_wgs84(lon, lat)
    if src == "gcj02" and dst == "bd09":
        return gcj02_to_bd09(lon, lat)
    if src == "bd09" and dst == "gcj02":
        return bd09_to_gcj02(lon, lat)
    if src == "wgs84" and dst == "bd09":
        return wgs84_to_bd09(lon, lat)
    if src == "bd09" and dst == "wgs84":
        return bd09_to_wgs84(lon, lat)
    raise ValueError(f"不支持的转换：{src} → {dst}")
