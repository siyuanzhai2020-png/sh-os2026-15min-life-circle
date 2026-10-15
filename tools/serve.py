"""交互式 Demo 服务：FastAPI 提供结构化 API + 静态前端。

    python tools/serve.py --port 8000
    → http://127.0.0.1:8000

设计：引擎在进程内计算并缓存（默认社区 0.3–0.5 秒），前端只消费 JSON，
所以整套 Demo 在没有地图 AK、没有外网时也是完整可演示的；
提供 Web 端 AK 后会额外加载百度地图 JSAPI GL 底图（见 docs/baidu-ak.md）。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query      # noqa: E402
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,  # noqa: E402
                               Response)
from fastapi.staticfiles import StaticFiles                           # noqa: E402

from lifecycle_metrics import RoadGraph, diagnose                        # noqa: E402
from lifecycle_metrics.accessibility import (build_circle_grid, circle_polygon,  # noqa: E402
                                             compute_access)
from lifecycle_metrics.config import (load_communities, load_elements,   # noqa: E402
                                      load_personas, persona_by_key,
                                      repo_path, resolve_community,
                                      save_communities, slugify_community)
from lifecycle_metrics.geo import haversine_m                            # noqa: E402
from lifecycle_metrics.metrics import strip_runtime                      # noqa: E402
from lifecycle_metrics.optimize import optimize_sites                    # noqa: E402
from lifecycle_metrics.pipeline import assemble_dataset                  # noqa: E402
from lifecycle_metrics.providers.baidu import geocode_wgs84              # noqa: E402
from lifecycle_metrics.schema import Dataset                             # noqa: E402

APP_VERSION = "0.1.0"
_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
_gen_lock = threading.Lock()

app = FastAPI(title="社区生活圈智能体检与规划助手", version=APP_VERSION)


def _atomic_png(path: Path, render) -> None:
    """先写临时文件再原子替换，返回时保证 path 是一张完整的图。

    为什么需要：GL 底图在缩放/重建时会**并发**请求同一张叠加图（`/api/overlay.png`、
    `/api/isochrone.png`）。两个请求同时发现缓存不存在、同时开始写同一个文件，
    先返回的那个就会把"写了一半的图"连同它当时的大小当 Content-Length 发出去 ——
    浏览器报 ERR_CONTENT_LENGTH_MISMATCH，服务端抛 "Response content shorter than
    Content-Length"。加锁 + rename 之后，并发请求要么等，要么拿到完整的旧图。
    """
    if path.exists():
        return
    with _gen_lock:
        if path.exists():
            return
        tmp = path.with_name(path.name + ".tmp")
        render(tmp)
        tmp.replace(path)


def _elements_cfg(community: Dict[str, Any]) -> Dict[str, Any]:
    return load_elements(repo_path(community["elements_config"]))


def _after_diagnosis(ds, cfg: Dict[str, Any], opt: Dict[str, Any], graph, grid,
                     cutoff: float, speed: float) -> Optional[Dict[str, Any]]:
    """把推荐点位加成设施后重算一遍体检 —— 供前端展示"优化后"的覆盖率与覆盖图。"""
    sites = opt.get("sites") or []
    if not sites:
        return None
    from lifecycle_metrics.optimize import apply_sites
    ds2 = apply_sites(ds, sites, opt["element"])
    access2 = compute_access(ds2, graph, cutoff, speed)
    access5_2 = compute_access(ds2, graph, cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    diag2 = diagnose(ds2, cfg, graph=graph, access=access2, access_5min=access5_2, grid=grid)
    return strip_runtime(diag2)


def _build(community_id: str, k: int) -> Dict[str, Any]:
    key = (community_id, k)
    if key in _cache:
        return _cache[key]

    communities = load_communities()
    community = resolve_community(communities, community_id)
    cfg = _elements_cfg(community)
    ds, build_info = assemble_dataset(community, cfg)

    graph = RoadGraph.from_dataset(ds)
    cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cutoff, speed)
    access5 = compute_access(ds, graph, cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    diag = diagnose(ds, cfg, graph=graph, access=access, access_5min=access5, grid=grid)
    opt = optimize_sites(ds, cfg, k=k, graph=graph, access=access)
    from lifecycle_metrics.profile import enrich_optimization
    enrich_optimization(ds, graph, cfg, opt, walk_speed=speed)     # 点位画像（建设形态/受益面/容量核对）

    circle = circle_polygon(ds, grid, cutoff)
    circle_geojson = None
    if circle is not None:
        try:
            from shapely.geometry import mapping
            circle_geojson = mapping(circle.simplify(0.00012))
        except Exception:  # noqa: BLE001
            circle_geojson = None

    payload = {
        "community": community,
        "elements_config": cfg,
        "dataset": ds.to_dict(),
        "diag": strip_runtime(diag),
        "after_diag": _after_diagnosis(ds, cfg, opt, graph, grid, cutoff, speed),
        "optimize": opt,
        "circle_geojson": circle_geojson,
        "household_provenance": {
            "overrides": build_info.get("household_overrides"),
            "calibration": build_info.get("population_calibration"),
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[key] = payload
    return payload


BASEMAP_DIR = repo_path("data") / "cache"


def _basemap_png(community: Dict[str, Any], zoom: int, width: int, height: int) -> Path:
    """服务端拉取百度静态图并缓存（实现见 lifecycle_metrics/basemap.py）。

    存在的意义：浏览器端 JSAPI 可能被扩展/代理/网络策略拦掉（实测真实浏览器出现过
    "脚本加载失败"），而静态图走服务端 AK，只要后端能出网就能给 Demo 一张真底图。
    """
    from lifecycle_metrics.basemap import fetch_basemap
    try:
        return fetch_basemap(community, zoom=zoom, size=max(width, height),
                             cache_dir=BASEMAP_DIR)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/basemap.png")
def basemap_png(community: Optional[str] = None, zoom: int = 16, size: int = 1024) -> FileResponse:
    cid = community or load_communities().get("active")
    community = resolve_community(load_communities(), cid)
    path = _basemap_png(community, zoom, size, size)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/basemap.json")
def basemap_meta(community: Optional[str] = None, zoom: int = 16, size: int = 1024) -> Dict[str, Any]:
    """静态底图的地理参考：中心（WGS-84）+ 每像素米数，前端据此对齐叠加层。"""
    from lifecycle_metrics.basemap import basemap_meta as _meta
    cid = community or load_communities().get("active")
    return _meta(resolve_community(load_communities(), cid), zoom=zoom, size=size)


@app.get("/api/geocode/search")
def geocode_search(q: str = Query(..., min_length=1), city: str = "上海市",
                   limit: int = Query(8, ge=1, le=20)) -> Dict[str, Any]:
    """模糊搜索街道/地点：给"新增社区"输入框用，返回候选列表供用户挑选。

    与地理编码的区别：地理编码要求精确地址，"曹杨新村街道"这类模糊词经常定位失败；
    地点检索本身就是模糊匹配，更适合搜索框交互。
    """
    from lifecycle_metrics.providers.baidu import geocode_wgs84, search_places
    query = q.strip()
    try:
        results = search_places(query, city=city, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"地点检索失败：{exc}") from exc
    if len(results) < 2 and len(query) >= 6:          # 用户可能直接输了完整地址
        try:
            lon, lat, meta = geocode_wgs84(query, city=city)
            results.append({"name": query, "address": "（按完整地址地理编码）",
                            "lon": lon, "lat": lat, "bd09": meta.get("bd09"),
                            "looks_like_subdistrict": True, "is_government": True,
                            "tag": "geocoding", "type": meta.get("level") or ""})
        except Exception:  # noqa: BLE001
            pass
    return {"query": query, "city": city, "count": len(results), "results": results}


@app.post("/api/probe")
async def probe_sink(payload: Dict[str, Any]) -> Dict[str, Any]:
    """接收浏览器端实测结果（如 JSAPI 分辨率探测），落盘到 data/cache/probe-*.json。

    存在的意义：headless 浏览器抓 DOM 不稳定，让页面自己把结果回传最可靠。
    """
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = repo_path("data") / "cache" / f"probe-{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "saved": str(path.relative_to(ROOT))}


@app.get("/api/config")
def app_config() -> Dict[str, Any]:
    """下发前端配置。浏览器端 AK 本来就会出现在页面源码里，这里只是省去手工粘贴。"""
    from lifecycle_metrics.env import baidu_credentials, load_env_file
    load_env_file()
    creds = baidu_credentials()
    personas = load_personas()
    return {
        "baidu_web_ak": creds["web_ak"],
        "server_ak_configured": bool(creds["ak"]),
        "server_sk_configured": bool(creds["sk"]),
        "personas": personas.get("personas", []),
        "persona_notes": {k: v for k, v in personas.items() if k != "personas"},
        "note": "服务端密钥只留在后端环境变量中，绝不下发到浏览器。",
    }


@app.post("/api/communities")
def create_community(payload: Dict[str, Any], background: "BackgroundTasks") -> Dict[str, Any]:
    """自选社区：地址（或经纬度）→ 拉取真实数据 → 直接可用于分析。

    耗时长（Overpass + 百度 POI，20–60 秒），前端需要展示进度。
    用同步 def，FastAPI 会把它放到线程池，不阻塞事件循环。
    """
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写社区名称")
    address = (payload.get("address") or "").strip()
    lon, lat = payload.get("lon"), payload.get("lat")
    half_width = int(payload.get("half_width") or 800)
    half_width = max(300, min(3000, half_width))
    provider = payload.get("provider") or "baidu"

    geo_meta: Dict[str, Any] = {"source": "user"}
    if lon is None or lat is None:
        if not address:
            raise HTTPException(status_code=400, detail="请填写地址，或直接给出经纬度")
        try:
            lon, lat, geo_meta = geocode_wgs84(address)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"地理编码失败：{exc}") from exc

    cfg_all = load_communities()
    # 重复添加同一个社区：不要再建一份，直接切到已有的那份。
    # 判据是"同名 + 中心点很近"（同一地点两次搜索常给出相差几十到几百米的候选点，
    # 早期每次都新建，于是下拉框里冒出两个同名社区，见 docs/metrics.md）。
    for exist in cfg_all["communities"]:
        if str(exist.get("name", "")).strip() != name:
            continue
        c0 = exist.get("center") or {}
        if c0.get("lon") is None:
            continue
        if haversine_m(float(lon), float(lat), float(c0["lon"]), float(c0["lat"])) <= 500:
            near_m = round(haversine_m(float(lon), float(lat),
                                       float(c0["lon"]), float(c0["lat"])))
            return {"id": exist["id"], "name": exist["name"], "reused": True,
                    "distance_m": near_m,
                    "detail": f"该社区已存在（中心点相距 {near_m} m），"
                              f"已直接切换到 {exist['id']}，未重复建立数据集。"}

    cid = slugify_community(name, [c["id"] for c in cfg_all["communities"]])
    entry: Dict[str, Any] = {
        "id": cid, "name": name,
        "district": geo_meta.get("district") or "",
        "center": {"lon": round(float(lon), 6), "lat": round(float(lat), 6)},
        "center_note": f"WGS-84；来源={geo_meta.get('source')}"
                       + (f"；地理编码置信度={geo_meta.get('confidence')}" if geo_meta.get("confidence") else ""),
        "extent_m": {"half_width": half_width, "half_height": half_width},
        "provider": provider,
        "dataset": f"data/{cid}.dataset.json",
        "elements_config": "config/elements.json",
        "cutoff_element": "circle_15min",
        "data_disclaimer": "自选社区：POI 来自百度地图开放平台，步行路网与建筑来自 OpenStreetMap；"
                           "户数为估算/校准值，仅用于赛题演示。",
    }
    pop, area = payload.get("population"), payload.get("area_km2")
    if pop and area:
        entry["population_calibration"] = {
            "density_per_km2": round(float(pop) / float(area), 1),
            "basis": f"常住人口 {float(pop):,.0f} 人 ÷ 面积 {float(area)} km²，再乘分析范围 "
                     f"{(2 * half_width / 1000) ** 2:.2f} km²",
            "sources": ["用户提供的常住人口与面积（建议注明七普或街道年报）"],
            "caveat": "分析范围是方框，可能包含行政边界外的区域；总量为密度推算近似值。",
        }

    cfg_all["communities"].append(entry)
    save_communities(cfg_all)

    try:
        elements_cfg = _elements_cfg(entry)
        ds, build_info = assemble_dataset(entry, elements_cfg, rebuild=True)
    except Exception as exc:  # noqa: BLE001
        # 建数据失败必须回滚社区条目：否则会在下拉框里留下一个"半成品社区"
        # （没有数据集、点进去要重新联网建，之前踩过：程家桥街道的残留条目）
        cfg_now = load_communities()
        cfg_now["communities"] = [c for c in cfg_now["communities"] if c["id"] != cid]
        if cfg_now.get("active") == cid:
            cfg_now["active"] = cfg_now["communities"][0]["id"]
        save_communities(cfg_now)
        _cache.pop((cid, 0), None)
        _cache.pop((cid, 2), None)
        raise HTTPException(
            status_code=502,
            detail=f"数据构建失败（需要外网访问 Overpass 与百度），已回滚社区「{name}」：{exc}") from exc

    _cache.pop((cid, 0), None)
    _cache.pop((cid, 2), None)
    # 数据建好后顺手把报告也算出来（后台线程），用户点「查看完整报告」时就能秒开
    background.add_task(_safe_ensure_report, cid)
    conf = geo_meta.get("confidence")
    warning = None
    if isinstance(conf, (int, float)) and conf < 50:
        warning = (f"地理编码置信度偏低（{conf}），中心点可能是街道质心而非你期望的位置；"
                   f"建议在地址里带上更具体的门牌/地标，或用 --lon/--lat 直接给坐标。")
    return {"ok": True, "community": entry, "build": build_info,
            "households": ds.total_households(),
            "nodes": len(ds.nodes), "facilities": len(ds.facilities),
            "residential": len(ds.residential),
            "geocode": {k: geo_meta.get(k) for k in ("confidence", "level", "precise", "source")},
            "warning": warning}


@app.delete("/api/communities/{community_id}")
def delete_community(community_id: str) -> Dict[str, Any]:
    """删除自选社区（不动内置的两个示例社区与已生成的数据文件）。"""
    cfg_all = load_communities()
    before = len(cfg_all["communities"])
    cfg_all["communities"] = [c for c in cfg_all["communities"] if c["id"] != community_id]
    if len(cfg_all["communities"]) == before:
        raise HTTPException(status_code=404, detail=f"没有社区 {community_id}")
    if cfg_all.get("active") == community_id:
        cfg_all["active"] = cfg_all["communities"][0]["id"]
    save_communities(cfg_all)
    _cache.pop((community_id, 0), None)
    _cache.pop((community_id, 2), None)
    return {"ok": True, "removed": community_id, "active": cfg_all["active"]}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "version": APP_VERSION, "cached": sorted(k[0] for k in _cache)}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """浏览器默认会来要站点图标。给一个 204，免得控制台里长期挂着一条 404。"""
    return Response(status_code=204)


@app.get("/api/communities")
def communities() -> Dict[str, Any]:
    cfg = load_communities()
    rows = []
    for c in cfg["communities"]:
        # ready = 现在就能出结果：要么数据集已经在本地，要么是离线合成社区（无需网络与 AK）。
        # 仓库不带真实数据（第三方 POI 的再分发问题），所以克隆下来只有合成社区是 ready 的，
        # 其余要自己配 AK 生成 —— 前端据此把没数据的社区标出来，避免一进来就 5 个报错。
        ds_path = repo_path(c["dataset"]) if c.get("dataset") else None
        local_ds = bool(ds_path and ds_path.exists())
        offline = (c.get("provider") or "").lower() == "synthetic"
        rows.append({k: c.get(k) for k in ("id", "name", "district", "provider",
                                           "data_disclaimer", "population_calibration", "center")}
                    | {"custom": str(c.get("id", "")).startswith("custom-"),
                       "ready": local_ds or offline,
                       "local_dataset": local_ds,
                       "has_dataset": (repo_path("out") / c["id"] / "diagnose.json").exists()})
    return {"active": cfg.get("active"), "communities": rows}


@app.get("/api/bundle")
def bundle(community: Optional[str] = None, k: int = Query(2, ge=0, le=8)) -> JSONResponse:
    try:
        payload = _build(community or load_communities().get("active"), k)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(payload)


@app.get("/api/diagnose")
def diagnose_only(community: Optional[str] = None) -> JSONResponse:
    payload = _build(community or load_communities().get("active"), 0)
    return JSONResponse({"diag": payload["diag"], "community": payload["community"]})


@app.get("/api/optimize")
def optimize_only(community: Optional[str] = None, k: int = Query(2, ge=1, le=8),
                  element: Optional[str] = None) -> JSONResponse:
    payload = _build(community or load_communities().get("active"), 0)
    cfg = payload["elements_config"]
    # 直接用 bundle 里已经装配好的数据（含户数覆盖/校准），避免再次落盘或重复构建
    ds = Dataset.from_dict(payload["dataset"])
    graph = RoadGraph.from_dataset(ds)
    cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cutoff, speed)
    opt = optimize_sites(ds, cfg, element=element, k=k, graph=graph, access=access)
    from lifecycle_metrics.profile import enrich_optimization
    enrich_optimization(ds, graph, cfg, opt, walk_speed=speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    opt["after_diag"] = _after_diagnosis(ds, cfg, opt, graph, grid, cutoff, speed)
    return JSONResponse(opt)


@app.get("/api/route")
def day_route(community: Optional[str] = None, point: str = Query(..., min_length=1),
              mode: str = "before", order: str = "day", k: int = Query(2, ge=1, le=8),
              elements: Optional[str] = None, stay: Optional[str] = None,
              include_optional: bool = False, start: str = "07:30",
              persona: Optional[str] = None, speed: Optional[float] = None) -> JSONResponse:
    """点击某个居住组团 → "一天动线"（沿步行路网，逐段给时间）。

    - persona=elder_active…：用典型住户画像（出行链/停留时长/开放时段/步速）驱动动线；
      不传则走"全要素"通用动线
    - mode=before：现状设施；mode=after：把选址优化推荐的 P1…Pk 当成已建成（需 k）
    - order=day：按出行链顺序；order=nearest：纯就近串点
    - elements=market,canteen…：只走这几项（默认取画像出行链，无画像时取全部必需要素）
    - stay=market:15,canteen:40：覆盖停留时长；speed=0.9：覆盖步速（界面滑杆用）
    """
    from lifecycle_metrics.route import plan_day_route
    from lifecycle_metrics.optimize import apply_sites

    want_after = mode == "after"
    payload = _build(community or load_communities().get("active"), k if want_after else 0)
    cfg = payload["elements_config"]
    ds = Dataset.from_dict(payload["dataset"])
    if want_after:
        sites = (payload.get("optimize") or {}).get("sites") or []
        if sites:
            ds = apply_sites(ds, sites, payload["optimize"]["element"])
    graph = RoadGraph.from_dataset(ds)

    try:
        p_cfg = persona_by_key(load_personas(), persona)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    elem_list = [x.strip() for x in (elements or "").split(",") if x.strip()] or None
    stay_map: Dict[str, float] = {}
    for chunk in (stay or "").split(","):
        if ":" in chunk:
            key, _, val = chunk.partition(":")
            try:
                stay_map[key.strip()] = float(val)
            except ValueError:
                continue
    try:
        route = plan_day_route(ds, graph, point, cfg, elements=elem_list, stay_min=stay_map,
                               order=order, include_optional=include_optional, start_clock=start,
                               persona=p_cfg, walk_speed=speed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    route["mode"] = "after" if want_after else "before"
    route["mode_label"] = (f"优化后（含 P1–P{len((payload.get('optimize') or {}).get('sites') or [])}）"
                           if want_after else "现状")
    route["community"] = payload["community"]["id"]
    return JSONResponse(route)


@app.get("/api/isochrone")
def isochrone(community: Optional[str] = None, point: str = Query(..., min_length=1),
              speed: Optional[float] = None) -> JSONResponse:
    """以某个居住组团为圆心的步行等时圈（5/10/15 分钟"风圈"）+ 可达面积比 + 方向盲区。"""
    from lifecycle_metrics.isochrone import focus_isochrone

    payload = _build(community or load_communities().get("active"), 0)
    cfg = payload["elements_config"]
    ds = Dataset.from_dict(payload["dataset"])
    graph = RoadGraph.from_dataset(ds)
    try:
        iso = focus_isochrone(ds, graph, cfg, point, walk_speed=speed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    iso["community"] = payload["community"]["id"]
    return JSONResponse(iso)


@app.get("/api/isochrone.png")
def isochrone_png(community: Optional[str] = None, point: str = Query(..., min_length=1),
                  size: int = Query(1600, ge=256, le=2048)) -> FileResponse:
    """等时圈"风圈"的透明叠加图（给 JSAPI GL 底图当 GroundOverlay）。"""
    from lifecycle_metrics.isochrone import focus_isochrone
    from lifecycle_metrics.raster import render_focus_overlay_png

    payload = _build(community or load_communities().get("active"), 0)
    cid = payload["community"]["id"]
    out = repo_path("data") / "cache" / f"iso-{cid}-{point}-{size}.png"

    def _render(tmp: Path) -> None:
        cfg = payload["elements_config"]
        ds = Dataset.from_dict(payload["dataset"])
        graph = RoadGraph.from_dataset(ds)
        iso = focus_isochrone(ds, graph, cfg, point)
        render_focus_overlay_png(ds, iso, tmp, size=size)

    _atomic_png(out, _render)
    return FileResponse(out, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=600"})


@app.get("/api/isochrone.json")
def isochrone_meta(community: Optional[str] = None, point: str = Query(..., min_length=1),
                   size: int = Query(1600, ge=256, le=2048)) -> Dict[str, Any]:
    """等时圈叠加图的地理范围（贴图用）。"""
    from lifecycle_metrics.raster import focus_overlay_bounds

    payload = _build(community or load_communities().get("active"), 0)
    ds = Dataset.from_dict(payload["dataset"])
    cid = payload["community"]["id"]
    return {"ok": True, "community": cid, "point": point,
            "bounds_wgs84": focus_overlay_bounds(ds),
            "image_url": f"/api/isochrone.png?community={cid}&point={point}&size={size}",
            "note": "透明 PNG，按分析范围贴到 JSAPI 底图上；口径与体检底图的等时圈一致。"}


@app.get("/api/overlay.json")
def overlay_meta(community: Optional[str] = None, raster: str = "cover",
                 mode: str = "before", k: int = Query(2, ge=1, le=8)) -> Dict[str, Any]:
    """覆盖色块的透明 PNG 的地理参考（给 JSAPI 底图当 GroundOverlay 用）。

    GL 地图上没法直接铺上千个格子（会卡），但用户期望看到和「体检底图」一样的覆盖色块，
    所以服务端把同一份栅格渲成一张透明 PNG，前端按 bounds 贴上去，口径完全一致。
    mode=after 时用"含推荐点位"的体检结果（与 canvas 模式的现状/优化后切换对齐）。
    """
    from lifecycle_metrics.raster import (COVER_BANDS, ISOCHRONE_BANDS,  # noqa: F401
                                          overlay_bounds, render_overlay_png)
    want_after = mode == "after"
    payload = _build(community or load_communities().get("active"), k if want_after else 0)
    diag = payload["diag"]
    if want_after and payload.get("after_diag"):
        diag = payload["after_diag"]
    ds = Dataset.from_dict(payload["dataset"])
    cid = payload["community"]["id"]
    tag = f"{raster}-{mode}" if want_after else raster
    out = repo_path("data") / "cache" / f"overlay-{cid}-{tag}.png"
    _atomic_png(out, lambda tmp: render_overlay_png(diag, ds, tmp, raster=raster))
    from lifecycle_metrics.raster import overlay_bounds
    bands = (ISOCHRONE_BANDS if raster == "isochrone" else COVER_BANDS)
    return {"ok": True, "community": cid, "mode": "after" if want_after else "before",
            "raster": "isochrone" if raster == "isochrone" else "cover",
            "size": out.stat().st_size,
            "bounds_wgs84": overlay_bounds(diag),
            "legend": [{"label": b[2], "color": "#%02x%02x%02x" % b[1][:3]} for b in bands],
            "image_url": f"/api/overlay.png?community={cid}&raster={raster}&mode={mode}&k={k}",
            "note": "透明 PNG，按分析范围贴合到地图上；色块口径与体检底图完全一致。"}


@app.get("/api/overlay.png")
def overlay_png(community: Optional[str] = None, raster: str = "cover",
                mode: str = "before", k: int = Query(2, ge=1, le=8)) -> FileResponse:
    meta = overlay_meta(community, raster, mode, k)
    tag = f"{raster}-after" if meta["mode"] == "after" else raster
    path = repo_path("data") / "cache" / f"overlay-{meta['community']}-{tag}.png"
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/route.png")
def day_route_png(community: Optional[str] = None, point: str = Query(..., min_length=1),
                  mode: str = "before", order: str = "day",
                  k: int = Query(2, ge=1, le=8), elements: Optional[str] = None,
                  stay: Optional[str] = None, start: str = "07:30"):
    """把「一天动线」渲染成 PNG（等时圈底图 + 折线 + 序号停靠点），供导出与报告配图。

    与 /api/route 同一套口径（现状/优化后、动线序/最短路序、情景假设都可传）。
    """
    import tempfile

    from fastapi.responses import Response

    from lifecycle_metrics.optimize import apply_sites
    from lifecycle_metrics.raster import render_route_png
    from lifecycle_metrics.route import plan_day_route

    want_after = mode == "after"
    payload = _build(community or load_communities().get("active"), k if want_after else 0)
    cfg = payload["elements_config"]
    ds = Dataset.from_dict(payload["dataset"])
    if want_after:
        sites = (payload.get("optimize") or {}).get("sites") or []
        if sites:
            ds = apply_sites(ds, sites, payload["optimize"]["element"])
    graph = RoadGraph.from_dataset(ds)
    elem_list = [x.strip() for x in (elements or "").split(",") if x.strip()] or None
    stay_map: Dict[str, float] = {}
    for chunk in (stay or "").split(","):
        if ":" in chunk:
            key, _, val = chunk.partition(":")
            try:
                stay_map[key.strip()] = float(val)
            except ValueError:
                continue
    try:
        route = plan_day_route(ds, graph, point, cfg, elements=elem_list, stay_min=stay_map,
                               order=order, start_clock=start)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    route["mode"] = "after" if want_after else "before"
    speed = cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cfg["cutoffs"]["circle_15min"]["time_s"], speed)
    diag = diagnose(ds, cfg, graph=graph, access=access)
    tmp = Path(tempfile.gettempdir()) / f"route-{payload['community']['id']}-{point}.png"
    render_route_png(ds, diag, route, tmp)
    name = f"day-route-{payload['community']['id']}-{point}-{mode}.png"
    return Response(tmp.read_bytes(), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/api/report")
def report_file(community: Optional[str] = None, fmt: str = "html"):
    """报告入口：没有就现生成（自选社区建完后还没跑过 build_demo）。

    HTML 必须走静态目录（/out/<cid>/report.html）而不是直接返回文件内容——
    否则报告里的相对路径图片（map_overview.png）会被解析成 /api/map_overview.png 而 404。
    """
    cid = community or load_communities().get("active")
    try:
        resolve_community(load_communities(), cid)
    except KeyError as exc:
        return _report_error(f"没有社区 {cid}", str(exc))
    try:
        html = _ensure_report(cid)
    except Exception as exc:                                   # noqa: BLE001
        return _report_error(f"{cid} 的报告生成失败", f"{type(exc).__name__}: {exc}")
    if fmt == "html":
        return RedirectResponse(f"/out/{cid}/report.html", status_code=307)
    return FileResponse(html.parent / "report.md", media_type="text/markdown; charset=utf-8")


def _report_error(title: str, detail: str):
    """报告不可用时给一张人话页面 —— 浏览器里直接看到 JSON 的 {"detail":"Not Found"} 太劝退。"""
    from fastapi.responses import HTMLResponse
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>报告暂不可用</title><style>
body{{font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;
     display:flex;align-items:center;justify-content:center;height:100vh;background:#f7f8fa;color:#1f2430}}
.card{{max-width:640px;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:26px 30px}}
h1{{font-size:20px;margin:0 0 10px}} code{{background:#f2f5f9;padding:2px 6px;border-radius:4px}}
pre{{background:#f7f8fa;border:1px solid #e5e7eb;border-radius:8px;padding:10px;overflow:auto}}
a{{color:#1f77b4}}</style></head><body><div class="card">
<h1>报告暂不可用</h1><p>{title}</p><pre>{detail}</pre>
<p>常见原因：该社区的数据集还没生成（自选社区需要先「② 开始分析」拉取数据），
或数据文件被删除。也可以在命令行重跑：<br>
<code>python tools/build_demo.py &lt;community_id&gt;</code></p>
<p><a href="/">← 返回体检首页</a></p></div></body></html>"""
    return HTMLResponse(body, status_code=500)


