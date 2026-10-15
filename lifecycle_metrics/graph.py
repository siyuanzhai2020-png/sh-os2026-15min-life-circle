"""步行路网图 + 带截断的 Dijkstra。

刻意用标准库 heapq 手写，而不是 networkx：路网规模不大时快得多，
也让"可达性自算"这件事在评审面前是可读、可解释的代码，而不是一行黑盒调用。
"""
from __future__ import annotations

import heapq
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .geo import GridIndex
from .schema import Dataset, Edge, Node


class RoadGraph:
    def __init__(self, nodes: Sequence[Node], edges: Sequence[Edge]):
        self.coords: Dict[str, Tuple[float, float]] = {n.id: (n.lon, n.lat) for n in nodes}
        self.adj: Dict[str, List[Tuple[str, float]]] = {n.id: [] for n in nodes}
        self.adj_len: Dict[str, List[Tuple[str, float]]] = {n.id: [] for n in nodes}
        self.edge_kinds: Dict[Tuple[str, str], str] = {}
        self.edge_len: Dict[Tuple[str, str], float] = {}
        for e in edges:
            if e.u not in self.adj or e.v not in self.adj:
                continue
            t = e.travel_time_s
            self.adj[e.u].append((e.v, t))
            self.adj_len[e.u].append((e.v, e.length_m))
            self.edge_kinds[(e.u, e.v)] = e.kind
            self.edge_len[(e.u, e.v)] = e.length_m
            if not e.oneway:
                self.adj[e.v].append((e.u, t))
                self.adj_len[e.v].append((e.u, e.length_m))
                self.edge_kinds[(e.v, e.u)] = e.kind
                self.edge_len[(e.v, e.u)] = e.length_m
        self._index = GridIndex([(n.id, n.lon, n.lat) for n in nodes]) if nodes else None
        self._snap_cache: Dict[Tuple[float, float], Tuple[str | None, float]] = {}

    # ------------------------------------------------------------------
    @classmethod
    def from_dataset(cls, ds: Dataset) -> "RoadGraph":
        return cls(ds.nodes, ds.edges)

    def __len__(self) -> int:
        return len(self.coords)

    def nearest_node(self, lon: float, lat: float) -> Tuple[str | None, float]:
        key = (round(lon, 6), round(lat, 6))
        if key not in self._snap_cache:
            assert self._index is not None
            self._snap_cache[key] = self._index.nearest(lon, lat)
        return self._snap_cache[key]

    # ------------------------------------------------------------------
    def _dijkstra(self, adj: Mapping[str, List[Tuple[str, float]]], source: str,
                  cutoff: float) -> Dict[str, float]:
        if source not in adj:
            return {}
        dist: Dict[str, float] = {source: 0.0}
        pq: List[Tuple[float, str]] = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            if d > cutoff:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd <= cutoff and nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def dijkstra(self, source: str, cutoff_s: float) -> Dict[str, float]:
        """单源最短路（时间为权重），只保留 <= cutoff_s 的节点。返回 {node_id: travel_time_s}。"""
        return self._dijkstra(self.adj, source, cutoff_s)

    def dijkstra_length(self, source: str, cutoff_m: float) -> Dict[str, float]:
        """单源最短路（距离为权重），用于与地图厂商的步行距离口径对照。"""
        return self._dijkstra(self.adj_len, source, cutoff_m)

    # ------------------------------------------------------------------
    def shortest_path(self, source: str, target: str, cutoff_s: float = 1e9
                      ) -> Optional[Dict[str, Any]]:
        """两节点间最短路（时间为权重）→ {"nodes": [...], "time_s":, "length_m":}。

        与 dijkstra() 的区别：这里要留下前驱表才能回溯出**路径本身**（用于把
        "一天动线"画成沿路网的折线）。距离用同一组边的 length_m 累加，
        所以 time_s / length_m 与 dijkstra 的口径完全一致。
        不可达（或超出 cutoff_s）返回 None。
        """
        if source not in self.adj or target not in self.adj:
            return None
        if source == target:
            return {"nodes": [source], "time_s": 0.0, "length_m": 0.0}
        dist: Dict[str, float] = {source: 0.0}
        length: Dict[str, float] = {source: 0.0}
        prev: Dict[str, str] = {}
        pq: List[Tuple[float, str]] = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            if u == target:
                break
            if d > cutoff_s:
                break
            for v, w in self.adj[u]:
                nd = d + w
                if nd <= cutoff_s and nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    length[v] = length[u] + self.edge_len.get((u, v), 0.0)
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if target not in dist:
            return None
        nodes = [target]
        while nodes[-1] != source:
            nxt = prev.get(nodes[-1])
            if nxt is None:                      # 理论上不会发生，防御性处理
                return None
            nodes.append(nxt)
        nodes.reverse()
        return {"nodes": nodes, "time_s": round(dist[target], 2),
                "length_m": round(length[target], 2)}

    def travel_time_matrix(self, sources: Iterable[str], cutoff_s: float
                           ) -> Dict[str, Dict[str, float]]:
        return {s: self.dijkstra(s, cutoff_s) for s in sources}

    def reachable_nodes_within(self, lon: float, lat: float, cutoff_s: float) -> Dict[str, float]:
        nid, _ = self.nearest_node(lon, lat)
        if nid is None:
            return {}
        return self.dijkstra(nid, cutoff_s)
