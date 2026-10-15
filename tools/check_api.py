"""端到端 API 自检：服务起来后逐条验证关键端点。

    python tools/check_api.py [base_url]
"""
from __future__ import annotations

import json
import math
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# Windows 管道/重定向的默认编码是 GBK，而报告口径里含 "km²"（U+00B2）—— GBK 编码不了它。
# 不在最后一行才炸的话，自检结果已经跑完却以非零码结束，看起来像"自检失败"。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

fails: list[str] = []
checks: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    checks.append((name, cond, detail))
    if not cond:
        fails.append(name)


r = requests.get(f"{BASE}/api/health", timeout=30)
check("GET /api/health", r.status_code == 200, str(r.status_code))

r = requests.get(f"{BASE}/api/communities", timeout=30)
cj = r.json()
check("GET /api/communities", r.status_code == 200 and cj.get("communities"),
      f"{len(cj.get('communities', []))} 个社区")

r = requests.get(f"{BASE}/api/bundle", timeout=180)
b = r.json()
check("GET /api/bundle", r.status_code == 200 and "diag" in b and "dataset" in b,
      f"{len(r.content)} bytes")
diag = b.get("diag", {})
ds = b.get("dataset", {})
opt = b.get("optimize") or {}
check("bundle.dataset 非空", bool(ds.get("nodes") and ds.get("edges") and ds.get("residential")),
      f"{len(ds.get('nodes', []))} 节点 / {len(ds.get('edges', []))} 边 / "
      f"{len(ds.get('residential', []))} 组团 / {len(ds.get('facilities', []))} 设施")
check("bundle.diag 六维指标", len(diag.get("dimensions", {})) == 6,
      json.dumps({k: v["score"] for k, v in diag.get("dimensions", {}).items()}, ensure_ascii=False))
check("bundle.diag.grid_counts 已产出", bool((diag.get("grid_counts") or {}).get("counts")),
      f"required_total={diag.get('grid_counts', {}).get('required_total')}")
gm = diag.get("grid_meta") or {}
check("bundle.diag.grid_meta 可序列化", all(k in gm for k in ("lon0", "lat0", "cell_m", "ncols", "nrows")),
      json.dumps(gm))
check("bundle.diag 不含运行时对象", "grid" not in diag and "access" not in diag, "")

# 分析范围口径：居住组团必须全部落在分析范围内。
# 百度地点检索是圆形、半径比方形分析范围大一圈，框外住宅区 POI 一旦被当成分析单元，
# 就会出现"地图上有小区、却没有对应色块"，并系统性拉低覆盖率。OSM 侧一直有这道判定。
_comm = b.get("community") or {}
_ext = _comm.get("extent_m") or {}
if _comm.get("center") and _ext.get("half_width"):
    _c = _comm["center"]
    _hw = _ext["half_width"] / (111320 * math.cos(math.radians(_c["lat"])))
    _hh = _ext["half_height"] / 111320
    _out = [r for r in ds.get("residential", [])
            if not (_c["lon"] - _hw - 1e-6 <= r["lon"] <= _c["lon"] + _hw + 1e-6
                    and _c["lat"] - _hh - 1e-6 <= r["lat"] <= _c["lat"] + _hh + 1e-6)]
    check("居住组团均在分析范围内", not _out,
          f"{len(ds.get('residential', []))} 个组团，框外 {len(_out)} 个"
          + (f"（如 {_out[0]['id']} {_out[0]['name']}）" if _out else "")
          + f"｜框外住宅区 POI 仅用于命名："
            f"{ds.get('meta', {}).get('residential_poi_outside_box', '—')} 个")

check("bundle.optimize 有推荐点位", bool(opt.get("sites")), f"{len(opt.get('sites', []))} 处")
check("bundle.circle_geojson", bool(b.get("circle_geojson")),
      str((b.get("circle_geojson") or {}).get("type")))
check("diag 问题清单非空", len(diag.get("problems", [])) > 0, f"{len(diag.get('problems', []))} 条")

r = requests.get(f"{BASE}/api/optimize?k=3", timeout=180)
o3 = r.json()
check("GET /api/optimize?k=3", r.status_code == 200 and len(o3.get("sites", [])) <= 3,
      f"{len(o3.get('sites', []))} 处, 覆盖率 {o3.get('before', {}).get('coverage_rate')} → "
      f"{o3.get('after', {}).get('coverage_rate')}")

r = requests.get(f"{BASE}/api/report?fmt=html", timeout=60)
check("GET /api/report (html)", r.status_code == 200 and "生活圈体检报告" in r.text,
      f"{len(r.content)} bytes")

# 一天动线：拿第一个居住组团跑一次，并核对"沿路网、逐段可加总、缺口显式给出"
pid = (ds.get("residential") or [{}])[0].get("id")
r = requests.get(f"{BASE}/api/route", params={"point": pid}, timeout=180)
rt = r.json()
stops = rt.get("stops") or []
leg_pts = sum(len(s.get("leg_coords") or []) for s in stops)
check("GET /api/route（一天动线）", r.status_code == 200 and bool(stops),
      f"{rt.get('point', {}).get('name')} · {len(stops)} 处停靠 · 步行 "
      f"{(rt.get('totals') or {}).get('walk_min')} 分钟 · 折线 {leg_pts} 点")