def _safe_ensure_report(cid: str) -> None:
    """后台生成报告：失败只记日志，不影响创建社区的主流程。"""
    try:
        _ensure_report(cid)
        print(f"[report] {cid} 已生成 out/{cid}/report.html")
    except Exception as exc:                                   # noqa: BLE001
        print(f"[report] {cid} 生成失败：{type(exc).__name__}: {exc}")


def _ensure_report(cid: str) -> Path:
    """确保 out/<cid>/report.html 存在；缺就按与 tools/build_demo.py 相同的口径现算一份。

    自选社区建完数据后并不会自动出报告，用户点「查看完整报告」时就撞 404 —— 这里补上。
    """
    out_dir = repo_path("out") / cid
    html = out_dir / "report.html"
    if html.exists():
        return html
    from lifecycle_metrics.report import build_report
    payload = _build(cid, 2)
    community = payload["community"]
    cfg = payload["elements_config"]
    ds = Dataset.from_dict(payload["dataset"])
    graph = RoadGraph.from_dataset(ds)
    cutoff = cfg["cutoffs"]["circle_15min"]["time_s"]
    speed = cfg.get("walk_speed_mps", 1.25)
    access = compute_access(ds, graph, cutoff, speed)
    access5 = compute_access(ds, graph, cfg["cutoffs"]["circle_5min"]["time_s"], speed)
    grid = build_circle_grid(ds, graph, cutoff, cell_m=40.0, walk_speed=speed)
    diag = diagnose(ds, cfg, graph=graph, access=access, access_5min=access5, grid=grid)
    opt = payload["optimize"]
    after_diag = None
    if opt.get("sites"):
        from lifecycle_metrics.optimize import apply_sites
        ds2 = apply_sites(ds, opt["sites"], opt["element"])
        a2 = compute_access(ds2, graph, cutoff, speed)
        a5_2 = compute_access(ds2, graph, cfg["cutoffs"]["circle_5min"]["time_s"], speed)
        after_diag = diagnose(ds2, cfg, graph=graph, access=a2, access_5min=a5_2, grid=grid)
    build_report(ds, diag, cfg, out_dir, opt=opt, run_optimize=False, after_diag=after_diag)
    return html


