"""引擎单元测试：口径回归 + 算法正确性 + 报告可生成。

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics import RoadGraph, diagnose, load_dataset, save_dataset   # noqa: E402
from lifecycle_metrics.accessibility import (build_circle_grid, circle_polygon,  # noqa: E402
                                             compute_access, element_access_grid)
from lifecycle_metrics.config import load_communities, load_elements, resolve_community  # noqa: E402
from lifecycle_metrics.geo import (M_PER_DEG_LAT, haversine_m, m_per_deg_lon,  # noqa: E402
                                   offset_m, weighted_gini)
from lifecycle_metrics.metrics import strip_runtime                             # noqa: E402
from lifecycle_metrics.optimize import optimize_sites                           # noqa: E402
from lifecycle_metrics.providers import get_provider                            # noqa: E402
from lifecycle_metrics.providers.baidu import (in_analysis_box, is_auxiliary,  # noqa: E402
                                               map_baidu_tags, search_area)
from lifecycle_metrics.providers.osm import map_osm_tags                        # noqa: E402
from lifecycle_metrics.report import build_report                               # noqa: E402
from lifecycle_metrics.schema import Dataset, Edge, Facility, Node, ResidentialPoint  # noqa: E402

LON0, LAT0 = 121.0, 31.0


def _ll(dx_m: float, dy_m: float):
    return offset_m(LON0, LAT0, dx_m, dy_m)


TOY_ELEMENTS = {
    "cutoffs": {"circle_15min": {"time_s": 300, "label": "15 分钟生活圈"},
                "circle_5min": {"time_s": 120, "label": "5 分钟生活圈"}},
    "walk_speed_mps": 1.25,
    "avg_household_size": 2.6,
    "composite_weights": {"coverage": 0.3, "compliance": 0.3, "supply_demand": 0.2,
                          "redundancy": 0.1, "fairness": 0.05, "proximity_5min": 0.05},
    "elements": [
        {"key": "market", "name": "菜市场", "category": "基础保障", "required": True,
         "sitable": True, "min_count_15min": 1, "service_pop_per_unit": 1000, "source": "test"},
        {"key": "pharmacy", "name": "药店", "category": "便利服务", "required": True,
         "sitable": True, "min_count_15min": 1, "service_pop_per_unit": 800, "source": "test"},
    ],
}


def toy_dataset() -> Dataset:
    """一条 700 m 的直线路网 + 一个支路，末端组团超出 300 s 阈值。"""
    pts = {"N0": (0, 0), "N1": (100, 0), "N2": (200, 0), "N3": (0, 100),
           "N4": (400, 0), "N5": (700, 0)}
    nodes = []
    for nid, (dx, dy) in pts.items():
        lon, lat = _ll(dx, dy)
        nodes.append(Node(id=nid, lon=lon, lat=lat))
    links = [("N0", "N1"), ("N1", "N2"), ("N2", "N4"), ("N4", "N5"), ("N0", "N3")]
    coord = {n.id: (n.lon, n.lat) for n in nodes}
    edges = []
    for u, v in links:
        d = haversine_m(*coord[u], *coord[v])
        edges.append(Edge(u=u, v=v, length_m=round(d, 2), kind="residential", speed_mps=1.25))
    resid = []
    for rid, (nid, hh) in zip(["R0", "R1", "R4"], [("N0", 400), ("N2", 600), ("N5", 500)]):
        lon, lat = coord[nid]
        resid.append(ResidentialPoint(id=rid, name=f"组团{rid}", lon=lon, lat=lat, households=hh))
    flo, fla = coord["N2"]
    plo, pla = coord["N1"]
    facs = [Facility(id="F1", name="菜市场", element="market", lon=flo, lat=fla),
            Facility(id="F2", name="药店", element="pharmacy", lon=plo, lat=pla)]
    community = {"id": "toy", "name": "测试社区", "center": {"lon": LON0, "lat": LAT0},
                 "extent_m": {"half_width": 800, "half_height": 800}}
    return Dataset(community=community, nodes=nodes, edges=edges, residential=resid,
                   facilities=facs, meta={"source": "toy"})


def island_dataset(with_water: bool = False) -> Dataset:
    """复现真实踩到的坑：小区内部路画到离大马路 40 m 处就停了（OSM 没接上）。

    主路网 A-B-C 沿 y=0；内部路 I0-I1 沿 y=40（相距 40 m ≤ 60 m 补连阈值）。
    with_water=True 时在两段路之间铺一条水面，这时**不能**补连（等于凭空造桥）。
    """
    pts = {"A": (0, 0), "B": (200, 0), "C": (400, 0), "I0": (0, 40), "I1": (200, 40)}
    nodes = [Node(id=k, lon=_ll(dx, dy)[0], lat=_ll(dx, dy)[1]) for k, (dx, dy) in pts.items()]
    coord = {n.id: (n.lon, n.lat) for n in nodes}
    edges = []
    for u, v in [("A", "B"), ("B", "C"), ("I0", "I1")]:
        edges.append(Edge(u=u, v=v, length_m=round(haversine_m(*coord[u], *coord[v]), 2),
                          kind="residential", speed_mps=1.25))
    res = [ResidentialPoint(id="R0", name="临街组团", lon=coord["B"][0], lat=coord["B"][1],
                            households=600),
           ResidentialPoint(id="R1", name="院内组团", lon=coord["I1"][0], lat=coord["I1"][1],
                            households=400)]
    facs = [Facility(id="F1", name="菜市场", element="market", lon=coord["B"][0], lat=coord["B"][1]),
            Facility(id="F2", name="药店", element="pharmacy", lon=coord["C"][0], lat=coord["C"][1])]
    meta = {"source": "toy-island"}
    if with_water:
        meta["water_polygons"] = [[_ll(-100, 30), _ll(300, 30), _ll(300, 50),
                                   _ll(-100, 50), _ll(-100, 30)]]
    community = {"id": "toy-island", "name": "断头路测试", "center": {"lon": LON0, "lat": LAT0},
                 "extent_m": {"half_width": 800, "half_height": 800}}
    return Dataset(community=community, nodes=nodes, edges=edges, residential=res,
                   facilities=facs, meta=meta)


class TestSchema(unittest.TestCase):
    def test_roundtrip(self):
        ds = toy_dataset()
        with tempfile.TemporaryDirectory() as tmp:
            path = save_dataset(ds, Path(tmp) / "d.json")
            back = load_dataset(path)
        self.assertEqual(len(back.nodes), len(ds.nodes))
        self.assertEqual(back.community["id"], "toy")
        self.assertEqual(back.total_households(), 1500)
        self.assertEqual(len(back.facilities_by_element()["market"]), 1)


class TestGraph(unittest.TestCase):
    def test_dijkstra_cutoff(self):
        g = RoadGraph.from_dataset(toy_dataset())
        d = g.dijkstra("N0", 300)
        self.assertAlmostEqual(d["N2"], 160.0, delta=3)      # 200 m / 1.25 m/s
        self.assertNotIn("N5", d)                            # 700 m = 560 s > 300 s

    def test_nearest_node_snap(self):
        g = RoadGraph.from_dataset(toy_dataset())
        lon, lat = _ll(680, 10)
        nid, dist = g.nearest_node(lon, lat)
        self.assertEqual(nid, "N5")
        self.assertLess(dist, 25)


class TestAccessibility(unittest.TestCase):
    def test_reachable_and_cutoff(self):
        ds = toy_dataset()
        g = RoadGraph.from_dataset(ds)
        acc = compute_access(ds, g, 300)
        self.assertEqual(len(acc.elements_of("R0")["market"]), 1)
        self.assertEqual(len(acc.elements_of("R0")["pharmacy"]), 1)
        self.assertEqual(acc.elements_of("R4"), {})          # 末端组团 300 s 内不可达

    def test_circle_grid_and_sources(self):
        ds = toy_dataset()
        g = RoadGraph.from_dataset(ds)
        grid = build_circle_grid(ds, g, 300, cell_m=40.0)
        counts = element_access_grid(ds, g, TOY_ELEMENTS, grid, 300)
        self.assertEqual(counts["required_total"], 2)
        self.assertEqual(len(counts["counts"]), grid.nrows)
        poly = circle_polygon(ds, grid, 300)
        self.assertIsNotNone(poly)                            # shapely 已安装时应能并成多边形
        self.assertGreater(poly.area, 0)


class TestLivableMask(unittest.TestCase):
    """覆盖底图的居住用地蒙版：只影响显示，不改数；水体与无居住区不着色。"""

    def _ds_with_water(self) -> Dataset:
        ds = toy_dataset()
        # 河道：压住 N2–N4 段（200–400 m）的一块水面（±40 m 宽，跨过格心）
        ds.meta["water_polygons"] = [[_ll(200, -40), _ll(400, -40), _ll(400, 40),
                                      _ll(200, 40), _ll(200, -40)]]
        return ds

    def test_masks_water_and_keeps_values(self):
        ds = self._ds_with_water()
        g = RoadGraph.from_dataset(ds)
        grid = build_circle_grid(ds, g, 300, cell_m=40.0)
        full = element_access_grid(ds, g, TOY_ELEMENTS, grid, 300, mask_non_residential=False)
        masked = element_access_grid(ds, g, TOY_ELEMENTS, grid, 300)

        colored = lambda d: sum(1 for row in d["counts"] for v in row if v >= 0)
        self.assertGreater(colored(full), colored(masked))        # 蒙版确实抠掉了格子
        self.assertGreater(masked["mask"]["masked_water"], 0)
        self.assertEqual(masked["required_total"], full["required_total"])
        # 保留下来的格子数值必须与原图完全一致（蒙版不改数）
        for r in range(grid.nrows):
            for c in range(grid.ncols):
                if masked["counts"][r][c] >= 0:
                    self.assertEqual(masked["counts"][r][c], full["counts"][r][c])

        # 河道中心那一格必须被抠掉
        lon, lat = _ll(300, 0)
        c = int((lon - grid.lon0) * m_per_deg_lon(grid.lat0) / grid.cell_m)
        r = int((lat - grid.lat0) * M_PER_DEG_LAT / grid.cell_m)
        self.assertEqual(masked["counts"][r][c], -1)

    def test_diagnose_unaffected_by_mask(self):
        """蒙版是渲染规则：开着蒙版算出来的六维指标必须与关掉时一致。"""
        ds = self._ds_with_water()
        with_mask = diagnose(ds, TOY_ELEMENTS)
        self.assertIn("mask", with_mask["grid_counts"])
        self.assertAlmostEqual(with_mask["dimensions"]["coverage"]["score"],
                               diagnose(toy_dataset(), TOY_ELEMENTS)["dimensions"]["coverage"]["score"],
                               places=6)


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.ds = toy_dataset()
        self.diag = diagnose(self.ds, TOY_ELEMENTS)

    def test_coverage_and_compliance(self):
        rows = {r["key"]: r for r in self.diag["elements"]}
        # R0/R1 可达，R4 不可达：户数加权 1000/1500
        self.assertAlmostEqual(rows["market"]["coverage_rate"], 1000 / 1500, places=4)
        self.assertAlmostEqual(rows["pharmacy"]["coverage_rate"], 1000 / 1500, places=4)
        pts = {p["id"]: p for p in self.diag["points"]}
        self.assertEqual(pts["R0"]["missing"], [])
        self.assertEqual(set(pts["R4"]["missing"]), {"market", "pharmacy"})
        self.assertAlmostEqual(self.diag["dimensions"]["compliance"]["score"], 1000 / 1500, places=4)

    def test_problems_and_serialisation(self):
        self.assertTrue(self.diag["problems"])
        payload = strip_runtime(self.diag)
        self.assertNotIn("grid", payload)
        self.assertNotIn("access", payload)
        json.dumps(payload)                                   # 必须可 JSON 序列化
        self.assertIn("ncols", payload["grid_meta"])

    def test_gini_edges(self):
        self.assertAlmostEqual(weighted_gini([1, 1, 1], [3, 2, 1]), 0.0, places=6)
        self.assertGreater(weighted_gini([0, 1], [1, 1]), 0.4)


class TestOptimize(unittest.TestCase):
    def test_greedy_improves_coverage(self):
        ds = toy_dataset()
        g = RoadGraph.from_dataset(ds)
        acc = compute_access(ds, g, 300)
        opt = optimize_sites(ds, TOY_ELEMENTS, element="market", k=1, graph=g, access=acc)
        self.assertGreaterEqual(len(opt["sites"]), 1)
        self.assertGreater(opt["after"]["coverage_rate"], opt["before"]["coverage_rate"])
        self.assertGreater(opt["gain"]["households"], 0)
        self.assertIn("R4", opt["sites"][0]["newly_covered_points"])

    def test_default_target_skips_non_sitable(self):
        cfg = json.loads(json.dumps(TOY_ELEMENTS))
        cfg["elements"][1]["required"] = True
        ds = toy_dataset()
        opt = optimize_sites(ds, cfg, k=1)
        self.assertIn(opt["element"], {"market", "pharmacy"})


class TestProviders(unittest.TestCase):
    def test_synthetic_is_deterministic(self):
        community = resolve_community(load_communities())
        build = get_provider("synthetic")
        a, b = build(community), build(community)
        self.assertEqual([n.id for n in a.nodes], [n.id for n in b.nodes])
        self.assertEqual([round(n.lon, 7) for n in a.nodes], [round(n.lon, 7) for n in b.nodes])
        self.assertEqual(len(a.facilities), len(b.facilities))
        self.assertEqual(a.meta["bridges"], 3)
        self.assertTrue(a.meta["river"])
        self.assertGreater(a.total_households(), 10000)

    def test_synthetic_dataset_diagnoses(self):
        community = resolve_community(load_communities())
        cfg = load_elements(ROOT / "config" / "elements.json")
        ds = get_provider("synthetic")(community)
        diag = diagnose(ds, cfg)
        self.assertEqual(len(diag["dimensions"]), 6)
        self.assertTrue(0 <= diag["summary"]["composite_score"] <= 100)
        self.assertEqual(len(diag["elements"]), len(cfg["elements"]))

    def test_osm_tag_mapping(self):
        self.assertEqual(map_osm_tags({"highway": "bus_stop"}), "bus_stop")
        self.assertEqual(map_osm_tags({"amenity": "marketplace"}), "market")
        self.assertEqual(map_osm_tags({"shop": "greengrocer"}), "market")
        self.assertEqual(map_osm_tags({"railway": "station"}), "metro")
        self.assertEqual(map_osm_tags({"leisure": "park"}), "park")
        self.assertIsNone(map_osm_tags({"amenity": "school", "isced:level": "2,3"}))
        self.assertIsNone(map_osm_tags({"highway": "motorway"}))

    def test_baidu_tag_mapping(self):
        self.assertEqual(map_baidu_tags("曹杨菜市场", "农贸市场", "购物;农贸市场"), "market")
        self.assertEqual(map_baidu_tags("曹杨社区卫生服务站"), "community_health")
        self.assertEqual(map_baidu_tags("地铁曹杨路站", "地铁站"), "metro")
        self.assertEqual(map_baidu_tags("好德便利店"), "convenience")
        self.assertIsNone(map_baidu_tags("华东师范大学"))       # 大学不能算小学
        self.assertIsNone(map_baidu_tags("曹杨中学"))
        self.assertIsNone(map_baidu_tags("花鸟市场"))


class TestResidentialScope(unittest.TestCase):
    """分析范围口径：百度地点检索是**圆形**、半径比方形分析范围大一圈（半边长 800 m
    → 半径 1040 m）。框外住宅区 POI 若被当成分析单元，就会出现"地图上有小区、栅格里
    却没有色块"，并系统性拉低覆盖率。OSM 侧一直有这道判定，百度补充路径曾漏掉。
    """

    COMM = {"center": {"lon": 121.4, "lat": 31.24},
            "extent_m": {"half_width": 800, "half_height": 800}}

    def _dlon(self, meters: float) -> float:
        lat = self.COMM["center"]["lat"]
        return meters / (111320.0 * math.cos(math.radians(lat)))

    def test_inside_and_outside(self):
        c = self.COMM["center"]
        self.assertTrue(in_analysis_box(self.COMM, c["lon"], c["lat"]))
        self.assertTrue(in_analysis_box(self.COMM, c["lon"], c["lat"] + 700 / 111320.0))
        # 1000 m：落在圆形检索半径内（1040 m）、但已在分析框外 → 必须判为框外
        self.assertFalse(in_analysis_box(self.COMM, c["lon"], c["lat"] + 1000 / 111320.0))
        self.assertFalse(in_analysis_box(self.COMM, c["lon"] + self._dlon(900), c["lat"]))

    def test_search_radius_exceeds_box(self):
        """检索半径比方框半边长大约 30% —— 这正是框外 POI 的来源。"""
        _, radius = search_area(self.COMM)
        self.assertAlmostEqual(radius, 1040, delta=1)
        self.assertGreater(radius, self.COMM["extent_m"]["half_width"])


class TestCRS(unittest.TestCase):
    """坐标系统换算回归：基准点取自同一地物的 OSM(WGS-84) 与百度(BD-09) 实测坐标。

    如果这里失败，说明 POI 与路网会错位约 1.1 km，所有可达性结论都不可信。
    """

    CASES = [
        # 名称, WGS-84（OSM）, BD-09（百度地理编码）, 允许误差(米)
        ("曹杨公园", (121.404177, 31.241711), (121.415344, 31.245903), 20.0),
        ("兰溪青年公园", (121.400199, 31.242614), (121.411357, 31.246777), 25.0),
    ]

    def test_wgs84_to_bd09_matches_baidu(self):
        from lifecycle_metrics.crs import wgs84_to_bd09
        for name, wgs, bd, tol in self.CASES:
            got = wgs84_to_bd09(*wgs)
            err = haversine_m(got[0], got[1], bd[0], bd[1])
            self.assertLess(err, tol, f"{name} 换算误差 {err:.1f} m 超过 {tol} m")

    def test_roundtrip_within_one_metre(self):
        from lifecycle_metrics.crs import bd09_to_wgs84, wgs84_to_bd09
        for lon, lat in [(121.4042, 31.2417), (121.4737, 31.2304), (116.3975, 39.9087)]:
            back = bd09_to_wgs84(*wgs84_to_bd09(lon, lat))
            self.assertLess(haversine_m(lon, lat, back[0], back[1]), 1.0)

    def test_offset_is_about_one_km_in_shanghai(self):
        """把量级钉在测试里：上海地区 WGS-84 与 BD-09 相差约 1 km，不是几十米。"""
        from lifecycle_metrics.crs import wgs84_to_bd09
        bd = wgs84_to_bd09(121.4042, 31.2417)
        d = haversine_m(121.4042, 31.2417, bd[0], bd[1])
        self.assertGreater(d, 800.0)
        self.assertLess(d, 2000.0)

    def test_out_of_china_passthrough(self):
        """境外不偏移。注意：粗判据是经纬度包围盒，东京落在盒内会被误偏移，属已知局限。"""
        from lifecycle_metrics.crs import wgs84_to_bd09
        self.assertEqual(wgs84_to_bd09(2.3522, 48.8566), (2.3522, 48.8566))   # 巴黎


class TestPipeline(unittest.TestCase):
    """户数装配：实测覆盖 / 总量校准 / 幂等性。

    幂等性这条是踩坑后加的：数据集带缓存复用，若在已校准的户数上再乘一次系数会复利缩放
    （实测出现过 77,745 → ×0.87 → 67,543 的错误结果）。
    """

    def _community(self, tmp: Path, calibration=None) -> dict:
        ds_path = tmp / "c.dataset.json"
        toy = toy_dataset()
        save_dataset(toy, ds_path)
        cfg = {"id": "toypipe", "name": "toy", "dataset": str(ds_path),
               "provider": "synthetic", "center": toy.community["center"],
               "extent_m": toy.community["extent_m"]}
        if calibration:
            cfg["population_calibration"] = calibration
        return cfg

    def test_calibration_is_idempotent(self):
        from lifecycle_metrics.pipeline import assemble_dataset
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._community(Path(tmp), {"target_population": 78000,
                                              "basis": "测试用总量", "sources": ["unit test"]})
            ds1, info1 = assemble_dataset(cfg, TOY_ELEMENTS)
            pop1 = ds1.total_households() * TOY_ELEMENTS["avg_household_size"]
            ds2, info2 = assemble_dataset(cfg, TOY_ELEMENTS)          # 第二次装配
            pop2 = ds2.total_households() * TOY_ELEMENTS["avg_household_size"]
        self.assertTrue(info1["population_calibration"]["applied"])
        self.assertAlmostEqual(pop1, 78000, delta=800)
        self.assertAlmostEqual(pop2, pop1, delta=1)                  # 不允许复利缩放
        self.assertEqual(info2["households_restored_to_base"], len(ds2.residential))

    def test_measured_csv_overrides_and_skips_calibration(self):
        from lifecycle_metrics.pipeline import assemble_dataset
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._community(Path(tmp), {"target_population": 78000, "basis": "x"})
            csv_dir = ROOT / "data" / "households"
            csv_dir.mkdir(parents=True, exist_ok=True)
            csv_path = csv_dir / "toypipe.csv"
            csv_path.write_text("name,households\n组团R0,1234\n", encoding="utf-8")
            try:
                ds, info = assemble_dataset(cfg, TOY_ELEMENTS)
            finally:
                csv_path.unlink(missing_ok=True)
        self.assertTrue(info["household_overrides"]["applied"])
        self.assertEqual(info["household_overrides"]["matched"], 1)
        self.assertFalse(info["population_calibration"]["applied"])   # 实测优先
        got = {r.id: r.households for r in ds.residential}
        self.assertEqual(got["R0"], 1234)


class TestSiting(unittest.TestCase):
    """选址可建性约束：候选点必须落在载体环带内，且不入水体、不进禁建区。"""

    def _ds_with_siting(self) -> Dataset:
        """构造：载体在 (600,0)，水体盖住其东侧、禁建区在载体西侧路边。

        注意几何要落在"离路网 ≤70 m"的可达范围内，否则候选点会先被可达性过滤掉，
        测不到水体/禁建区这两条约束。
        """
        ds = toy_dataset()
        base_lon, base_lat = _ll(600, 0)
        w1, w2 = _ll(720, -30), _ll(900, 30)                  # 水体条带（东侧）
        water = [[w1[0], w1[1]], [w2[0], w1[1]], [w2[0], w2[1]], [w1[0], w2[1]]]
        ex_lon, ex_lat = _ll(460, 0)                          # 禁建区（西侧路边）
        ds.meta.update({
            "carriers": [{"name": "测试地铁口", "kind": "tier1_transit", "tier": 1,
                          "lon": base_lon, "lat": base_lat}],
            "carrier_meta": {"tier1_transit": {"tier": 1, "name": "地铁站/交通节点周边",
                                               "band": [50, 150], "note": "测试"}},
            "carrier_counts": {"tier1_transit": 1},
            "exclusions": [{"name": "测试派出所", "kind": "government", "radius_m": 40,
                            "lon": ex_lon, "lat": ex_lat, "reason": "党政机关/公安"}],
            "exclusion_meta": {"government": {"name": "党政机关/公安", "radius_m": 40}},
            "water_polygons": [water], "water_lines": [],
            "siting_note": "单元测试用",
        })
        return ds

    def test_candidates_limited_to_carrier_band_and_filtered(self):
        from lifecycle_metrics.optimize import optimize_sites
        ds = self._ds_with_siting()
        opt = optimize_sites(ds, TOY_ELEMENTS, element="market", k=1, cell_m=25)

        self.assertEqual(opt["candidate_mode"], "carriers")
        self.assertEqual(opt["siting_basis"]["carriers_available"], 1)
        self.assertGreater(opt["rejected"]["water"], 0)        # 水体确实筛掉了候选
        self.assertGreater(opt["rejected"]["exclusion"], 0)    # 禁建区确实筛掉了候选
        carrier = ds.meta["carriers"][0]
        for s in opt["sites"]:
            d = haversine_m(s["lon"], s["lat"], carrier["lon"], carrier["lat"])
            self.assertGreaterEqual(d, 40.0)                   # 环带下界（含 1 格容差）
            self.assertLessEqual(d, 160.0)                     # 环带上界
            self.assertEqual(s["tier"], 1)
            self.assertEqual(s["carrier"]["name"], "测试地铁口")
            self.assertFalse(s["constraints"]["in_water"])
            self.assertFalse(s["constraints"]["in_exclusion"])

    def test_tier_coefficient_prefers_transit(self):
        from lifecycle_metrics.optimize import TIER_COEF
        self.assertGreater(TIER_COEF[1], TIER_COEF[3])
        self.assertGreater(TIER_COEF[3], TIER_COEF[4])
        self.assertNotIn(5, TIER_COEF)          # 第五梯队已放弃（POI 数据不可得）

    def test_co_location_gap_from_config(self):
        from lifecycle_metrics.config import load_elements
        from lifecycle_metrics.optimize import CO_LOCATION_GAP, _same_kind_gap, element_map
        cfg = load_elements(ROOT / "config" / "elements.json")
        emap = element_map(cfg)
        self.assertEqual(_same_kind_gap("market", emap, None), CO_LOCATION_GAP["independent"])
        self.assertEqual(_same_kind_gap("canteen", emap, None), CO_LOCATION_GAP["compatible"])
        self.assertEqual(_same_kind_gap("market", emap, 999.0), 999.0)   # 显式覆盖优先


class TestPoiClassification(unittest.TestCase):
    """百度 POI 分类闸门：名称里带关键词的商家太多了，必须用 tag/type 二次确认。

    这些都是真实抓到的错分（不是假想用例）：曹杨 9 个"地铁站"里 5 个是
    "XX酒店(…地铁站店)"、停车场；"申亚时代广场"被算成公园；
    "XX社区卫生服务中心-地上停车场"被算成一处卫生站。
    """

    CASES = [
        ("如家精选酒店(上海普陀华师大梅岭北路地铁站店)-地上停车场", "交通设施;停车场",
         "life 交通设施;停车场", None),
        ("枫桥路", "交通设施;地铁站", "交通设施;地铁站", "metro"),
        ("铂悦酒店(上海真如地铁站环宇城店)", "酒店", "hotel 酒店;经济型", None),
        ("曹杨路地铁站-地铁4通道口-长城大厦", "出入口;其他", "出入口;其他", None),
        ("茶百道(曹杨路地铁站店)", "美食;饮品", "food", None),
        ("曹杨路南石路", "交通设施;公交车站", "交通设施;公交车站", "bus_stop"),
        ("上海动物园", "交通设施;地铁站", "交通设施;地铁站", "metro"),
        ("星星充电充电站(申亚时代广场直流)", "交通设施;充电站", "life 交通设施;充电站", None),
        ("赢华国际广场", "房地产;写字楼", "house 房地产;写字楼", None),
        ("余家便利店(绿地科创大厦店)", "购物;便利店", "shopping 购物;便利店", "convenience"),
        ("曹杨公园", "旅游景点;公园", "scope 旅游景点;公园;城市公园", "park"),
        ("风铃绿地", "旅游景点;公园", "scope 旅游景点;公园", "park"),
        ("曹杨社区卫生服务中心", "医疗;其他", "hospital 医疗;医院;社区卫生服务中心", "community_health"),
        ("上海虹桥万丽酒店-游泳池", "运动健身;体育场馆", "life 运动健身;游泳馆", None),
        ("乐刻运动健身(兰溪路店)", "运动健身;健身中心", "life 运动健身;健身房", "fitness"),
    ]

    def test_map_baidu_tags_gate(self):
        for name, tag, type_str, want in self.CASES:
            with self.subTest(name=name):
                if is_auxiliary(name, tag, type_str):
                    self.assertIsNone(want or None)
                    continue
                self.assertEqual(map_baidu_tags(name, tag, type_str), want)

    def test_auxiliary_poi(self):
        self.assertTrue(is_auxiliary("曹杨社区卫生服务中心-地上停车场", "交通设施;停车场", ""))
        self.assertTrue(is_auxiliary("北新泾地铁站-1申亚时代广场口", "出入口;其他", ""))
        self.assertFalse(is_auxiliary("曹杨公园", "旅游景点;公园", "scope 旅游景点;公园"))
        self.assertFalse(is_auxiliary("PASSION健身", "运动健身;健身中心", "life 运动健身;健身房"))


class TestDayRoute(unittest.TestCase):
    """一天动线：路径必须沿路网、逐段可加总、缺口要被显式标出。"""

    def _route(self, **kw):
        from lifecycle_metrics.route import plan_day_route
        ds = toy_dataset()
        return ds, plan_day_route(ds, RoadGraph.from_dataset(ds), "R0", TOY_ELEMENTS, **kw)

    def test_path_reconstruction(self):
        g = RoadGraph.from_dataset(toy_dataset())
        p = g.shortest_path("N0", "N5")
        self.assertEqual(p["nodes"], ["N0", "N1", "N2", "N4", "N5"])
        self.assertAlmostEqual(p["time_s"], g.dijkstra("N0", 1e9)["N5"], places=2)
        self.assertAlmostEqual(p["length_m"], 699.2, delta=1.0)
        self.assertIsNone(g.shortest_path("N0", "N5", 100))       # 阈值内不可达
        self.assertEqual(g.shortest_path("N0", "N0")["nodes"], ["N0"])

    def test_route_legs_and_totals(self):
        ds, r = self._route()
        self.assertEqual(r["point"]["id"], "R0")
        self.assertTrue(r["stops"])
        # 每段折线：起点是"上一处（或家）"，终点是本站的吸附节点 —— 离路段单独用 stub 表示，
        # 所以折线本身必须落在路网上（不穿墙）
        prev = (r["point"]["lon"], r["point"]["lat"])
        for s in r["stops"]:
            self.assertGreaterEqual(len(s["leg_coords"]), 1)
            first, last = s["leg_coords"][0], s["leg_coords"][-1]
            self.assertLess(haversine_m(*prev, *first), 260.0)          # 上一处（含离路距离）
            self.assertLess(haversine_m(*last, s["lon"], s["lat"]), 260.0)
            prev = (s["lon"], s["lat"])
        total = sum(s["walk_min"] for s in r["stops"]) + (r["home_leg"] or {}).get("walk_min", 0)
        self.assertAlmostEqual(r["totals"]["walk_min"], round(total, 1), places=1)
        self.assertGreater(r["totals"]["walk_m"], 0)
        self.assertIn("assumptions", r)
        self.assertIn("15 分钟生活圈", r["assumptions"]["note"])

    def test_missing_element_is_reported(self):
        # R4 离设施超过 300 s 阈值 → 必须给出"全缺口"结论，而不是抛异常或静默跳过
        ds = toy_dataset()
        from lifecycle_metrics.route import plan_day_route
        r = plan_day_route(ds, RoadGraph.from_dataset(ds), "R4", TOY_ELEMENTS)
        self.assertEqual(r["stops"], [])
        self.assertEqual(r["totals"]["missing_count"], len(TOY_ELEMENTS["elements"]))
        self.assertFalse(r["totals"]["complete"])
        self.assertIn("无", r["order_used"])

    def test_stay_time_override(self):
        _, r = self._route(stay_min={"market": 3})
        stop = next(s for s in r["stops"] if s["element"] == "market")
        self.assertEqual(stop["stay_min"], 3)
        self.assertLess(r["totals"]["span_min"], r["totals"]["walk_min"] + 300)


class TestPersona(unittest.TestCase):
    """住户画像层：一张可核对的规则表，而不是大模型现场判人。

    要守住的三件事：① 画像真的改变动线；② 步速按比例改变"15 分钟能走多远"；
    ③ 设施不是全天可用，赶不上要变成"服务时段冲突 + 改法"，而不是偷偷把一天拉长。
    """

    ELDER = {
        "key": "elder", "name": "活力老人", "walk_speed_mps": 0.85, "start_clock": "09:00",
        "max_leg_min": 12.0, "chain": ["market", "pharmacy"],
        "stay_min": {"market": 25, "pharmacy": 10},
        "windows": {"market": [[330, 720]], "pharmacy": [[480, 1260]]},
    }

    def _route(self, persona=None, **kw):
        from lifecycle_metrics.route import plan_day_route
        ds = toy_dataset()
        return plan_day_route(ds, RoadGraph.from_dataset(ds), "R0", TOY_ELEMENTS,
                              persona=persona, **kw)

    def test_persona_drives_speed_and_order(self):
        r = self._route(self.ELDER)
        self.assertEqual(r["assumptions"]["walk_speed_mps"], 0.85)
        self.assertEqual(r["assumptions"]["start_clock"], "09:00")
        self.assertEqual(r["persona"]["key"], "elder")
        self.assertEqual([s["element"] for s in r["stops"]], ["market", "pharmacy"])
        self.assertEqual(r["stops"][0]["stay_min"], 25)          # 画像的停留时长
        self.assertEqual(r["stops"][0]["leg_limit_min"], 12.0)

    def test_speed_scales_reachable_set(self):
        """0.60 m/s 的助行器比 1.25 m/s 的成人少走到东西 —— 同一张图，只是时间换算不同。"""
        fast = self._route(None, walk_speed=1.25)
        slow = self._route(None, walk_speed=0.60)
        self.assertLess(slow["totals"]["stops"], fast["totals"]["stops"])
        self.assertLess(fast["assumptions"]["speed_scale"], slow["assumptions"]["speed_scale"])
        # 路网上的时间是按 cfg 速度预算的，换步速只做比例缩放：距离不变、时间变长
        common = ({s["element"] for s in fast["stops"]}
                  & {s["element"] for s in slow["stops"]})
        self.assertTrue(common, "两种步速至少应有一项共同可达")
        el = sorted(common)[0]
        f = next(s for s in fast["stops"] if s["element"] == el)
        s = next(x for x in slow["stops"] if x["element"] == el)
        self.assertAlmostEqual(f["walk_m"], s["walk_m"], delta=2.0)
        self.assertGreater(s["walk_min"], f["walk_min"])

    def test_window_closed_becomes_conflict_with_advice(self):
        # 09:00 出发菜市场（05:30–12:00）开着；推到 13:30 出发就该撞上"已收市"
        ok = self._route(self.ELDER, start_clock="09:00")
        self.assertEqual(ok["conflicts"], [])
        late = self._route(self.ELDER, start_clock="13:30")
        kinds = {c["kind"] for c in late["conflicts"]}
        self.assertIn("closed", kinds)
        self.assertTrue(any(x["level"] == "高" for x in late["recommendations"]))
        self.assertEqual(late["totals"]["conflict_count"], len(late["conflicts"]))

    def test_early_arrival_waits_but_silly_wait_becomes_conflict(self):
        """早到 ≤15 分钟就地等；早到太久不硬等（否则一天被拉长几小时），记成冲突。"""
        from lifecycle_metrics.route import _align_window
        self.assertEqual(_align_window(500, [[510, 690]]), (510, 10, None))       # 等 10 分钟
        c, w, conflict = _align_window(400, [[510, 690]])                        # 早 110 分钟
        self.assertEqual((c, w), (400, 0.0))
        self.assertEqual(conflict["kind"], "early")
        self.assertEqual(_align_window(600, [[510, 690]]), (600, 0.0, None))     # 时段内

    def test_over_leg_limit_is_flagged(self):
        r = self._route(self.ELDER)
        for s in r["stops"]:
            self.assertEqual(bool(s["over_leg_limit"]), s["walk_min"] > 12.0)
        self.assertTrue(all("leg_limit_min" in s for s in r["stops"]))

    def test_personas_config_is_valid(self):
        """config/personas.json 要被真正读进来，且每条画像都自洽。"""
        from lifecycle_metrics.config import load_personas
        cfg = load_personas()
        ps = cfg.get("personas") or []
        self.assertGreaterEqual(len(ps), 5)
        keys = {e["key"] for e in load_elements()["elements"]}
        for p in ps:
            self.assertTrue(p.get("chain"), p["key"])
            self.assertTrue(set(p["chain"]) <= keys, f"{p['key']} 出行链含未知要素")
            self.assertGreater(p["walk_speed_mps"], 0)
            self.assertRegex(p["start_clock"], r"^\d{2}:\d{2}$")
            for el, wins in (p.get("windows") or {}).items():
                self.assertIn(el, keys)
                self.assertTrue(wins and all(len(w) == 2 and w[0] < w[1] for w in wins), el)


class TestFocusIsochrone(unittest.TestCase):
    """单组团等时圈（风圈）：几何必须自洽 —— 圈内距离不得超过该圈半径。"""

    def setUp(self):
        from lifecycle_metrics.isochrone import focus_isochrone
        ds = toy_dataset()
        self.ds = ds
        self.iso = focus_isochrone(ds, RoadGraph.from_dataset(ds), TOY_ELEMENTS, "R0", cell_m=20.0)

    def test_bands_are_nested_and_bounded(self):
        iso, g = self.iso, self.iso["grid"]
        m_lon = m_per_deg_lon(g["lat0"])
        hx = (iso["point"]["lon"] - g["lon0"]) * m_lon
        hy = (iso["point"]["lat"] - g["lat0"]) * M_PER_DEG_LAT
        self.assertEqual(g["nrows"], len(g["rows"]))
        self.assertTrue(all(len(r) == g["ncols"] for r in g["rows"]))
        self.assertLessEqual(set("".join(g["rows"])), set("0123"))
        # 家所在的格子必须是第 0 圈
        self.assertEqual(g["rows"][int(hy / g["cell_m"])][int(hx / g["cell_m"])], "0")
        # 每一圈的格子，直线距离都不得超过该圈半径（步行距离 ≥ 直线距离）
        for r, row in enumerate(g["rows"]):
            for c, ch in enumerate(row):
                if ch == "3":
                    continue
                d = math.hypot((c + 0.5) * g["cell_m"] - hx, (r + 0.5) * g["cell_m"] - hy)
                self.assertLessEqual(d, iso["bands"][int(ch)]["radius_m"] + g["cell_m"],
                                     f"cell {r},{c} 超出第 {ch} 圈半径")

    def test_areas_and_compactness(self):
        iso = self.iso
        areas = [b["area_km2"] for b in iso["bands"]]
        self.assertLess(areas[0], areas[1])
        self.assertLess(areas[1], areas[2])
        cp = iso["compactness"]
        self.assertLessEqual(cp["ratio"], 1.0)
        self.assertGreater(cp["ratio"], 0.0)
        self.assertAlmostEqual(cp["actual_km2"], areas[2], places=6)
        self.assertEqual(cp["radius_15_m"], round(1.25 * 300))    # TOY 阈值 300 s

    def test_elements_counts_are_monotonic(self):
        counts = [b["elements_count"] for b in self.iso["bands"]]
        self.assertEqual(counts, sorted(counts))
        self.assertLessEqual(counts[-1], self.iso["bands"][-1]["elements_total"])

    def test_directions_cover_16_compass_points(self):
        iso = self.iso
        self.assertEqual(len(iso["directions"]), 16)
        self.assertEqual([d["deg"] for d in iso["directions"]], [i * 22.5 for i in range(16)])
        self.assertLessEqual(max(d["reach_m"] for d in iso["directions"]), iso["max_reach_m"])
        for b in iso["blind_directions"]:
            self.assertLess(b["reach_m"], iso["max_reach_m"])

    def test_extent_limited_directions_are_not_reported_as_cut(self):
        """贴分析范围边缘的组团：走向范围外的方向是**口径边界**，不是路网切断。

        不区分就会误报"南侧被切断 190 m"（其实只是走到了分析范围边上），
        这条误报会直接进报告与选址提示。
        """
        from lifecycle_metrics.isochrone import focus_isochrone
        self.assertTrue(all(d.get("limited_by") in ("network", "extent")
                            for d in self.iso["directions"]))
        self.assertFalse({d["label"] for d in self.iso["blind_directions"]}
                         & set(self.iso["extent_limited_directions"]))

        # 把分析范围收到 240 m 见方：16 个方向全部先撞到边界（120 m 步行仅 96 s，
        # 远在 300 s 阈值之内）→ 一个"被切断的方向"都不该报，且全部应标为 extent。
        ds = toy_dataset()
        ds.community["extent_m"] = {"half_width": 120, "half_height": 120}
        iso = focus_isochrone(ds, RoadGraph.from_dataset(ds), TOY_ELEMENTS, "R0", cell_m=20.0)
        self.assertEqual(len(iso["extent_limited_directions"]), 16)
        self.assertEqual(iso["blind_directions"], [])
        # 斜向走到方框角点，直线距离是半宽 × √2；再多不超过一格（采样步长）
        self.assertTrue(all(d["reach_m"] <= 120 * math.sqrt(2) + 20
                            for d in iso["directions"]),
                        [(d["label"], d["reach_m"]) for d in iso["directions"]])

    def test_overlay_png_and_bounds(self):
        from lifecycle_metrics.raster import focus_overlay_bounds, render_focus_overlay_png
        with tempfile.TemporaryDirectory() as tmp:
            out = render_focus_overlay_png(self.ds, self.iso, Path(tmp) / "iso.png", size=256)
            self.assertTrue(out.exists() and out.stat().st_size > 500)
        b = focus_overlay_bounds(self.ds)
        self.assertLess(b["sw"][0], b["ne"][0])
        self.assertLess(b["sw"][1], b["ne"][1])
        e = self.ds.community["extent_m"]
        self.assertAlmostEqual((b["ne"][1] - b["sw"][1]) * M_PER_DEG_LAT, 2 * e["half_height"], delta=2)


class TestNetworkStitching(unittest.TestCase):
    """断头路补连 + "路网未连通"组团的处理（真实踩到的坑，见 docs/metrics.md）。"""

    def test_gap_is_stitched_and_cluster_becomes_assessable(self):
        from lifecycle_metrics.network import analyze_network, stitch_components
        ds = island_dataset()
        g0 = RoadGraph.from_dataset(ds)
        before = analyze_network(ds, g0)
        self.assertEqual(before["components"], 2)              # 主路 + 内部路 两块
        self.assertIn("R1", before["isolated_points"])         # 院内组团先落在孤立块里

        edges, stitches = stitch_components(ds.nodes, ds.edges,
                                           water_polygons=[], water_lines=[])
        self.assertTrue(stitches)
        self.assertLessEqual(max(s["length_m"] for s in stitches if "length_m" in s), 60.0)
        ds.edges = edges
        after = analyze_network(ds, RoadGraph.from_dataset(ds))
        self.assertEqual(after["components"], 1)
        self.assertEqual(after["isolated_points"], {})

    def test_water_gap_is_not_stitched_and_cluster_is_unassessable(self):
        from lifecycle_metrics.network import analyze_network, stitch_components
        ds = island_dataset(with_water=True)
        edges, stitches = stitch_components(ds.nodes, ds.edges,
                                            water_polygons=ds.meta["water_polygons"])
        self.assertEqual([s for s in stitches if "length_m" in s], [])   # 跨水一律不连
        ds.edges = edges
        net = analyze_network(ds, RoadGraph.from_dataset(ds))
        self.assertIn("R1", net["isolated_points"])

        diag = diagnose(ds, TOY_ELEMENTS)
        pts = {p["id"]: p for p in diag["points"]}
        self.assertEqual(pts["R1"]["network_status"], "isolated")
        self.assertFalse(pts["R1"]["assessable"])
        # 无法评估的组团不进覆盖率分母，也不进问题清单
        self.assertEqual(diag["summary"]["unassessable_households"], 400)
        self.assertEqual(diag["summary"]["assessable_households"], 600)
        self.assertAlmostEqual(diag["elements"][0]["coverage_rate"], 1.0, places=4)
        for p in diag["problems"]:
            self.assertNotIn("R1", p["uncovered_points"])
        self.assertEqual(diag["data_quality"]["unassessable_households"], 400)
        json.dumps(strip_runtime(diag))                        # 仍可序列化

    def test_diagnose_assessable_by_default(self):
        ds = island_dataset()
        diag = diagnose(ds, TOY_ELEMENTS)
        self.assertEqual(diag["summary"]["unassessable_households"], 400)   # 未补连时如实标注


class TestReport(unittest.TestCase):
    def test_report_files(self):
        ds = toy_dataset()
        diag = diagnose(ds, TOY_ELEMENTS)
        with tempfile.TemporaryDirectory() as tmp:
            files = build_report(ds, diag, TOY_ELEMENTS, tmp, k=1)
            names = {k: Path(v).exists() for k, v in files.items()}
        for name, exists in names.items():
            self.assertTrue(exists, name)
        self.assertIn("report.md", names)
        self.assertIn("map_solution.png", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
