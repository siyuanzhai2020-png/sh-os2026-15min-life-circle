"""数据源适配层：同一份 Dataset schema，三种生产者。

- synthetic：离线确定性生成，用于无网络/无 AK 时跑通全链路（默认）
- osm      ：Overpass API 拉取真实步行路网 + OSM POI（需要外网）
- baidu    ：百度地图开放平台（需要 AK，POI 覆盖显著优于 OSM）
"""
from __future__ import annotations

from typing import Callable, Dict

from .synthetic import build_synthetic_dataset


def _osm(*args, **kwargs):  # 延迟导入，避免无网络环境导入即报错
    from .osm import build_osm_dataset
    return build_osm_dataset(*args, **kwargs)


def _baidu(*args, **kwargs):
    from .baidu import build_baidu_dataset
    return build_baidu_dataset(*args, **kwargs)


PROVIDERS: Dict[str, Callable] = {
    "synthetic": build_synthetic_dataset,
    "osm": _osm,
    "baidu": _baidu,
}


def get_provider(name: str) -> Callable:
    if name not in PROVIDERS:
        raise KeyError(f"未知数据源 {name!r}，可选：{sorted(PROVIDERS)}")
    return PROVIDERS[name]


__all__ = ["PROVIDERS", "get_provider", "build_synthetic_dataset"]