# 静态资源：报告目录（地图 PNG）+ 前端
out_root = repo_path("out")
out_root.mkdir(parents=True, exist_ok=True)
app.mount("/out", StaticFiles(directory=str(out_root)), name="out")
app.mount("/", StaticFiles(directory=str(repo_path("web")), html=True), name="web")


def _open_when_ready(url: str, host: str, port: int, timeout_s: float = 60.0) -> None:
    """在后台等端口真的能连上，再打开浏览器。

    为什么不直接 sleep 几秒：冷启动要建缓存，耗时从 2 秒到 30 秒都可能；
    固定 sleep 要么开早了看到"无法连接"，要么白等。这里直接探端口，好了就开。
    """
    import socket
    import webbrowser

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                time.sleep(0.4)                      # 让首页静态资源先就位
                print(f"[open] 已打开浏览器 {url}")
                webbrowser.open(url)
                return
        time.sleep(0.3)
    print(f"[open] 等端口超时，请手动打开 {url}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="启动社区生活圈体检 Demo")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--warm", action="store_true", help="启动前预计算默认社区")
    ap.add_argument("--open", action="store_true", help="就绪后自动打开浏览器")
    args = ap.parse_args(argv)

    if args.warm:
        t0 = time.time()
        _build(load_communities().get("active"), 2)
        print(f"[warm] 预计算完成 {time.time() - t0:.2f}s")

    url = f"http://{args.host}:{args.port}"
    if args.open:
        threading.Thread(target=_open_when_ready, args=(url, args.host, args.port),
                         daemon=True).start()

    import uvicorn
    print(f"→ {url}  （Ctrl+C 停止）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
