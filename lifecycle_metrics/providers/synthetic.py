"""离线确定性合成数据源 —— 让整条链路在没有网络、没有 AK 的情况下也能真跑。

生成的结构刻意保留真实社区的"病灶"：
- 主干路 + 支路 + 内部通道（弄堂/小区步道）三层路网，节点带确定性抖动，不是 CAD 网格；
- 一条斜穿的生活性河道，只留 3 座桥 → 会产生真实的绕行；
- 设施高度集中在老城核心，跨河的新建片区设施稀少 → 体检能跑出真实短板。

⚠️ 数据是虚构的，仅用于演示与回归测试。换 provider（osm / baidu）即可替换为真实数据，
引擎、指标、报告完全不改。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple

from ..geo import offset_m
from ..schema import Dataset, Edge, Facility, Node, ResidentialPoint

SPEED = {
    "primary": 1.15,      # 主干路：过街等待多，步行等效速度低
    "residential": 1.28,  # 支路
    "footway": 1.32,      # 小区内部步道
    "bridge": 1.20,       # 桥面（含上下桥坡道）
    "service": 1.10,      # 服务性道路
}

# 路网布局（局部平面米，原点为社区中心）
XS = [-800, -600, -450, -300, -150, 0, 150, 300, 450, 600, 800]
YS = [-800, -600, -450, -300, -150, 0, 150, 300, 450, 600, 800]
ARTERIAL_X = {-600, 0, 600}
ARTERIAL_Y = {-600, 0, 600}

# 河道：x + y = RIVER_C，东北侧(x+y>RIVER_C)为跨河新建片区
RIVER_C = 675.0
BRIDGE_FRACTIONS = (0.22, 0.55, 0.86)


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _river_side(p: Tuple[float, float]) -> float:
    return p[0] + p[1] - RIVER_C


def build_synthetic_dataset(community: Dict, seed: int = 20260201) -> Dataset:
    rng = random.Random(seed)
    lon0 = community["center"]["lon"]
    lat0 = community["center"]["lat"]

    nodes_lm: Dict[str, Tuple[float, float]] = {}
    node_kind: Dict[str, str] = {}
    edges: List[Tuple[str, str, str]] = []

    def add_node(nid: str, xy: Tuple[float, float], kind: str) -> str:
        nodes_lm[nid] = xy
        node_kind[nid] = kind
        return nid

    # 1) 路口节点（带抖动）
    grid: Dict[Tuple[int, int], str] = {}
    for i, x in enumerate(XS):
        for j, y in enumerate(YS):
            jx = x + rng.uniform(-14, 14)
            jy = y + rng.uniform(-14, 14)
            kind = "primary" if (x in ARTERIAL_X or y in ARTERIAL_Y) else "residential"
            grid[(i, j)] = add_node(f"I{i}_{j}", (jx, jy), kind)

    # 2) 路段
    for i in range(len(XS)):
        for j in range(len(YS)):
            if i + 1 < len(XS):
                u, v = grid[(i, j)], grid[(i + 1, j)]
                kind = ("primary"
                        if (YS[j] in ARTERIAL_Y
                            or (XS[i] in ARTERIAL_X and XS[i + 1] in ARTERIAL_X))
                        else "residential")
                edges.append((u, v, kind))
            if j + 1 < len(YS):
                u, v = grid[(i, j)], grid[(i, j + 1)]
                kind = ("primary"
                        if (XS[i] in ARTERIAL_X
                            or (YS[j] in ARTERIAL_Y and YS[j + 1] in ARTERIAL_Y))
                        else "residential")
                edges.append((u, v, kind))

    # 3) 街坊内部步道（约六成街坊有内部通道，模拟封闭/半封闭小区差异）
    cells: List[Tuple[int, int]] = []
    for i in range(len(XS) - 1):
        for j in range(len(YS) - 1):
            cx = (XS[i] + XS[i + 1]) / 2 + rng.uniform(-25, 25)
            cy = (YS[j] + YS[j + 1]) / 2 + rng.uniform(-25, 25)
            cells.append((i, j))
            if rng.random() < 0.62:
                nid = add_node(f"C{i}_{j}", (cx, cy), "footway")
                corners = [grid[(i, j)], grid[(i + 1, j)], grid[(i, j + 1)], grid[(i + 1, j + 1)]]
                rng.shuffle(corners)
                for c in corners[: rng.randint(2, 4)]:
                    edges.append((nid, c, "footway"))

    # 4) 河道：跨河边只保留 3 座桥
    crossings: List[Tuple[str, str, str, float]] = []
    kept: List[Tuple[str, str, str]] = []
    for u, v, kind in edges:
        su, sv = _river_side(nodes_lm[u]), _river_side(nodes_lm[v])
        if su * sv < 0:
            mid = (nodes_lm[u][0] + nodes_lm[v][0] + nodes_lm[u][1] + nodes_lm[v][1]) / 2
            crossings.append((u, v, kind, mid))
        else:
            kept.append((u, v, kind))
    crossings.sort(key=lambda t: t[3])
    bridge_idx = {int(round(f * (len(crossings) - 1))) for f in BRIDGE_FRACTIONS} if crossings else set()
    for k, (u, v, kind, _) in enumerate(crossings):
        kept.append((u, v, "bridge" if k in bridge_idx else "REMOVED"))

    # 5) 少量随机断头路（真实路网并非处处连通）
    final: List[Tuple[str, str, str]] = []
    for u, v, kind in kept:
        if kind == "REMOVED":
            continue
        if kind == "residential" and rng.random() < 0.025:
            continue
        final.append((u, v, kind))

    # 6) 转经纬度
    to_ll = lambda p: offset_m(lon0, lat0, p[0], p[1])
    nodes: List[Node] = []
    for nid, (x, y) in nodes_lm.items():
        lon, lat = to_ll((x, y))
        nodes.append(Node(id=nid, lon=round(lon, 7), lat=round(lat, 7)))

    edge_objs: List[Edge] = []
    for u, v, kind in final:
        length = _dist(nodes_lm[u], nodes_lm[v])
        edge_objs.append(Edge(u=u, v=v, length_m=round(length, 2), kind=kind,
                              speed_mps=SPEED.get(kind, 1.25)))

    # 7) 居住组团：跨河新片区户数更高（新建高层），老城核心户数偏低
    residential: List[ResidentialPoint] = []
    resid_cells = rng.sample(cells, k=30)
    for n, (i, j) in enumerate(sorted(resid_cells)):
        cx = (XS[i] + XS[i + 1]) / 2 + rng.uniform(-30, 30)
        cy = (YS[j] + YS[j + 1]) / 2 + rng.uniform(-30, 30)
        across = _river_side((cx, cy)) > 0
        households = rng.randint(1200, 3000) if across else rng.randint(300, 1100)
        lon, lat = to_ll((cx, cy))
        residential.append(ResidentialPoint(
            id=f"R{n:02d}",
            name=("跨河新苑" if across else "曹杨") + f"{n + 1:02d}号组团",
            lon=round(lon, 7), lat=round(lat, 7),
            households=households,
            built_year=rng.randint(2016, 2023) if across else rng.randint(1953, 1998),
            tags={"across_river": across},
        ))

    # 8) 设施：老城核心密集，跨河片区只有零星便利类设施
    spec: List[Tuple[str, str, float, float]] = [
        # element, name, x, y
        ("market", "曹杨菜市场", -450, -300),
        ("market", "梅岭北路菜场", -150, 150),
        ("market", "枫桥生鲜菜场", 150, -450),
        ("community_health", "曹杨社区卫生服务站", -300, 0),
        ("community_health", "兰溪路社区卫生服务站", 0, -300),
        ("canteen", "梧桐社区食堂", -150, -150),
        ("canteen", "杏山路老年助餐点", -450, 150),
        ("pharmacy", "华氏大药房(曹杨店)", -600, 150),
        ("pharmacy", "国大药房(枣阳路店)", -150, 450),
        ("pharmacy", "益丰大药房(枫桥店)", 150, -150),
        ("pharmacy", "雷允上药房(梅川店)", 300, -600),
        ("kindergarten", "曹杨新村幼儿园", -300, 300),
        ("kindergarten", "兰溪路幼儿园", 300, -150),
        ("kindergarten", "杏山幼儿园", -600, -450),
        ("primary_school", "曹杨实验小学", -450, 450),
        ("primary_school", "沙田学校(小学部)", 150, 300),
        ("culture_center", "曹杨社区文化活动中心", -300, -450),
        ("culture_center", "桂巷文化活动站", 300, 150),
        ("fitness", "枣阳园健身点", -600, 600),
        ("fitness", "兰溪青年公园球场", -150, -600),
        ("fitness", "枫桥步道", 450, -450),
        ("fitness", "百禧公园健身点", 150, 150),
        ("fitness", "桐柏路健身角", -300, 300),
        ("park", "曹杨公园", -450, 0),
        ("park", "兰溪青年公园", 150, -300),
        ("park", "跨河滨水绿地", 450, 450),
        ("bus_stop", "曹杨路公交站", -600, -150),
        ("bus_stop", "梅岭北路公交站", -150, 0),
        ("bus_stop", "枫桥路公交站", 150, -600),
        ("bus_stop", "兰溪路公交站", -300, 150),
        ("bus_stop", "杏山路公交站", -450, -450),
        ("bus_stop", "新苑东路公交站", 450, 300),
        ("bus_stop", "新苑南路公交站", 300, 450),
        ("bus_stop", "桐柏路公交站", 600, 150),
        ("metro", "地铁曹杨路站", -450, -150),
        ("convenience", "全家(曹杨店)", -150, -450),
        ("convenience", "罗森(枫桥店)", 300, -300),
        ("convenience", "便利蜂(新苑店)", 450, 450),
        ("convenience", "好德(新苑南店)", 600, 300),
    ]
    facilities: List[Facility] = []
    for n, (element, name, x, y) in enumerate(spec):
        ox, oy = x + rng.uniform(-18, 18), y + rng.uniform(-18, 18)
        lon, lat = to_ll((ox, oy))
        facilities.append(Facility(
            id=f"F{n:02d}", name=name, element=element,
            lon=round(lon, 7), lat=round(lat, 7),
            capacity=1.0, source="synthetic",
            tags={"dataset": "synthetic-demo"},
        ))

    # 9) 河道折线（供渲染使用）
    river_ll = [to_ll((-125.0, 800.0)), to_ll((800.0, -125.0))]

    meta = {
        "source": "synthetic",
        "generator": "lifecycle_metrics.providers.synthetic v1",
        "seed": seed,
        "note": community.get("data_disclaimer", ""),
        "river": [[round(p[0], 7), round(p[1], 7)] for p in river_ll],
        "node_count": len(nodes),
        "edge_count": len(edge_objs),
        "removed_crossings": sum(1 for _, _, k in kept if k == "REMOVED"),
        "bridges": sum(1 for _, _, k in final if k == "bridge"),
    }
    return Dataset(community=community, nodes=nodes, edges=edge_objs,
                   residential=residential, facilities=facilities, meta=meta)
