"""lifecycle_metrics — 可复用的社区生活圈体检指标引擎.

对外只暴露少量稳定入口：

    from lifecycle_metrics import load_dataset, RoadGraph, diagnose, optimize_sites

设计原则：
1. 引擎不依赖任何在线服务，输入是一份标准化 Dataset，输出是结构化 JSON。
2. 数据生产（OSM / 百度地图 / 合成）在 providers 层，engine 层不感知数据来源。
3. 口径集中写在 config/elements.json 与 docs/metrics.md，避免代码里散落魔数。
"""
from .schema import Dataset, Facility, Node, Edge, ResidentialPoint, save_dataset, load_dataset
from .graph import RoadGraph
from .metrics import diagnose

__all__ = [
    "Dataset",
    "Node",
    "Edge",
    "ResidentialPoint",
    "Facility",
    "load_dataset",
    "save_dataset",
    "RoadGraph",
    "diagnose",
]
__version__ = "0.1.0"
