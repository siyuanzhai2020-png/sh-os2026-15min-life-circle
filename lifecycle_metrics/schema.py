"""标准化数据模型 —— 所有数据源（合成 / OSM / 百度地图）都必须产出这套结构。

见 docs/data-schema.md。字段一旦冻结，换数据源不需要改引擎。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Node:
    id: str
    lon: float
    lat: float


@dataclass
class Edge:
    u: str
    v: str
    length_m: float
    kind: str = "residential"      # footway / residential / service / steps / bridge / crossing
    speed_mps: float = 1.25        # 步行速度，按道路类型取值
    oneway: bool = False

    @property
    def travel_time_s(self) -> float:
        return self.length_m / max(self.speed_mps, 0.1)


@dataclass
class ResidentialPoint:
    """居住组团（小区/街坊），households 用于人口加权。"""
    id: str
    name: str
    lon: float
    lat: float
    households: int
    built_year: Optional[int] = None
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Facility:
    """设施点。element 必须能在 config/elements.json 的要素清单里找到。"""
    id: str
    name: str
    element: str
    lon: float
    lat: float
    capacity: float = 1.0          # 相对服务能力（1.0 = 标准配置一处）
    source: str = "synthetic"      # synthetic / osm / baidu
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Dataset:
    community: Dict[str, Any]
    nodes: List[Node]
    edges: List[Edge]
    residential: List[ResidentialPoint]
    facilities: List[Facility]
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------- 便捷访问 ----------
    def facilities_by_element(self) -> Dict[str, List[Facility]]:
        out: Dict[str, List[Facility]] = {}
        for f in self.facilities:
            out.setdefault(f.element, []).append(f)
        return out

    def total_households(self) -> int:
        return sum(r.households for r in self.residential)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "community": self.community,
            "meta": self.meta,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "residential": [asdict(r) for r in self.residential],
            "facilities": [asdict(f) for f in self.facilities],
        }

    # ---------- 持久化 ----------
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Dataset":
        return cls(
            community=raw["community"],
            meta=raw.get("meta", {}),
            nodes=[Node(**n) for n in raw["nodes"]],
            edges=[Edge(**e) for e in raw["edges"]],
            residential=[ResidentialPoint(**r) for r in raw["residential"]],
            facilities=[Facility(**f) for f in raw["facilities"]],
        )


def load_dataset(path: str | Path) -> Dataset:
    return Dataset.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save_dataset(ds: Dataset, path: str | Path) -> Path:
    return ds.save(path)