check("动线逐段可加总", abs(sum(s["walk_min"] for s in stops)
                            + ((rt.get("home_leg") or {}).get("walk_min") or 0)
                            - (rt.get("totals") or {}).get("walk_min", 0)) < 0.5,
      f"合计 {rt.get('totals', {}).get('walk_min')} 分钟")
check("动线给出缺口结论", isinstance(rt.get("missing"), list),
      f"{len(rt.get('missing') or [])} 项走不到 · 完整={rt.get('totals', {}).get('complete')}")
r2 = requests.get(f"{BASE}/api/route", params={"point": pid, "mode": "after", "k": 2}, timeout=180)
check("GET /api/route?mode=after（优化后场景）",
      r2.status_code == 200 and r2.json().get("mode") == "after",
      f"{r2.json().get('mode_label')} · 缺口 {len(r2.json().get('missing') or [])} 项")
check("点位画像已生成", bool((opt.get("sites") or [{}])[0].get("profile", {}).get("build")),
      f"{(opt.get('sites') or [{}])[0].get('profile', {}).get('element_name')} · "
      f"受益 {(opt.get('sites') or [{}])[0].get('profile', {}).get('benefit_count')} 组团")

# ---------------- 批次 4：住户画像 / 同心等时圈 / 界面双模式 ----------------
cfg = requests.get(f"{BASE}/api/config", timeout=60).json()
personas = cfg.get("personas") or []
check("GET /api/config 下发住户画像", len(personas) >= 5,
      "、".join(p.get("name", "") for p in personas))
check("画像自带出行链与开放时段",
      all(p.get("chain") and p.get("windows") and p.get("walk_speed_mps") for p in personas),
      f"{len(personas)} 类")

# 同一栋楼、五类住户：步速/出行链/开放时段不同 → 走出来的"一天"必须真的不同
signatures, speeds = set(), set()
for p in personas:
    rp = requests.get(f"{BASE}/api/route",
                      params={"point": pid, "persona": p["key"]}, timeout=180).json()
    t = rp.get("totals") or {}
    signatures.add((t.get("stops"), t.get("start_clock"), t.get("end_clock")))
    speeds.add(rp.get("assumptions", {}).get("walk_speed_mps"))
    check(f"画像动线 {p['key']}（步速生效、要素不出出行链）",
          rp.get("assumptions", {}).get("walk_speed_mps") == p["walk_speed_mps"]
          and {s["element"] for s in rp.get("stops") or []} <= set(p["chain"]),
          f"{t.get('stops')} 站 · 步行 {t.get('walk_min')} 分钟 · "
          f"{t.get('start_clock')}–{t.get('end_clock')} · 缺口 {t.get('missing_count')} · "
          f"服务时段冲突 {t.get('conflict_count')}")
check("不同画像走出不同的天", len(signatures) >= 3, str(sorted(signatures)))
check("画像步速各不相同", len(speeds) >= 4, str(sorted(speeds)))

# 开放时段：把出发时刻推到闭门之后，必须出现"服务时段冲突 + 高/中级改法"
late = requests.get(f"{BASE}/api/route",
                    params={"point": pid, "persona": personas[0]["key"],
                            "start": "13:30"}, timeout=180).json()
check("设施开放时段会被校验（闭门 → 服务时段冲突）", len(late.get("conflicts") or []) >= 1,
      "；".join(f"{c['element_name']}({c['kind']})" for c in late.get("conflicts") or []))
check("服务时段冲突给出可执行建议",
      any(x.get("level") in ("高", "中") for x in late.get("recommendations") or []),
      (late.get("recommendations") or [{}])[0].get("title", ""))

# 同心等时圈（风圈）
iso = requests.get(f"{BASE}/api/isochrone", params={"point": pid}, timeout=180).json()
g = iso.get("grid") or {}
cp = iso.get("compactness") or {}
bands = iso.get("bands") or []
check("GET /api/isochrone（风圈）", len(bands) == 3 and bool(g.get("rows")),
      f"{g.get('ncols')}×{g.get('nrows')} 栅格 · 分带 " + "/".join(f"{b['area_km2']}km²" for b in bands))
check("分带面积递增、半径 = 步速 × 时间",
      all(bands[i]["area_km2"] < bands[i + 1]["area_km2"] for i in range(len(bands) - 1))
      and [b["radius_m"] for b in bands] == [375, 750, 1125],
      str([b["radius_m"] for b in bands]))
check("可达面积比 = 实际可达 ÷ 同半径理想圆", 0 < cp.get("ratio", 0) <= 1
      and abs(cp.get("actual_km2", 0) - bands[-1]["area_km2"]) < 1e-6,
      f"{cp.get('ratio')}（{cp.get('actual_km2')} ÷ {cp.get('ideal_km2')} km²，"
      f"被吃掉 {cp.get('lost_km2')} km²）")
