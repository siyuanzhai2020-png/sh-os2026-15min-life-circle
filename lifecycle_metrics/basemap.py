"""百度静态底图：服务端取图 + 地理参考。

两条用途：
1. `/api/basemap.png|json` 给前端做"不依赖浏览器加载百度脚本"的真底图；
2. `tools/align_check.py` 把自建路网画到真底图上，用来核验坐标对齐。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Tuple

from .crs import wgs84_to_bd09
from .env import baidu_credentials, load_env_file
from .geo import M_PER_DEG_LAT, m_per_deg_lon

STATIC_URL = "https://api.map.baidu.com/staticimage/v2"
DEFAULT_SIZE = 1024

# 百度地图的真实分辨率常数（实测标定，2026-09-30）。
# 用 JSAPI 的 getBounds 与 pointToPixel 两条独立口径在 zoom 14–18 上测得：
#     mpp(zoom=14..18) = 13.679 / 6.839 / 3.420 / 1.710 / 0.855  (lat 31.248)
# 反推 mpp = C·cos(lat)/2^zoom ⇒ C ≈ 262150（各 zoom 反推值落在 262.1k–262.2k，离散 <0.1%）。
# ⚠️ 标准 Web Mercator 的 C = 156543.03，两者差 1.675 倍；用错会让底图被压缩进
# 错误的范围，离中心越远偏得越多（表现为"路网和底图错位/平移"）。
BAIDU_MERCATOR_CONST = 262150.0


def baidu_mpp(lat: float, zoom: int) -> float:
    """百度地图 zoom 级别对应的地面分辨率（米/像素）。"""
    return BAIDU_MERCATOR_CONST * math.cos(math.radians(lat)) / (2 ** zoom)


def mercator_mpp(lat: float, zoom: int) -> float:
    """标准 Web Mercator 的分辨率，仅用于对比（不要用它做百度底图的地理参考）。"""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


def basemap_meta(community: Dict[str, Any], zoom: int = 16,
                 size: int = DEFAULT_SIZE) -> Dict[str, Any]:
    c = community["center"]
    size = max(64, min(size, DEFAULT_SIZE))
    mpp = baidu_mpp(c["lat"], zoom)
    return {
        "ok": True,
        "community": community["id"],
        "center_wgs84": [c["lon"], c["lat"]],
        "zoom": zoom,
        "size": size,
        "mpp_m_per_px": mpp,
        "mpp_source": "百度实测标定：262300·cos(lat)/2^zoom（tools/basemap_scale_check.py）",
        "span_m": {"width": size * mpp, "height": size * mpp},
        "image_url": f"/api/basemap.png?community={community['id']}&zoom={zoom}&size={size}",
        "attribution": "地图底图 © 百度地图（静态图 API，服务端 AK）",
    }


def fetch_basemap(community: Dict[str, Any], zoom: int = 16, size: int = DEFAULT_SIZE,
                  cache_dir: str | Path = "data/cache") -> Path:
    """拉取并缓存静态底图。

    **先查缓存、再要求 AK**：演示包离线分发时不含任何密钥，靠随包附带的底图 PNG 照常出图；
    只有缓存未命中且没有 AK 时才报错。
    注意：不要传 scale（width×scale > 1024 会报 "width 超出范围"）。
    """
    size = max(64, min(size, DEFAULT_SIZE))
    out = Path(cache_dir) / f"basemap-{community['id']}-z{zoom}-{size}.png"
    if out.exists():
        return out

    load_env_file()
    creds = baidu_credentials()
    ak = creds["ak"] or creds["web_ak"]
    if not ak:
        raise RuntimeError(
            f"离线底图缓存不存在（{out}）且未配置百度 AK。离线场景请使用随包的 "
            f"data/cache/basemap-*.png；需要新底图时把 .env.local.example 复制为 .env.local "
            f"并填入自己的 AK（见 docs/baidu-ak.md）。")
    c = community["center"]
    bd_lon, bd_lat = wgs84_to_bd09(c["lon"], c["lat"])
    import requests
    r = requests.get(STATIC_URL, params={"ak": ak, "center": f"{bd_lon:.6f},{bd_lat:.6f}",
                                         "width": size, "height": size, "zoom": zoom},
                     timeout=30)
    if r.content[:4] != b"\x89PNG":
        raise RuntimeError(f"百度静态图返回异常（可能未开通该服务）：{r.text[:200]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    return out


def projector(community: Dict[str, Any], size: int, zoom: int):
    """WGS-84 → 静态图像素坐标（按百度实测分辨率）。"""
    c = community["center"]
    mpp = baidu_mpp(c["lat"], zoom)
    mlon, mlat = m_per_deg_lon(c["lat"]), M_PER_DEG_LAT

    def proj(lon: float, lat: float) -> Tuple[float, float]:
        dx = (lon - c["lon"]) * mlon
        dy = (lat - c["lat"]) * mlat
        return size / 2 + dx / mpp, size / 2 - dy / mpp

    proj.mpp = mpp          # type: ignore[attr-defined]
    proj.mlon = mlon        # type: ignore[attr-defined]
    proj.mlat = mlat        # type: ignore[attr-defined]
    return proj
