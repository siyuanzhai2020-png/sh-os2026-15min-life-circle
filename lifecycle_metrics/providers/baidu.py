"""百度地图数据源：POI（地点检索）+ 复用步行路网，产出标准 Dataset。

两个关键设计决定（评审会问，文档里也要写清）：

1. **步行路网仍来自 OSM**。百度地图开放平台的 Web 服务 API 不提供可下载的步行路网，
   只有点对点的步行路径规划；用后者逐格构造等时圈会立刻打爆配额。
   因此本 provider 的定位是：**百度地图提供权威 POI 与地理编码，路网用 OSM 自建**，
   并用百度步行路径规划做抽样校验（见 docs/baidu-ak.md 的 AK 需求）。
2. **所有请求都落盘缓存**（data/cache/baidu-*.json），重复运行不消耗配额。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..crs import bd09_to_wgs84, wgs84_to_bd09
from ..env import baidu_credentials, load_env_file
from ..geo import haversine_m, m_per_deg_lat, m_per_deg_lon
from ..schema import Dataset, Facility, ResidentialPoint
from .osm import build_osm_dataset

PLACE_SEARCH_URL = "/place/v2/search"
WALKING_URL = "/directionlite/v1/walking"
DEFAULT_HOST = "https://api.map.baidu.com"
# 百度 POI 不含户数：住宅区组团先用缺省值，正式提交前应替换为实测户数
DEFAULT_HOUSEHOLDS = 600
# OSM 建筑推算出的户数密度低于该值（户/km²）就认为 OSM 覆盖不足，用百度住宅区 POI 补空白。
# 参照：上海中心城区居住密度约 1–1.5 万户/km²，街道级行政区一般不低于 5 千户/km²。
RESIDENTIAL_DENSITY_FLOOR = 3000.0
# 百度住宅区 POI 与 OSM 组团相距小于该值视为同一处，不重复计入
RESIDENTIAL_DEDUP_M = 150.0
# 给"居住组团 N"这类通用名挂真实小区名的搜索半径
RESIDENTIAL_NAME_RADIUS_M = 250.0
GENERIC_CLUSTER_RE = re.compile(r"^居住组团\s*\d+$")

# 关键词 → 要素 key，顺序敏感（先匹配到的优先）
BAIDU_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("metro", ["地铁站", "轨道交通站", "轻轨站", "地铁出入口"]),
    ("bus_stop", ["公交站", "公交车站", "公交枢纽"]),
    ("community_health", ["社区卫生服务", "卫生服务站", "卫生院", "诊所"]),
    ("canteen", ["社区食堂", "长者食堂", "老年食堂", "助餐点", "助餐", "惠民饭堂"]),
    ("market", ["菜市场", "菜场", "农贸市场", "生鲜市场", "生鲜超市", "生鲜集市", "副食品"]),
    ("pharmacy", ["药店", "药房", "医药"]),
    ("kindergarten", ["幼儿园", "托儿所", "幼托", "幼儿学园"]),
    ("primary_school", ["小学"]),
    ("culture_center", ["文化活动中心", "文化中心", "文化馆", "社区活动中心", "党群服务中心"]),
    ("fitness", ["健身", "球场", "体育馆", "体育场", "步道", "游泳馆", "运动场"]),
    ("park", ["口袋公园", "公园", "绿地", "广场"]),
    ("convenience", ["便利店", "社区超市", "超市"]),
]

EXCLUDE_KEYWORDS: Dict[str, List[str]] = {
    "primary_school": ["大学", "中学", "高中", "职校", "附属中学"],
    "market": ["花鸟市场", "建材市场", "家具市场", "批发市场", "电脑市场", "菜场管理"],
    "park": ["高尔夫", "游乐园", "停车场", "美食城", "美食广场", "充电站", "写字楼"],
    "canteen": ["食堂承包", "餐饮管理"],
    "fitness": ["健身房有限公司", "酒店", "宾馆", "民宿", "会所泳池"],
    "metro": ["号线"],          # 线路名不是站点
    "convenience": ["超市配送", "超市仓储"],
}

# 检索用的查询词（每个要素多个词，按页翻取后合并去重）
QUERIES: Dict[str, List[str]] = {
    "market": ["菜市场", "农贸市场", "生鲜超市"],
    "community_health": ["社区卫生服务中心", "社区卫生服务站"],
    "canteen": ["社区食堂", "助餐点"],
    "pharmacy": ["药店"],
    "kindergarten": ["幼儿园"],
    "primary_school": ["小学"],
    "culture_center": ["社区文化活动中心", "文化活动中心"],
    "fitness": ["健身路径", "社区体育场", "健身广场", "市民球场"],
    "park": ["公园", "口袋公园"],
    "bus_stop": ["公交站"],
    "metro": ["地铁站"],
    "convenience": ["便利店"],
}
RESIDENTIAL_QUERIES = ["住宅区", "小区"]

# ---------------------------------------------------------------------------
# 选址用的"可建载体"五梯队（来自赛题方的优先选址排序）。
# 便民设施在市区几乎不可能靠空地新建，只能依托既有大楼/街边/商场/小区内部，
# 所以候选点位必须落在这些载体附近。band = (最小距离, 最大距离)，单位米。
# ---------------------------------------------------------------------------
CARRIER_QUERIES: Dict[str, List[str]] = {
    "tier1_transit": ["地铁站", "地铁出入口", "轨道交通站"],
    "tier2_service": ["街道社区服务中心", "党群服务中心", "社区事务受理服务中心",
                      "居委会", "街道办事处"],
    "tier3_commercial": ["购物中心", "商场", "超市", "写字楼", "商务楼", "大厦"],
    "tier4_public": ["医院", "图书馆", "文化馆", "体育馆", "广场", "公园"],
}
CARRIER_META: Dict[str, Dict[str, Any]] = {
    "tier1_transit": {"tier": 1, "name": "地铁站/交通节点周边", "band": (50, 150),
                      "relation": "邻近",
                      "note": "客流条件最好；设施布局于站点影响范围以外，须避让消防通道与客流疏散空间，需专项审批"},
    "tier2_service": {"tier": 2, "name": "街道/社区服务中心、党群服务中心、居委会",
                      "band": (0, 80), "relation": "合署",
                      "note": "与基层服务设施合署设置，居民到达路径熟悉"},
    "tier3_commercial": {"tier": 3, "name": "商业综合体/超市/菜市场/写字楼大堂",
                         "band": (0, 80), "relation": "合署",
                         "note": "宜设于一层临街或大堂入口附近，客流可见性高"},
    "tier4_public": {"tier": 4, "name": "医院/图书馆/公园/广场/体育场馆入口",
                     "band": (60, 120), "relation": "邻近",
                     "note": "人流集聚度高；应设于院区外围，医院入口周边不宜过度集聚"},
}
CARRIER_CAP = {"tier1_transit": 60, "tier2_service": 80, "tier3_commercial": 80,
               "tier4_public": 50}
# 注：赛题方给出的"第五梯队（公交枢纽/地下通道/闲置亭棚）"已放弃——
# 百度 POI 里这类点位实测取不到（曹杨新村 0 处），公交站属于常规设施而非枢纽，
# 报刊亭/核酸亭等闲置设施无数据源。需要时必须由街道提供台账（见 docs/siting.md）。

# ---------------------------------------------------------------------------
# 禁建/不宜区（在 POI 位置周围设排除半径）。口径：**基层便民场所可作载体；
# 区级及以上党政机关、公安、消防、司法、军事、市政设施、校园与院区内部排除**。
# ---------------------------------------------------------------------------
EXCLUSION_QUERIES: Dict[str, List[str]] = {
    "government": ["公安局", "派出所", "法院", "检察院", "消防站", "区委区政府", "武警"],
    "utility": ["变电站", "加油站", "加气站"],
    "education": ["小学", "中学", "大学"],   # 校园内部不可建
    "medical": ["医院"],                      # 院区内部不可建（入口附近仍可作载体）
}
EXCLUSION_META: Dict[str, Dict[str, Any]] = {
    "government": {"name": "党政机关/公安/司法/消防/军事", "radius_m": 45},
    "utility": {"name": "市政设施（变电站/加油站等）", "radius_m": 45},
    "education": {"name": "学校校园内部", "radius_m": 60},
    "medical": {"name": "医院院区内部", "radius_m": 60},
}
EXCLUDE_NAME_KEYWORDS = ["区委", "区政府", "人民政府", "党委", "人大", "政协", "公安",
                         "派出所", "法院", "检察院", "司法", "消防", "武警", "军区", "部队",
                         "变电站", "加油站", "加气站", "燃气", "油库"]

# 载体名称质量过滤：检索词会"蹭"到别的业态（"地铁站"检索出"XX酒店(地铁站店)"），
# 这些不是我们想要的载体，必须剔掉
CARRIER_NAME_BLOCK = {
    "tier1_transit": ["酒店", "宾馆", "公寓", "客栈", "民宿", "餐厅", "咖啡", "超市", "便利店",
                      "银行", "营业厅", "停车场", "充电站", "公厕", "驿站", "商场", "广场",
                      "食品", "零食", "小吃", "奶茶", "水果", "花店", "宠物"],
    "tier4_public": ["社区卫生服务", "卫生服务站", "诊所", "宠物", "医务室", "护理站",
                     "停车场", "公厕"],
}
# "XX广场"在中文里常是购物中心，归到商业梯队更准确
SHOPPING_HINTS = ["购物", "商场", "百货", "购物中心"]

# 同一要素内按距离合并（同一地铁站的多个出入口/重复 POI 必须并成一个站）
CLUSTER_RADIUS_M: Dict[str, float] = {"metro": 250.0, "bus_stop": 60.0, "park": 80.0}
DEFAULT_CLUSTER_M = 35.0
# 地铁出入口名字：如"枫桥路地铁站-3口"、"2号口"
ENTRANCE_RE = re.compile(r"(\d+\s*口$|号口|出入口|通道口|出口$|入口$|[A-Za-z]\s*口$)")
# 载体表里"这条是不是出入口/通道口"的判定：出入口出现在名字中间也算
# （"大场镇地铁站-地铁1通道口-客运交通枢纽"结尾不是"口"，但它是通道口 POI）
STATION_ENTRANCE_RE = re.compile(r"(号口|出入口|通道口|出口|入口)")
# 把出入口/通道口名归一成站名："行知路地铁站-1口"→"行知路地铁站"，
# "大场镇地铁站-地铁1通道口-客运交通枢纽"→"大场镇地铁站"
STATION_NAME_CUT = re.compile(r"[-—－·]?\s*(?:地铁)?\s*[A-Za-z]?\d*\s*(?:号口|出入口|通道口|出口|入口|口)")
STATION_CLUSTER_M = 250.0    # 同一座地铁站的多个 POI 归并半径（与要素表 metro 的合并口径一致）
# 归一后仍不像"一座站"的：站内设施（电梯/安检/控制室…）与蹭词的商家
STATION_FACILITY_RE = re.compile(r"(电梯|安检|警务|控制室|站厅|站台|卫生间|厕所|售票|充值|"
                                 r"服务中心|出口|入口|通道|旅舍|旅馆|招待所)")


# 只有 tag/type 明确是这一类设施才算 —— 百度 POI 的**名称**里带关键词的商家太多了：
# 实测曹杨 9 个"地铁站"里 5 个是"XX酒店(…地铁站店)"、停车场，还有"曹杨路地铁站-地铁4通道口-长城大厦"
# 这种出入口 POI。只按键名匹配会把酒店算成地铁站，既虚高覆盖率，又让"一天动线"走去酒店。
KIND_GATE: Dict[str, str] = {
    "metro": r"(地铁站|轻轨站|轨道交通站)",
    "bus_stop": r"(公交|巴士|车站|枢纽)",
    # "广场""绿地"这类词在商家名里遍地都是（申亚时代广场、绿地科创大厦），
    # 只有 tag/type 属于公园绿地才算公园，否则"充电站/写字楼"都会被算成公园
    "park": r"(公园|绿地|风景|景区|游园|植物园|园林|花园|广场)",
}

# 附属设施 POI：停车场、充电站、出入口 —— 与母体设施同址，算进来只会虚增设施数，
# 而且"XX社区卫生服务中心-地上停车场"会在体检表里冒充一处卫生站
AUX_TAG_RE = re.compile(r"停车场|充电站")
AUX_NAME_RE = re.compile(r"(停车场|停车楼|充电站|出入口|通道口)")
ENTRANCE_TAIL_RE = re.compile(r"口$")


def is_auxiliary(name: str, tag: str = "", type_str: str = "") -> bool:
    """停车/充电/出入口这类附属 POI：不作为独立设施计入。"""
    name = name or ""
    if AUX_TAG_RE.search(f"{tag} {type_str}") or AUX_NAME_RE.search(name):
        return True
    # "北新泾地铁站-1申亚时代广场口" 这类以"…口"结尾的地铁出入口
    return bool(ENTRANCE_TAIL_RE.search(name) and re.search(r"(地铁站|轻轨站|轨道交通)", name))


def kind_ok(element: str, tag: str = "", type_str: str = "") -> bool:
    """该 POI 的 tag/type 是否真的属于这一类设施（纯函数，便于单测）。"""
    pat = KIND_GATE.get(element)
    if not pat:
        return True
    return bool(re.search(pat, f"{tag} {type_str}"))


def poi_fields(poi: Dict[str, Any]) -> Tuple[str, str, str]:
    """百度 POI 的 tag/type 在 detail_info 里（顶层是空的），这里统一取出来。"""
    info = poi.get("detail_info") or {}
    name = poi.get("name") or ""
    tag = poi.get("tag") or info.get("tag") or info.get("classified_poi_tag") or ""
    type_str = " ".join(str(x) for x in (
        poi.get("type"), info.get("type"), info.get("classified_poi_tag"), info.get("label")) if x)
    return name, str(tag), type_str


def merge_nearby(facilities: List[Facility]) -> Tuple[List[Facility], int]:
    """同要素内按距离阈值合并重复 POI（返回合并后的列表与被合并数量）。"""
    kept: List[Facility] = []
    merged = 0
    for f in facilities:
        radius = CLUSTER_RADIUS_M.get(f.element, DEFAULT_CLUSTER_M)
        if any(k.element == f.element and haversine_m(k.lon, k.lat, f.lon, f.lat) < radius
               for k in kept):
            merged += 1
            continue
        kept.append(f)
    return kept, merged


def map_baidu_tags(name: str, tag: str = "", type_str: str = "") -> Optional[str]:
    """把百度 POI 的名称/标签映射到要素 key（纯函数，便于单测）。

    两级判定：先按键名/类型匹配关键词，再用 `kind_ok` 要求 tag/type 真的属于该类
    （地铁站、公交站这两类必须过闸，否则"XX酒店(…地铁站店)"会被算成站点）。
    """
    blob = f"{name} {tag} {type_str}"
    for element, words in BAIDU_KEYWORDS:
        if any(w in blob for w in words):
            if any(x in blob for x in EXCLUDE_KEYWORDS.get(element, [])):
                continue
            if not kind_ok(element, tag, type_str):
                continue
            return element
    return None


def _collect_baidu_residential(ak: str, location: Tuple[float, float], radius_m: int,
                               sk: Optional[str], cache: Path,
                               max_pages: int = 3) -> List[Dict[str, Any]]:
    """检索"住宅区 / 小区"POI（WGS-84），用于补齐 OSM 建筑覆盖不足的居住组团。"""
    found: List[Dict[str, Any]] = []
    seen: set = set()
    for query in RESIDENTIAL_QUERIES:
        for page in range(max_pages):
            data = _place_search(ak, query, location, radius_m, page_num=page, sk=sk,
                                 cache_dir=cache)
            results = data.get("results", [])
            if not results:
                break
            for poi in results:
                name, tag, type_str = poi_fields(poi)
                loc = poi.get("location") or {}
                lon, lat = loc.get("lng"), loc.get("lat")
                if lon is None or lat is None:
                    continue
                if not any(w in f"{name} {tag} {type_str}"
                           for w in ("住宅区", "小区", "公寓", "居住", "家园", "花园", "新村", "苑")):
                    continue
                wlon, wlat = bd09_to_wgs84(float(lon), float(lat))
                key = (round(wlon, 5), round(wlat, 5))
                if key in seen:
                    continue
                seen.add(key)
                found.append({"name": name or "住宅区", "lon": round(wlon, 7),
                              "lat": round(wlat, 7), "uid": poi.get("uid"),
                              "address": poi.get("address")})
            if len(results) < 20:
                break
    return found


def search_area(community: Dict[str, Any]) -> Tuple[Tuple[float, float], int]:
    """社区的百度检索参数：(BD-09 中心 (lat, lon), 检索半径米)。"""
    center = community["center"]
    bd_lon, bd_lat = wgs84_to_bd09(center["lon"], center["lat"])
    radius_m = int(max(community["extent_m"]["half_width"],
                       community["extent_m"]["half_height"]) * 1.3)
    return (bd_lat, bd_lon), radius_m


def in_analysis_box(community: Dict[str, Any], lon: float, lat: float) -> bool:
    """点是否落在**分析范围**内（口径与 OSM 组团一致，见 providers/osm.py::_bbox）。

    为什么必须有这道判定：百度地点检索是**圆形**检索，半径取 max(半宽, 半高)×1.3，
    比方形分析范围大一圈（半边长 800 m 时半径 1040 m，四角更远）。检索圈内、分析框外
    的住宅区 POI 如果计入分析单元，会同时造成三个口径问题：

    1. 栅格只覆盖分析范围，框外组团没有对应格子 → "地图上有小区、却没有色块"；
    2. 设施只统计分析范围内的，框外组团只能在更远的条件下评估 → 系统性拉低覆盖率；
    3. 社区之间不可比：框外部分取决于检索半径与分析框的面积比，各社区并不相同。

    OSM 侧的居住组团一直有这道判定（osm.py: in_core），百度补充路径此前漏了。
    """
    c = community["center"]
    hw = community["extent_m"]["half_width"] / m_per_deg_lon(c["lat"])
    hh = community["extent_m"]["half_height"] / m_per_deg_lat()
    return c["lon"] - hw <= lon <= c["lon"] + hw and c["lat"] - hh <= lat <= c["lat"] + hh


def name_clusters_from_poi(residential: List[ResidentialPoint],
                           baidu_resid: List[Dict[str, Any]],
                           radius_m: float = RESIDENTIAL_NAME_RADIUS_M) -> int:
    """把只有通用名（"居住组团 37"）的组团挂上最近百度「小区/住宅区」POI 的名字。

    为什么不改名就是问题：点开一个组团看"一天动线"、或者在报告里写"补齐 XX 组团"，
    名字是"居住组团 37"就没法讲；挂上"金菊小区""元丰花园社区"之后，
    报告读起来就是一份能直接交给街道的东西。多个组团落在同一个小区里是正常的
    （120 m 聚合尺度会把一个大小区切成几块），此时共享同名即可。
    """
    if not baidu_resid:
        return 0
    named = 0
    for r in residential:
        if not r.name or not GENERIC_CLUSTER_RE.match(r.name.strip()):
            continue
        best: Optional[Dict[str, Any]] = None
        best_d = float("inf")
        for p in baidu_resid:
            d = haversine_m(r.lon, r.lat, p["lon"], p["lat"])
            if d < best_d:
                best, best_d = p, d
        if best is not None and best_d <= radius_m:
            r.name = best["name"]
            r.tags["named_from"] = {"source": "baidu_poi", "poi": best["name"],
                                    "distance_m": round(best_d, 1)}
            named += 1
    return named


def _sn(path: str, params: Dict[str, Any], sk: str) -> str:
    """百度地图服务端 AK 的 SN 校验签名。

    规则（以官方文档为准，接入前请复核）：
    sn = md5(请求路径 + "?" + 已 urlencode 的查询串 + SK)，取 32 位小写。
    """
    query = urllib.parse.urlencode(sorted((k, v) for k, v in params.items() if k != "sn"))
    raw = f"{path}?{query}{sk}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def geocode_wgs84(address: str, city: str = "上海市", ak: Optional[str] = None,
                  sk: Optional[str] = None, cache_dir: str | Path = "data/cache"
                  ) -> Tuple[float, float, Dict[str, Any]]:
    """地址 → WGS-84 经纬度（百度地理编码返回 BD-09，这里换算好再返回）。

    返回 (lon, lat, meta)，meta 含 confidence / level / 行政区，便于人工核对。
    """
    load_env_file()
    creds = baidu_credentials()
    ak = ak or creds["ak"] or creds["web_ak"]
    sk = sk if sk is not None else creds["sk"]
    if not ak:
        raise RuntimeError("缺少服务端 AK（BAIDU_SERVER_AK）")

    path = "/geocoding/v3/"
    params: Dict[str, Any] = {"address": address, "city": city, "output": "json", "ak": ak}
    if sk:
        params["sn"] = _sn(path, params, sk)
    cache = Path(cache_dir) / ("baidu-geo-" + hashlib.md5(
        json.dumps(params, sort_keys=True).encode()).hexdigest()[:12] + ".json")
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        import requests
        r = requests.get(DEFAULT_HOST + path, params=params, timeout=20,
                         headers={"User-Agent": "lifecycle-metrics/0.1"})
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 0:
            raise RuntimeError(f"百度地理编码失败 status={data.get('status')} "
                               f"message={data.get('message')}（地址：{address}）")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    res = data.get("result") or {}
    loc = res.get("location") or {}
    if not loc:
        raise RuntimeError(f"地理编码没有返回坐标：{address}")
    lon, lat = bd09_to_wgs84(float(loc["lng"]), float(loc["lat"]))
    comp = res.get("addressComponent") or {}
    meta = {"source": "baidu_geocoding", "bd09": [loc["lng"], loc["lat"]],
            "confidence": res.get("confidence"), "level": res.get("level"),
            "district": f"{comp.get('city', '')}{comp.get('district', '')}",
            "precise": res.get("precise"), "address": address}
    return round(lon, 6), round(lat, 6), meta


def _place_search(ak: str, query: str, location: Tuple[float, float], radius_m: int,
                  page_num: int = 0, page_size: int = 20, sk: Optional[str] = None,
                  cache_dir: Path = Path("data/cache"), host: str = DEFAULT_HOST,
                  timeout: int = 20, crs_in: str = "bd09") -> Dict[str, Any]:
    """地点检索。**location 必须是 BD-09**（百度地图坐标系），由调用方负责换算。"""
    lat, lon = location
    params: Dict[str, Any] = {
        "query": query, "location": f"{lat},{lon}", "radius": radius_m,
        "output": "json", "scope": "2", "page_num": page_num, "page_size": page_size,
        "ak": ak,
    }
    if sk:
        params["sn"] = _sn(PLACE_SEARCH_URL, params, sk)
    key_src = json.dumps({"q": query, "loc": f"{lat:.5f},{lon:.5f}", "r": radius_m,
                          "p": page_num}, ensure_ascii=False)
    cache_path = cache_dir / f"baidu-{hashlib.md5(key_src.encode()).hexdigest()[:12]}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    import requests  # 延迟导入，便于无网络时导入本模块
    url = host + PLACE_SEARCH_URL
    resp = requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": "lifecycle-metrics/0.1"})
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    if status != 0:
        raise RuntimeError(f"百度地点检索失败 status={status} message={data.get('message')} "
                           f"（301/302=配额或权限问题，401=AK 无效，参考 docs/baidu-ak.md）")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.2)  # 温和限速，保护配额
    return data


def _collect_element_points(ak: str, element: str, location: Tuple[float, float],
                            radius_m: int, sk: Optional[str], cache_dir: Path,
                            max_pages: int = 3) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for query in QUERIES.get(element, []):
        for page in range(max_pages):
            data = _place_search(ak, query, location, radius_m, page_num=page, sk=sk,
                                 cache_dir=cache_dir)
            results = data.get("results", [])
            if not results:
                break
            out.extend(results)
            if len(results) < 20:
                break
    return out


def clean_station_name(name: str) -> str:
    """地铁 POI 名归一成站名。

    "行知路地铁站-1口"                 → "行知路地铁站"
    "大场镇地铁站-地铁1通道口-客运交通枢纽" → "大场镇地铁站"
    "行知路站地铁站安检区"              → "行知路站地铁站"
    "无障碍电梯(上海动物园地铁站4号口)"   → "无障碍电梯"（随后会被 is_valid_station_carrier 否掉）
    """
    raw = str(name or "").strip()
    head = STATION_NAME_CUT.split(raw)[0]              # 砍掉出入口/通道口及其后的尾巴
    head = re.sub(r"[（(][^）)]*$", "", head)           # 括号里是出入口说明时，去掉未闭合的括号
    m = re.match(r"^(.*?地铁站)", head)                # "…地铁站安检区" → "…地铁站"
    if m and m.end() < len(head):
        head = m.group(1)
    return head.strip(" -—－·（）()") or raw


def is_valid_station_carrier(raw_name: str) -> bool:
    """归一之后，这条 POI 还像不像"一座地铁站"。

    否掉的是站在站里的设施与蹭词的商家："无障碍电梯(上海动物园地铁站4号口)"、
    "行知路站地铁站安检区" 里挑出来的杂物、以及"追梦少年青年旅舍(场中路地铁站店)"。
    """
    clean = clean_station_name(raw_name)
    if not clean or len(clean) < 3:
        return False
    if STATION_FACILITY_RE.search(clean):
        return False
    return ("地铁站" in clean or "轻轨站" in clean or "轨道" in clean
            or clean.endswith("站"))


def is_station_entrance(name: str) -> bool:
    """这条地铁 POI 是不是出入口/通道口（而不是站体本身）。"""
    n = str(name or "")
    return bool(STATION_ENTRANCE_RE.search(n)) or bool(ENTRANCE_RE.search(n))


def cluster_stations(items: List[Dict[str, Any]],
                     radius_m: float = STATION_CLUSTER_M) -> List[Dict[str, Any]]:
    """把同一座地铁站的多个 POI 归并成一条载体。

    为什么必须归并：百度对一座站会给多条记录 —— "行知路地铁站"、
    "行知路地铁站-1口"、"行知路地铁站-3口"、"大场镇地铁站-地铁1通道口-客运交通枢纽"。
    早期只按 120 m 就近去重，而出入口之间常常超过这个距离，于是**同一座站被算成
    2–3 个独立载体**：候选环带在同一位置叠好几遍、tier1 的 1.5 倍系数被重复放大，
    点位卡片上还会写成"依托 行知路地铁站-1口"—— 读起来像要把设施建在地铁口里面。

    归并规则：站体优先当代表（有站体就用站体坐标与站名），只有出入口时用归一名，
    并把出入口数量记进 `entrances`，方便卡片上如实说明。
    """
    groups: List[Dict[str, Any]] = []
    for it in items:
        raw = str(it.get("name") or "")
        name = clean_station_name(raw)
        entrance = is_station_entrance(raw)
        hit = None
        for g in groups:
            if haversine_m(g["lon"], g["lat"], it["lon"], it["lat"]) <= radius_m:
                hit = g
                break
        if hit is None:
            groups.append({**it, "name": name, "_entrance": entrance, "_names": {name},
                           "entrances": 1 if entrance else 0})
            continue
        hit["_names"].add(name)
        hit["entrances"] += 1 if entrance else 0
        # 站体 POI 优先当代表；否则取更短（更干净）的名字
        if hit["_entrance"] and not entrance:
            hit.update(lon=it["lon"], lat=it["lat"], name=name, query=it.get("query"),
                       _entrance=False)
        elif hit["_entrance"] == entrance and 0 < len(name) < len(hit["name"]):
            hit["name"] = name
    out: List[Dict[str, Any]] = []
    for g in groups:
        g.pop("_entrance", None)
        names = g.pop("_names")
        # 展示名优先用带"地铁站/轨道"的那种写法（"行知路" → "行知路地铁站"）
        station_like = [n for n in names if "地铁站" in n or "轨道" in n]
        if station_like:
            g["name"] = min(station_like, key=len)
        out.append(g)

    # 第二道：同名归并。大型换乘站（如曹杨路站，3 线换乘）的出入口能拉开 400 m 以上，
    # 只靠距离会切成一簇一簇、最后冒出两个同名载体。
    by_name: Dict[str, Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []
    for g in out:
        key = g["name"]
        if key in by_name:
            tgt = by_name[key]
            tgt["entrances"] = tgt.get("entrances", 0) + g.get("entrances", 0)
            continue
        by_name[key] = g
        ordered.append(g)

    for g in ordered:
        if g.get("entrances", 0) > 1:
            g["name"] = f"{g['name']}（{g['entrances']} 个出入口）"
    return ordered


def _collect_carriers(ak: str, location: Tuple[float, float], radius_m: int,
                      sk: Optional[str], cache_dir: Path, max_pages: int = 2
                      ) -> List[Dict[str, Any]]:
    """收集"可建载体"点（五梯队），供选址优化判断候选点位置是否合理。"""
    out: List[Dict[str, Any]] = []
    for kind, queries in CARRIER_QUERIES.items():
        batch: List[Dict[str, Any]] = []
        seen: set = set()
        for query in queries:
            for page in range(max_pages):
                try:
                    data = _place_search(ak, query, location, radius_m, page_num=page, sk=sk,
                                         cache_dir=cache_dir)
                except Exception:  # noqa: BLE001
                    break
                results = data.get("results", [])
                if not results:
                    break
                for poi in results:
                    name, tag, type_str = poi_fields(poi)
                    if any(kw in f"{name}{tag}{type_str}" for kw in EXCLUDE_NAME_KEYWORDS):
                        continue                      # 载体自身不能是禁建类建筑
                    blob = f"{name}{tag}{type_str}"
                    if kind == "tier1_transit":
                        # 地铁梯队要"真的站点"：归一后仍须像个站名，且不能是站内设施或蹭词商家。
                        # 否则会收进「欢囍花生(环球港地铁店)」「无障碍电梯(上海动物园地铁站4号口)」
                        # 「追梦少年青年旅舍(场中路地铁站店)」这些只是名字里带"地铁站"的东西。
                        if not is_valid_station_carrier(name):
                            continue
                    if any(kw in name for kw in CARRIER_NAME_BLOCK.get(kind, [])):
                        continue                      # 蹭检索词的其它业态
                    use_kind = kind
                    if kind == "tier4_public" and any(h in blob for h in SHOPPING_HINTS):
                        use_kind = "tier3_commercial"  # "XX购物广场"其实是商业综合体
                    loc = poi.get("location") or {}
                    lon, lat = loc.get("lng"), loc.get("lat")
                    if lon is None or lat is None:
                        continue
                    uid = poi.get("uid") or f"{round(float(lon), 5)},{round(float(lat), 5)}"
                    if uid in seen:
                        continue
                    seen.add(uid)
                    wlon, wlat = bd09_to_wgs84(float(lon), float(lat))
                    batch.append({"name": name or use_kind, "kind": use_kind,
                                  "tier": CARRIER_META[use_kind]["tier"],
                                  "lon": round(wlon, 7), "lat": round(wlat, 7),
                                  "query": query})
                if len(results) < 20:
                    break
        out.extend(batch[:CARRIER_CAP.get(kind, 50)])   # 每梯队限量，避免候选点爆炸
    # 同梯队内按距离去重：同一站点的重复 POI 保留一条。
    # 地铁梯队用 250 m（出入口之间可以隔很远，120 m 合并不掉），并做站名归一。
    dedup: List[Dict[str, Any]] = []
    stations: List[Dict[str, Any]] = []
    for c in out:
        if c["kind"] == "tier1_transit":
            stations.append(c)
            continue
        if any(k["kind"] == c["kind"] and haversine_m(k["lon"], k["lat"], c["lon"], c["lat"]) < 120
               for k in dedup):
            continue
        dedup.append(c)
    return dedup + cluster_stations(stations)


def _collect_exclusions(ak: str, location: Tuple[float, float], radius_m: int,
                        sk: Optional[str], cache_dir: Path, max_pages: int = 2
                        ) -> List[Dict[str, Any]]:
    """收集禁建/不宜区（党政机关、公安消防、校园、院区、市政设施）。"""
    out: List[Dict[str, Any]] = []
    for kind, queries in EXCLUSION_QUERIES.items():
        seen: set = set()
        for query in queries:
            for page in range(max_pages):
                try:
                    data = _place_search(ak, query, location, radius_m, page_num=page, sk=sk,
                                         cache_dir=cache_dir)
                except Exception:  # noqa: BLE001
                    break
                results = data.get("results", [])
                if not results:
                    break
                for poi in results:
                    name, tag, type_str = poi_fields(poi)
                    # "医院"检索会带出社区卫生服务站/诊所——那是我们要规划的小型设施，
                    # 不是院区，不能按"院区内部不可建"排除
                    if kind == "medical" and any(
                            kw in name for kw in ("社区卫生服务", "卫生服务站", "诊所",
                                                  "宠物", "护理站", "医务室")):
                        continue
                    loc = poi.get("location") or {}
                    lon, lat = loc.get("lng"), loc.get("lat")
                    if lon is None or lat is None:
                        continue
                    uid = poi.get("uid") or f"{round(float(lon), 5)},{round(float(lat), 5)}"
                    if uid in seen:
                        continue
                    seen.add(uid)
                    wlon, wlat = bd09_to_wgs84(float(lon), float(lat))
                    out.append({"name": name or kind, "kind": kind,
                                "radius_m": EXCLUSION_META[kind]["radius_m"],
                                "reason": EXCLUSION_META[kind]["name"],
                                "lon": round(wlon, 7), "lat": round(wlat, 7)})
                if len(results) < 20:
                    break
    return out


def search_places(query: str, city: str = "上海市", limit: int = 8, ak: Optional[str] = None,
                  sk: Optional[str] = None, cache_dir: str | Path = "data/cache"
                  ) -> List[Dict[str, Any]]:
    """模糊搜索街道/地点（区域检索），返回候选列表供用户挑选。

    与 geocode_wgs84 的区别：地理编码要求"精确地址"，用户只输入"曹杨新村街道"这类
    模糊词时经常定位失败；地点检索本身就是模糊匹配，更适合"搜索框"交互。
    返回项含 WGS-84 坐标、地址、行政区与置信线索（是否有"街道办事处/街道"字样）。
    """
    load_env_file()
    creds = baidu_credentials()
    ak = ak or creds["ak"] or creds["web_ak"]
    sk = sk if sk is not None else creds["sk"]
    if not ak:
        raise RuntimeError("缺少服务端 AK（BAIDU_SERVER_AK）")

    params: Dict[str, Any] = {"query": query, "region": city, "output": "json",
                              "scope": "2", "page_size": min(20, max(1, limit)), "ak": ak}
    if sk:
        params["sn"] = _sn(PLACE_SEARCH_URL, params, sk)
    cache = Path(cache_dir) / ("baidu-search-" + hashlib.md5(
        json.dumps(params, sort_keys=True).encode()).hexdigest()[:12] + ".json")
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        import requests
        r = requests.get(DEFAULT_HOST + PLACE_SEARCH_URL, params=params, timeout=20,
                         headers={"User-Agent": "lifecycle-metrics/0.1"})
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 0:
            raise RuntimeError(f"地点检索失败 status={data.get('status')} message={data.get('message')}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    out: List[Dict[str, Any]] = []
    for poi in data.get("results", [])[:limit]:
        name, tag, type_str = poi_fields(poi)
        loc = poi.get("location") or {}
        lon, lat = loc.get("lng"), loc.get("lat")
        if lon is None or lat is None:
            continue
        wlon, wlat = bd09_to_wgs84(float(lon), float(lat))
        blob = f"{name}{tag}{type_str}{poi.get('address', '')}"
        out.append({
            "name": name, "address": poi.get("address") or "", "tag": tag, "type": type_str,
            "lon": round(wlon, 6), "lat": round(wlat, 6),
            "bd09": [round(float(lon), 6), round(float(lat), 6)],
            "looks_like_subdistrict": any(k in blob for k in ("街道", "镇", "乡", "社区")),
            "is_government": any(k in blob for k in ("政府", "办事处", "党群", "服务中心", "居委会")),
        })
    return out


def walking_route(origin: Tuple[float, float], destination: Tuple[float, float],
                  ak: Optional[str] = None, sk: Optional[str] = None,
                  cache_dir: str | Path = "data/cache",
                  host: str = DEFAULT_HOST, crs_in: str = "wgs84") -> Dict[str, Any]:
    """百度步行路径规划（用于校验自算路网）。

    origin/destination 默认按 **WGS-84** 传入（与数据集一致），内部换算成 BD-09 再请求。
    """
    load_env_file()
    creds = baidu_credentials()
    ak = ak or creds["ak"]
    sk = sk if sk is not None else creds["sk"]
    if not ak:
        raise RuntimeError("缺少服务端 AK（BAIDU_SERVER_AK）")

    if crs_in == "wgs84":
        origin = tuple(reversed(wgs84_to_bd09(origin[1], origin[0])))   # (lon,lat) → (lat,lon)
        destination = tuple(reversed(wgs84_to_bd09(destination[1], destination[0])))
    params: Dict[str, Any] = {
        "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
        "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
        "output": "json", "ak": ak,
    }
    if sk:
        params["sn"] = _sn(WALKING_URL, params, sk)
    cache_path = Path(cache_dir) / (
        "baidu-walk-" + hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12] + ".json")
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        import requests
        data = {}
        for attempt in range(3):
            resp = requests.get(host + WALKING_URL, params=params, timeout=20,
                                headers={"User-Agent": "lifecycle-metrics/0.1"})
            resp.raise_for_status()
            data = resp.json()
            # 并发配额限制（status=401 且提示并发）→ 退避重试
            if data.get("status") == 401 and "并发" in str(data.get("message", "")):
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if data.get("status") != 0:
            raise RuntimeError(f"百度步行路径规划失败 status={data.get('status')} "
                               f"message={data.get('message')}")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.45)   # 温和限速：百度对步行路径规划有并发配额

    routes = ((data.get("result") or {}).get("routes") or [])
    if not routes:
        return {"ok": False, "status": data.get("status"), "distance_m": None, "duration_s": None}
    return {"ok": True, "distance_m": routes[0].get("distance"),
            "duration_s": routes[0].get("duration"), "status": 0}


def build_baidu_dataset(community: Dict[str, Any], ak: Optional[str] = None,
                        sk: Optional[str] = None, cache_dir: str | Path = "data/cache",
                        network: str = "osm", max_pages: int = 3,
                        residential_source: str = "auto",
                        fallback_to_osm: bool = True) -> Dataset:
    load_env_file()
    creds = baidu_credentials()
    ak = ak or creds["ak"]
    sk = sk or creds["sk"]
    if not ak:
        raise RuntimeError("缺少服务端 AK：请在 .env.local 设置 BAIDU_SERVER_AK（见 docs/baidu-ak.md）")
    cache = Path(cache_dir)

    # 1) 步行路网：复用 OSM provider（带缓存），保证可达性可自算、不烧配额
    if network != "osm":
        raise ValueError("当前仅支持 network='osm'（百度开放平台不提供步行路网下载）")
    base = build_osm_dataset(community, cache_dir=cache, include_facilities=True)

    center = community["center"]
    # 百度接口吃 BD-09：把数据集里的 WGS-84 中心换算过去再检索
    location, radius_m = search_area(community)
    bd_lon, bd_lat = wgs84_to_bd09(center["lon"], center["lat"])

    facilities: List[Facility] = []
    seen: set[Tuple[str, int, int]] = set()
    unclassified: List[str] = []
    for element in QUERIES:
        for poi in _collect_element_points(ak, element, location, radius_m, sk, cache, max_pages):
            name, tag, type_str = poi_fields(poi)
            if is_auxiliary(name, tag, type_str):
                continue          # 停车场/充电站/出入口：与母体设施同址，不计独立设施
            key = map_baidu_tags(name, tag, type_str)
            if key is None:
                if len(unclassified) < 25:
                    unclassified.append(f"{name}｜{tag or type_str or '无标签'}")
                continue
            if key == "metro" and ENTRANCE_RE.search(name):
                continue          # 出入口不是站点，稍后由 250 m 聚类合并到站
            loc = poi.get("location") or {}
            lon, lat = loc.get("lng"), loc.get("lat")
            if lon is None or lat is None:
                continue
            dedup = (key, round(lon * 10 ** 4), round(lat * 10 ** 4))
            if dedup in seen:
                continue
            seen.add(dedup)
            wlon, wlat = bd09_to_wgs84(float(lon), float(lat))   # BD-09 → WGS-84（与 OSM 对齐）
            facilities.append(Facility(
                id=f"B{len(facilities):03d}", name=name or key, element=key,
                lon=round(wlon, 7), lat=round(wlat, 7), source="baidu",
                tags={"uid": poi.get("uid"), "tag": tag, "type": type_str,
                      "address": poi.get("address"), "from_query_element": element,
                      "bd09": [round(float(lon), 6), round(float(lat), 6)]}))

    facilities, merged_count = merge_nearby(facilities)

    # 2) 与 OSM 做并集融合：同要素 50 m 内或同名视为同一处设施。
    #    只用"百度为空才兜底"会漏掉百度分类粗、覆盖差的小微设施（健身点、活动室等）。
    osm_added: Dict[str, int] = {}
    for f in base.facilities:
        if f.element not in QUERIES:
            continue
        if any(k.element == f.element and
               (k.name == f.name or haversine_m(k.lon, k.lat, f.lon, f.lat) < 50.0)
               for k in facilities):
            continue
        osm_added[f.element] = osm_added.get(f.element, 0) + 1
        facilities.append(Facility(id=f"O{len(facilities):03d}", name=f.name, element=f.element,
                                   lon=f.lon, lat=f.lat, capacity=f.capacity, source="osm",
                                   tags={"note": "OSM 补充（百度 POI 未覆盖/分类缺失）"}))
    facilities, merged2 = merge_nearby(facilities)
    merged_count += merged2

    # 3) 选址辅助数据：可建载体（五梯队）与禁建/不宜区
    carriers = _collect_carriers(ak, location, radius_m, sk, cache, max_pages=2)
    exclusions = _collect_exclusions(ak, location, radius_m, sk, cache, max_pages=2)

    residential: List[ResidentialPoint] = []
    osm_resid = base.residential
    use_osm_resid = residential_source == "osm" or (
        residential_source == "auto" and len(osm_resid) >= 8)
    if use_osm_resid:
        residential = list(osm_resid)

    # 百度住宅区 POI：OSM 建筑覆盖不足时用来"补空白"（否则地图上会出现
    # "色块铺满、居民区图标却缺席"的错位观感 —— 见 docs/data-schema.md）。
    baidu_resid: List[ResidentialPoint] = []
    need_baidu_resid = residential_source == "baidu" or not use_osm_resid
    if residential_source == "auto" and use_osm_resid:
        area_km2 = max(0.25, (2 * community["extent_m"]["half_width"] / 1000.0)
                       * (2 * community["extent_m"]["half_height"] / 1000.0))
        density = sum(r.households for r in osm_resid) / area_km2
        need_baidu_resid = density < RESIDENTIAL_DENSITY_FLOOR      # 户/km² 明显偏低 → 补
    if residential_source in ("auto", "baidu"):
        baidu_resid = _collect_baidu_residential(ak, location, radius_m, sk, cache, max_pages)

    baidu_added = 0
    # 只有分析范围内的住宅区 POI 才作为分析单元；框外的仍保留在 baidu_resid 里，
    # 供下面的 name_clusters_from_poi 命名用（贴边的框内组团常由框外小区取名）。
    baidu_resid_core = [p for p in baidu_resid
                        if in_analysis_box(community, p["lon"], p["lat"])]
    baidu_resid_outside = len(baidu_resid) - len(baidu_resid_core)
    if not use_osm_resid and not baidu_resid_core:
        raise RuntimeError(
            "分析范围内没有任何居住组团（OSM 建筑不足，百度住宅区 POI 也全部落在分析范围外）："
            f"检索半径 {radius_m} m 内取到 {len(baidu_resid)} 个住宅区 POI，其中框内 0 个。"
            "请扩大分析范围，或把中心点移到居住用地上。")
    if not use_osm_resid:
        residential = [ResidentialPoint(
            id=f"BR{i:03d}", name=p["name"], lon=p["lon"], lat=p["lat"],
            households=DEFAULT_HOUSEHOLDS,
            tags={"uid": p["uid"], "address": p["address"],
                  "estimate": f"百度 POI 不含户数，按 {DEFAULT_HOUSEHOLDS} 户/组团缺省估算"})
            for i, p in enumerate(baidu_resid_core)]
    elif need_baidu_resid and baidu_resid_core:
        # 只在"离已有 OSM 组团足够远"的空白处补点，并沿用邻近 OSM 组团的中位户数，
        # 保证补进来的组团与 OSM 组团量纲一致（否则会凭空放大总户数）。
        near = [r.households for r in osm_resid]
        near_sorted = sorted(near)
        for i, p in enumerate(baidu_resid_core):
            if any(haversine_m(p["lon"], p["lat"], r.lon, r.lat) < RESIDENTIAL_DEDUP_M
                   for r in osm_resid):
                continue
            local = sorted(r.households for r in osm_resid
                           if haversine_m(p["lon"], p["lat"], r.lon, r.lat) < 400.0)
            hh = int(local[len(local) // 2]) if local else int(near_sorted[len(near_sorted) // 2])
            residential.append(ResidentialPoint(
                id=f"BR{i:03d}", name=p["name"], lon=p["lon"], lat=p["lat"],
                households=max(40, hh),
                tags={"uid": p["uid"], "address": p["address"], "source": "baidu",
                      "estimate": f"百度住宅区 POI 补 OSM 空白；户数取邻近 OSM 组团中位数 {max(40, hh)} 户"}))
            baidu_added += 1

    # 组团实名：OSM 只给得出"居住组团 37"这种通用名，用最近的百度小区 POI 命名，
    # 报告与"一天动线"里才能直接说清是哪个小区（见 name_clusters_from_poi）
    named_from_poi = name_clusters_from_poi(residential, baidu_resid)

    meta = dict(base.meta)
    meta.update({
        "source": "baidu+osm",
        "crs": "WGS-84",
        "crs_note": "数据集统一为 WGS-84：百度 POI 由 BD-09 换算为 WGS-84（误差实测 < 10 m），"
                    "检索中心与步行路径规划请求均按 BD-09 发出。",
        "note": "POI 来自百度地图开放平台（地点检索），步行路网与居住组团来自 OpenStreetMap；"
                "户数为按建筑轮廓×层数的估算值（见 tags.estimate），正式提交前应替换为街道/社区实测户数。",
        "poi_count": len(facilities),
        "poi_by_source": {"baidu": sum(1 for f in facilities if f.source == "baidu"),
                          "osm": sum(1 for f in facilities if f.source == "osm")},
        "residential_count": len(residential),
        "poi_merged_nearby": merged_count,
        "unclassified_samples": unclassified,
        "residential_source": ("baidu" if not use_osm_resid
                               else "osm+baidu" if baidu_added else "osm"),
        "residential_baidu_added": baidu_added,
        "residential_named_from_poi": named_from_poi,
        "residential_poi_outside_box": baidu_resid_outside,
        "residential_scope_note": "分析单元只取分析范围内的居住组团（OSM 与百度补充同一口径）；"
                                  "百度地点检索为圆形、半径比方形分析范围大，"
                                  f"本次落在框外、仅用于组团命名的住宅区 POI 有 {baidu_resid_outside} 个。",
        "residential_density_floor_hh_per_km2": RESIDENTIAL_DENSITY_FLOOR,
        "osm_supplemented_elements": osm_added,
        # 选址依据（详见 optimize.py 与 docs/siting.md）
        "carriers": carriers,
        "carrier_meta": CARRIER_META,
        "carrier_counts": {k: sum(1 for c in carriers if c["kind"] == k) for k in CARRIER_QUERIES},
        "exclusions": exclusions,
        "exclusion_meta": EXCLUSION_META,
        "siting_note": "市区便民设施无法空地新建，候选点位限定在五类既有载体周边；"
                       "并排除水体、党政机关/公安消防、校园与院区内部、市政设施等不宜区。",
    })
    return Dataset(community=community, nodes=base.nodes, edges=base.edges,
                   residential=residential or base.residential, facilities=facilities,
                   meta=meta)