check("16 方向可达半径 + 方向限制来源", len(iso.get("directions") or []) == 16
      and all(d.get("limited_by") in ("network", "extent") for d in iso["directions"])
      # "被切断"只统计被路网限制的方向：贴分析范围边缘的方向由边界截断，不是路网切断
      and not ({d["label"] for d in iso.get("blind_directions") or []}
               & set(iso.get("extent_limited_directions") or [])),
      "被切断：" + ("、".join(f"{d['label']} {d['reach_m']}m"
                            for d in iso.get("blind_directions") or []) or "无")
      + "｜被分析范围截断：" + ("、".join(iso.get("extent_limited_directions") or []) or "无"))
check("栅格编码合法（只有 0/1/2/3）",
      set("".join(g.get("rows") or [])) <= set("0123"), "")
rimg = requests.get(f"{BASE}/api/isochrone.png", params={"point": pid}, timeout=180)
check("GET /api/isochrone.png（GL 叠加图）",
      rimg.status_code == 200 and rimg.content[:8] == b"\x89PNG\r\n\x1a\n",
      f"{len(rimg.content)} bytes")
rj = requests.get(f"{BASE}/api/isochrone.json", params={"point": pid}, timeout=60).json()
check("GET /api/isochrone.json（贴图范围）",
      bool(rj.get("bounds_wgs84", {}).get("sw")) and "isochrone.png" in rj.get("image_url", ""))
ov = requests.get(f"{BASE}/api/overlay.json", params={"raster": "isochrone"}, timeout=180).json()
check("GET /api/overlay.json（并集分带仍可用）",
      ov.get("ok") and bool(ov.get("bounds_wgs84", {}).get("sw")), ov.get("raster", ""))

r = requests.get(f"{BASE}/", timeout=60)
check("GET / (前端)", r.status_code == 200 and "生活圈" in r.text, f"{len(r.content)} bytes")
html = r.text
check("前端有体检/规划双模式与画像选择条",
      "panel-modes" in html and "personaBar" in html and "isoBody" in html, "")
check("一键演示 UI 齐备（入口 / 菜单 / 极简控制条 / 聚光层 / 指向卡）",
      all(k in html for k in ("demoStart", "demoMenu", "demoBar", "demoPause",
                              "demoNext", "demoExit",           # 控制条只留 暂停/下一步/退出
                              "demoSpot", "demoRing", "demoRing2", "demoTag",
                              "demoArrow", "demoCard", "demoHoles", "dh1", "dh2",
                              "daLine", "daHead")), "")
# 顶部条只写步数与按钮，说明文字全部在指向卡里 —— 字幕条已移除
check("演示不再有独立字幕条（描述移到指向卡）", "demoCaption" not in html, "")
r = requests.get(f"{BASE}/app.js", timeout=60)
check("GET /app.js", r.status_code == 200 and "renderCanvas" in r.text, f"{len(r.content)} bytes")
js = r.text
check("前端含风圈与画像逻辑",
      "drawIso" in js and "loadIsochrone" in js and "renderPersonaBar" in js, "")
check("前端含一键演示脚本（步骤表 / 播放 / 跳转 / GL 就绪检测）",
      all(k in js for k in ("DEMO_STEPS", "demoStart", "demoGoto", "demoWaitFor",
                            "demoScrollTo", "navigator.onLine")), "")
check("演示含聚光定位、选边、指向线与逐步自检",
      all(k in js for k in ("demoSpotlight", "demoPlaceCard", "demoDrawArrow", "demoDrawShade",
                            "demoBoxFor", "demoReplayStep", "demoSelfcheck", "demoPreviewStep",
                            "demoHoldFor", "demoMapSurface", "ResizeObserver", "toggleAttribute")), "")
# 蒙版从"四块矩形拼接"改成了"一层 SVG mask 挖洞"：接缝问题从根上消失，
# 而且能同时挖两个洞（第 ⑧⑨⑩ 步要同时高亮按钮/卡片与地图）
check("演示蒙版用单层 SVG mask（不再是四块矩形拼接）",
      "demoHoles" in html and ".demo-mask" not in html and "demoDrawMask" not in js, "")
# SVG 元素没有 hidden IDL 属性：赋值只是挂了个 JS 属性，CSS 的 [hidden] 仍然生效
check("演示层不直接用 el.hidden 开关 SVG（会静默失效）",
      not any(k in js for k in ("$('demoArrow').hidden", "$('daLine').hidden",
                                "$('daHead').hidden")), "")

glon, glat = ds["community"]["center"]["lon"], ds["community"]["center"]["lat"]

print(json.dumps({
    "base": BASE,
    "summary": diag.get("summary"),
    "prescription": {"element": opt.get("element_name"),
                     "before": opt.get("before", {}).get("coverage_rate"),
                     "after": opt.get("after", {}).get("coverage_rate"),
                     "gain_pp": opt.get("gain", {}).get("coverage_rate_pp")},
    "checks": [{"name": n, "ok": c, "detail": d} for n, c, d in checks],
    "passed": len(checks) - len(fails),
    "failed": fails,
}, ensure_ascii=False, indent=1))
sys.exit(1 if fails else 0)
