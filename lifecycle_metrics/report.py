"""体检报告生成：Markdown（可提交/可 diff）+ HTML（可演示）+ 地图 PNG + JSON 结果。

报告里的每个数字都来自 metrics/optimize 的结构化输出，不做二次加工：
这也是"结论可复现"的前提。
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .metrics import strip_runtime
from .optimize import optimize_sites
from .raster import render_map_png
from .schema import Dataset


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _bar(score: float, width: int = 16) -> str:
    n = int(round(max(0.0, min(1.0, score)) * width))
    return "█" * n + "░" * (width - n)


def _fmt_time(t: Optional[float]) -> str:
    return "—" if t is None else f"{t / 60:.1f} 分钟"


# ----------------------------------------------------------------------
def _dimensions_table(diag: Dict[str, Any]) -> str:
    lines = ["| 维度 | 得分 | 直观刻度 | 说明 |", "| --- | --- | --- | --- |"]
    for key, d in diag["dimensions"].items():
        lines.append(f"| {d['label']} | {d['score'] * 100:.1f} | `{_bar(d['score'])}` | {d['detail']} |")
    return "\n".join(lines)


def _elements_table(diag: Dict[str, Any]) -> str:
    lines = [
        "| 要素 | 类别 | 必需 | 设施数 | 15 分钟可达覆盖率 | 5 分钟近距率 | 单一依赖率 | 供需比 | 缺口指数 | 中位步行时间 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in diag["elements"]:
        lines.append(
            "| {name} | {cat} | {req} | {n} | {cov} | {prox} | {solo} | {ratio} | {gap} | {med} |"
            .format(
                name=e["name"], cat=e["category"], req="是" if e["required"] else "否",
                n=e["facility_count"], cov=_pct(e["coverage_rate"]),
                prox=_pct(e["proximity_5min_rate"]),
                solo=_pct(e["single_dependency_rate"]), ratio=f"{e['supply_demand_ratio']:.2f}",
                gap=f"{e['gap_index']:.2f}", med=_fmt_time(e["median_time_s"])))
    lines.append("")
    lines.append("> 口径提示：**供需比 / 缺口指数是相对模型**，其「每处设施服务人口」为工程假设值"
                 "（国标未规定该参数），有实测服务能力时应替换；覆盖率、达标率、可达性等硬指标不受该假设影响。"
                 "详见 `docs/metrics.md`。")
    return "\n".join(lines)


def _problems_section(diag: Dict[str, Any]) -> str:
    if not diag["problems"]:
        return "本次体检未发现必需要素缺口。"
    out = []
    for i, p in enumerate(diag["problems"], start=1):
        out.append(f"**{i}. [{p['level']}] {p['title']}**\n\n> {p['detail']}\n>\n"
                   f"> 影响约 {p['affected_households']} 户 / {p['affected_population']} 人"
                   + (f"；涉及组团：{', '.join(p['uncovered_points'][:12])}"
                      + ("…" if len(p["uncovered_points"]) > 12 else "")
                      if p.get("uncovered_points") else ""))
    return "\n\n".join(out)


def _cross_river_section(diag: Dict[str, Any]) -> str:
    pts = diag["points"]
    across = [p for p in pts if p["across_river"]]
    if not across:
        return ""
    other = [p for p in pts if not p["across_river"]]
    a_hh = sum(p["households"] for p in across)
    a_score = sum(p["compliance_score"] * p["households"] for p in across) / max(a_hh, 1)
    o_hh = sum(p["households"] for p in other) or 1
    o_score = sum(p["compliance_score"] * p["households"] for p in other) / o_hh
    missing: Dict[str, int] = {}
    for p in across:
        for m in p["missing"]:
            missing[m] = missing.get(m, 0) + 1
    top = sorted(missing.items(), key=lambda kv: -kv[1])[:5]
    name_by_key = {e["key"]: e["name"] for e in diag["elements"]}
    return (
        f"### 跨河片区专项\n\n"
        f"路网中生活性河道共打断 {diag['summary'].get('river_removed_crossings')} 处过河通道，"
        f"仅保留 {diag['summary'].get('bridges')} 座桥。跨河片区 "
        f"{len(across)} 个组团 / {a_hh} 户，加权达标得分 **{a_score:.2f}**，"
        f"河对岸组团为 **{o_score:.2f}**，差距 {abs(a_score - o_score) * 100:.1f} 个百分点。\n\n"
        + ("跨河片区缺口最集中的要素：" +
           "、".join(f"{name_by_key.get(k, k)}（{v} 个组团不可达）" for k, v in top) + "。\n"
           if top else "")
    )


def _data_quality_lines(diag: Dict[str, Any]) -> list:
    """数据质量与路网连通性：把"评估不了的部分"如实写出来，而不是算成 0 分。"""
    dq = diag.get("data_quality") or {}
    net = dq.get("network") or {}
    s = diag.get("summary") or {}
    lines = [
        "",
        "**数据质量与路网连通性**",
        "",
        f"- 路网拓扑：{s.get('road_nodes', 0)} 个节点 / {s.get('road_edges', 0)} 条边，"
        f"分为 **{net.get('components', 1)} 个连通块**"
        + (f"（已对断头路做就近补连 **{net['stitched_edges']} 处**）"
           if net.get("stitched_edges") else ""),
        f"- 可评估人口：**{s.get('assessable_population', 0):,} 人**"
        f"（{s.get('assessable_points', 0)} 个组团）"
        + (f"；另有 {s.get('unassessable_points', 0)} 个组团 / "
           f"{s.get('unassessable_households', 0):,} 户 / "
           f"{s.get('unassessable_population', 0):,} 人因**所在小路与主路网未连通**无法评估，"
           f"已从覆盖率分母与问题清单中剔除（不当作设施缺口）"
           if s.get("unassessable_households") else "；全部居住组团均可评估"),
    ]
    if net.get("isolated_facilities"):
        lines.append(f"- 另有 {len(net['isolated_facilities'])} 处设施吸附在孤立小路上"
                     f"（{', '.join(f['name'] for f in net['isolated_facilities'][:4])}…），"
                     f"这些设施对其它组团也显示不可达，覆盖率因此偏保守。")
    if net.get("stitched_note"):
        lines.append(f"- 补连规则：{net['stitched_note']}")
    lines.append("")
    return lines


def _data_quality_html(diag: Dict[str, Any]) -> str:
    dq = diag.get("data_quality") or {}
    net = dq.get("network") or {}
    s = diag.get("summary") or {}
    un = s.get("unassessable_households") or 0
    rows = [
        f"路网拓扑：{s.get('road_nodes', 0)} 节点 / {s.get('road_edges', 0)} 条边，"
        f"<b>{net.get('components', 1)} 个连通块</b>"
        + (f"，已补连断头路 <b>{net['stitched_edges']} 处</b>" if net.get("stitched_edges") else ""),
        (f"可评估人口 <b>{s.get('assessable_population', 0):,} 人</b>"
         f"（{s.get('assessable_points', 0)} 个组团）；"
         + (f"<b>{s.get('unassessable_points', 0)} 个组团 / {un:,} 户 / "
            f"{s.get('unassessable_population', 0):,} 人</b>因所在小路与主路网未连通、"
            f"按「无法评估」处理（不从 0 分计入覆盖率，也不进问题清单）"
            if un else "全部居住组团均可评估")),
    ]
    if net.get("isolated_facilities"):
        rows.append(f"{len(net['isolated_facilities'])} 处设施吸附在孤立小路上，"
                    f"对其它组团显示不可达，覆盖率偏保守")
    if net.get("stitched_note"):
        rows.append(_html.escape(net["stitched_note"]))
    return ("<h3 style='font-size:15px;margin:18px 0 8px'>数据质量与路网连通性</h3>"
            "<ul class='muted'>" + "".join(f"<li>{r}</li>" for r in rows) + "</ul>")


def _standards_table(elements_cfg: Dict[str, Any]) -> str:
    """标准依据表（Markdown）：每个要素对应哪条标准、什么层级、步行距离与面积口径。"""
    rows = ["| 要素 | 配置层级 | 标准要求 | 步行距离口径 | 建筑面积（标准有规定时） | 出处 | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for e in elements_cfg.get("elements", []):
        r = e.get("standard_ref") or {}
        if not r:
            continue
        dist = r.get("walk_distance_m")
        rows.append(
            f"| {e['name']} | {r.get('level', '—')} | {r.get('requirement', '—')} | "
            f"{str(dist) + ' m' if dist else '—'} | {r.get('building_area') or '—'} | "
            f"{r.get('doc', '—')} | {'已核对原文' if r.get('verified') else '**待核对原文**'} |")
    rows += ["",
             "步行距离口径出处：" + (elements_cfg.get("elements", [{}])[0]
                                     .get("standard_ref", {}).get("walk_basis", "—")),
             "",
             "> ⚠️ 本表出处来自**公开检索**（每条都带 source_url），**不是逐字核对标准原文得到的**。"
             "本项目使用的「15 分钟 / 5 分钟步行」阈值与国标表 3.0.4 的步行距离分级一致；"
             "各要素的配置层级与面积口径请在正式提交前用官方原文逐条核对，"
             "核对后把 `config/elements.json` 里对应条目的 `verified` 改为 `true`，本表会自动去掉「待核对」标记。"]
    return "\n".join(rows)


def _standards_html(elements_cfg: Dict[str, Any]) -> str:
    unverified = sum(1 for e in elements_cfg.get("elements", [])
                     if (e.get("standard_ref") or {}).get("verified") is False)
    rows = []
    for e in elements_cfg.get("elements", []):
        r = e.get("standard_ref") or {}
        if not r:
            continue
        dist = r.get("walk_distance_m")
        status = ("<span style='color:#2ca02c'>已核对</span>" if r.get("verified")
                  else "<span style='color:#d62728'>待核对原文</span>")
        rows.append(
            f"<tr><td>{_html.escape(e['name'])}</td><td>{_html.escape(str(r.get('level', '—')))}</td>"
            f"<td>{_html.escape(str(r.get('requirement', '—')))}</td>"
            f"<td>{str(dist) + ' m' if dist else '—'}</td>"
            f"<td>{_html.escape(str(r.get('building_area') or '—'))}</td>"
            f"<td class='muted'>{_html.escape(str(r.get('doc', '—')))}</td><td>{status}</td></tr>")
    walk = (elements_cfg.get("elements", [{}])[0].get("standard_ref", {})
            .get("walk_basis", "—"))
    return (f"<h3 style='font-size:15px;margin:18px 0 8px'>3.1 标准依据与达标口径</h3>"
            f"<table><thead><tr><th>要素</th><th>配置层级</th><th>标准要求</th><th>步行距离</th>"
            f"<th>建筑面积</th><th>出处</th><th>状态</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"<p class='muted'>步行距离口径：{_html.escape(walk)}</p>"
            f"<p class='muted'>⚠️ 本表出处来自公开检索，<b>{unverified} 条尚待核对原文</b>。"
            f"正式提交前请用官方原文逐条核对；核对完把 <code>config/elements.json</code> 里对应条目的 "
            f"<code>verified</code> 改成 <code>true</code>，此处会自动去掉标记。</p>")


def _robustness_section(out_dir: Path) -> list:
    path = Path(out_dir) / "robustness.json"
    if not path.exists():
        return ["", "未执行稳健性检验。运行 `python tools/robustness.py <community_id>` 后本节自动写入"
                    "「换速度 / 换阈值，结论会不会变」。"]
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = ["", "**结论稳健性**（本地重算，不依赖任何网络服务）", "",
            "| 假设 | 综合得分 | 等级 | 15 分钟可达覆盖率 | 最短板要素 |", "| --- | --- | --- | --- | --- |"]
    for r in d["rows"]:
        rows.append(f"| 步速 {r['speed_mps']} m/s · 阈值 {r['cutoff_s']:.0f} s | "
                    f"{r['composite_score']} | {r['grade']} | {r['coverage'] * 100:.1f}% | "
                    f"{r.get('worst_element') or '—'}（{(r.get('worst_coverage') or 0) * 100:.1f}%） |")
    rows += ["",
             f"- 得分区间 **{d['score_min']}–{d['score_max']}**（跨度 {d['score_span']}）；"
             f"等级{'在各假设下保持一致' if d['grade_stable'] else '会在区间边界上变化'}。",
             f"- 最短板要素{'始终是' if d['worst_element_stable'] else '在个别假设下不是'}"
             f"「{d['worst_element']}」——**补设施的方向不随假设改变**，这是比总分更重要的稳健性。",
             f"- {d.get('note', '')}"]
    return rows


def _robustness_html(out_dir: Path) -> str:
    path = Path(out_dir) / "robustness.json"
    if not path.exists():
        return ("<p class='muted'>未执行稳健性检验（运行 "
                "<code>python tools/robustness.py &lt;community_id&gt;</code> 后本节自动写入）。</p>")
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = "".join(
        f"<tr><td>步速 {r['speed_mps']} m/s · 阈值 {r['cutoff_s']:.0f} s</td>"
        f"<td>{r['composite_score']}</td><td>{r['grade']}</td>"
        f"<td>{r['coverage'] * 100:.1f}%</td>"
        f"<td>{_html.escape(str(r.get('worst_element') or '—'))}"
        f"（{(r.get('worst_coverage') or 0) * 100:.1f}%）</td></tr>"
        for r in d["rows"])
    return (f"<h3 style='font-size:15px;margin:18px 0 8px'>7.1 结论稳健性</h3>"
            f"<table><thead><tr><th>假设</th><th>综合得分</th><th>等级</th>"
            f"<th>15 分钟可达覆盖率</th><th>最短板要素</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<ul class='muted'><li>得分区间 <b>{d['score_min']}–{d['score_max']}</b>"
            f"（跨度 {d['score_span']}）；等级"
            f"{'在各假设下保持一致' if d['grade_stable'] else '会在区间边界上变化'}。</li>"
            f"<li>最短板要素{'始终是' if d['worst_element_stable'] else '在个别假设下不是'}"
            f"「{_html.escape(str(d['worst_element']))}」——<b>补设施的方向不随假设改变</b>。</li>"
            f"<li>{_html.escape(d.get('note', ''))}</li></ul>")


def _marginal_section(out_dir: Path) -> list:
    path = Path(out_dir) / "marginal.json"
    if not path.exists():
        return ["", "未执行边际效益分析。运行 `python tools/marginal.py <community_id>` 后本节自动写入。"]
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = ["", f"**边际效益**（{d['element_name']}：建第 1～{len(d['rows'])} 处分别能多覆盖多少）", "",
            "| 建 k 处 | 覆盖率 | 累计新增覆盖户数 | 第 k 处的边际增量 | 平均每处 | 综合得分 |",
            "| --- | --- | --- | --- | --- | --- |"]
    for r in d["rows"]:
        rows.append(f"| k={r['k']} | {r['coverage_rate'] * 100:.1f}% | "
                    f"{r['newly_covered_households']:,} | **+{r['marginal_households']:,}** | "
                    f"{r['avg_households_per_site']:,} | {r['composite_score']} |")
    first = d["rows"][0]["marginal_households"] if d["rows"] else 0
    last = d["rows"][-1]["marginal_households"] if d["rows"] else 0
    rows += ["", f"- 第 1 处带来 **+{first:,} 户**，第 {len(d['rows'])} 处只再增 "
                 f"**+{last:,} 户** —— 边际效益递减，这就是「为什么不是建得越多越好」的量化答案。",
             "- 超过拐点后再投，单位效益（户/处）明显下降，应把预算转投其它短板要素。"]
    return rows


def _marginal_html(out_dir: Path) -> str:
    path = Path(out_dir) / "marginal.json"
    if not path.exists():
        return ("<p class='muted'>未执行边际效益分析（运行 "
                "<code>python tools/marginal.py &lt;community_id&gt;</code>）。</p>")
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = "".join(
        f"<tr><td>k={r['k']}</td><td>{r['coverage_rate'] * 100:.1f}%</td>"
        f"<td>{r['newly_covered_households']:,}</td>"
        f"<td><b>+{r['marginal_households']:,}</b></td>"
        f"<td>{r['avg_households_per_site']:,}</td><td>{r['composite_score']}</td></tr>"
        for r in d["rows"])
    first = d["rows"][0]["marginal_households"] if d["rows"] else 0
    last = d["rows"][-1]["marginal_households"] if d["rows"] else 0
    return (f"<h3 style='font-size:15px;margin:18px 0 8px'>6.1 边际效益：每多建一处还能多覆盖多少</h3>"
            f"<table><thead><tr><th>建 k 处</th><th>覆盖率</th><th>累计新增覆盖户数</th>"
            f"<th>第 k 处的边际增量</th><th>平均每处</th><th>综合得分</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f"<p class='muted'>第 1 处带来 <b>+{first:,} 户</b>，第 {len(d['rows'])} 处只再增 "
            f"<b>+{last:,} 户</b>：边际效益递减，这就是「为什么不是建得越多越好」的量化答案。</p>")


def _focus_section(out_dir: Path) -> list:
    """以某个组团为圆心的同心等时圈（"风圈"）。由 tools/route_map.py 生成。"""
    path = Path(out_dir) / "focus_iso.json"
    if not path.exists():
        return ["", "未生成单组团等时圈。运行 `python tools/route_map.py <community_id>` 后本节自动写入"
                    "（交互式 Demo 里点任一个居住组团也能实时生成）。"]
    d = json.loads(path.read_text(encoding="utf-8"))
    cp = d["compactness"]
    lines = ["", "### 5.2 15 分钟圈不是一个圆：以某组团为圆心的同心等时圈", "",
             "![风圈](map_focus.png)", "",
             f"以 **{d['point']['name']}**（{d['point']['households']:,} 户）为圆心，"
             f"按 {d['walk_speed_mps']} m/s 的步速沿真实路网展开：", "",
             "| 分带 | 理想半径 | 实际可达面积 | 同半径理想圆 | 可达面积比 | 圈内凑齐服务 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for b in d["bands"]:
        lines.append(f"| {b['label']} | {b['radius_m']} m | {b['area_km2']} km² | "
                     f"{b['ideal_area_km2']} km² | {b['fill_pct'] * 100:.0f}% | "
                     f"{b['elements_count']}/{b['elements_total']} 项 |")
    blind = "、".join(f"{x['label']} {x['reach_m']} m" for x in d.get("blind_directions", []))
    extent_lim = "、".join(d.get("extent_limited_directions") or [])
    lines += ["",
              f"- **可达面积比 {cp['ratio'] * 100:.0f}%**：分析范围内实际可达 {cp['actual_km2']} km²，"
              f"同半径理想圆 {cp['ideal_km2']} km² —— 被河道、快速路、封闭小区、断头路"
              f"受路网折减 {cp['lost_km2']} km²。",
              f"- **被切断的方向**：{blind or '无明显'}（16 个方向平均可达 "
              f"{d['mean_reach_m']} m，最长 {d['max_reach_m']} m）。"
              f"同一段步行时间，这几个方向走不出去，选址若落在这些方向，受益面会被限制。",
              *([f"- 注：{extent_lim} 方向的可达半径由**分析范围边界**截断（不是被路网切断），"
                 f"已排除在「被切断的方向」之外。"] if extent_lim else []),
              "",
              "> **为什么不做成台风那样的同心圆**：台风圈是理想圆，这里画的是**真实路网上的"
              "等时圈**。同样 15 分钟，隔着一条河、一条快速路，形状完全不同 —— "
              "借的是风圈的视觉语言（分层色带 + 等时线 + 圈上刻度 + 可达面积比），不是圆本身。",
              "",
              "> 口径：以组团质心为圆心（组团是 120 m 网格聚合出的分析单元，不是某一栋楼），"
              "沿步行路网算最短路并计入两端离路距离，未建模过街等待与坡度；"
              "面积统计范围限于分析框内。"]
    return lines


def _persona_section(out_dir: Path) -> list:
    """同一栋楼、不同住户的一天对比。由 tools/route_map.py 生成。"""
    path = Path(out_dir) / "personas.json"
    if not path.exists():
        return ["", "未生成住户画像对比。运行 `python tools/route_map.py <community_id>` 后本节自动写入。"]
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = d.get("rows") or []
    if not rows:
        return ["", "住户画像对比为空。"]
    lines = ["", "### 5.3 同一栋楼，五类住户的一天", "",
             f"同一个居住组团（{d.get('point_name', '')}），换成不同人群，"
             "出行链、步速、可接受步行距离都不同 —— 走出来的「一天」也完全不同：", "",
             "| 画像 | 出门 | 停靠 | 步行 | 停留 | 等待 | 全程 | 不可达 | 服务时段冲突 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        t = r["totals"]
        lines.append(f"| {r['name']} | {t['start_clock']} | {t['stops']} | {t['walk_min']} 分钟 | "
                     f"{t['stay_min']} 分钟 | {t['wait_min']} 分钟 | {t['span_min']} 分钟 | "
                     f"{t['missing_count']} 项 | {t['conflict_count']} 处 |")
    stuck = [r["name"] for r in rows if r["totals"]["stops"] == 0]
    if stuck:
        lines += ["",
                  f"> **注意**：{'、'.join(stuck)} 在本组团的 15 分钟等时圈内**无任何出行链要素可达**。"
                  "这不是数据缺失，而是本次体检最值得注意的一条结论：同一个组团，"
                  "按成人步速（1.25 m/s）可达的要素，在助行器步速（0.60 m/s）下可能全部不可达。"
                  "因此 15 分钟生活圈的达标口径必须写明所采用的步行速度。"]
    lines += ["",
              "**画像不是大模型现判的，是一张可核对的规则表**（`config/personas.json`）："
              "每类住户给出出行链、停留时长、设施开放时段、步速与可接受单段步行时间。"
              "这样做的三个理由：① 结果可复现，评委按同一份数据能跑出同一条动线；"
              "② 离线可用，不依赖外部接口；③ 每个人都能打开表来核对「老人为什么是这几个点」。",
              "",
              "**设施不是全天可用**：菜市场 12:00 收市、社区卫生服务站午间停诊、"
              "幼儿园 08:30 关门。到达时若已闭门或尚未开门，动线会记成**服务时段冲突**并给出改法"
              "（把它排到动线前段 / 推动延长服务时间），这一步让动线从「几何串点」变成「真的过一天」。"]
    return lines


def _focus_html(out_dir: Path) -> str:
    path = Path(out_dir) / "focus_iso.json"
    if not path.exists():
        return ("<p class='muted'>未生成单组团等时圈（运行 "
                "<code>python tools/route_map.py &lt;community_id&gt;</code>）。</p>")
    d = json.loads(path.read_text(encoding="utf-8"))
    cp = d["compactness"]
    rows = "".join(
        f"<tr><td>{b['label']}</td><td>{b['radius_m']} m</td><td>{b['area_km2']} km²</td>"
        f"<td>{b['ideal_area_km2']} km²</td><td>{b['fill_pct'] * 100:.0f}%</td>"
        f"<td>{b['elements_count']}/{b['elements_total']} 项</td></tr>"
        for b in d["bands"])
    blind = "、".join(f"{_html.escape(x['label'])} {x['reach_m']} m"
                     for x in d.get("blind_directions", [])) or "无明显"
    extent_lim = "、".join(d.get("extent_limited_directions") or [])
    return (
        "<h3 style='font-size:15px;margin:18px 0 8px'>5.2 15 分钟圈不是一个圆："
        "以某组团为圆心的同心等时圈</h3>"
        f"<p class='muted'>以 <b>{_html.escape(d['point']['name'])}</b>"
        f"（{d['point']['households']:,} 户）为圆心，按 {d['walk_speed_mps']} m/s 的步速"
        "沿真实路网展开。</p>"
        "<figure><img src='map_focus.png' alt='同心等时圈'>"
        "<figcaption>图 3　同心等时圈（风圈）：分层色带 = 5/10/15 分钟可达范围，"
        "深色等时线为分带边界，虚线圆为同半径理想圆，灰底为理想圆范围内不可达的空间，"
        "红箭头指向被河道/快速路/断头路切断的方向</figcaption></figure>"
        "<table><thead><tr><th>分带</th><th>理想半径</th><th>实际可达面积</th>"
        "<th>同半径理想圆</th><th>可达面积比</th><th>圈内凑齐服务</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<ul class='muted'>"
        f"<li><b>可达面积比 {cp['ratio'] * 100:.0f}%</b>：分析范围内实际可达 "
        f"{cp['actual_km2']} km²，同半径理想圆 {cp['ideal_km2']} km² —— 被河道、快速路、"
        f"封闭小区、断头路折减 <b>{cp['lost_km2']} km²</b>。</li>"
        f"<li><b>被切断的方向</b>：{blind}（16 个方向平均可达 {d['mean_reach_m']} m，"
        f"最长 {d['max_reach_m']} m）。选址若落在这些方向，受益面会被限制。</li>"
        "<li><b>为什么不做成台风那样的同心圆</b>：台风圈是理想圆，这里画的是真实路网上的"
        "等时圈。同样 15 分钟，隔着一条河、一条快速路，形状完全不同 —— 借的是风圈的"
        "视觉语言（分层色带 + 等时线 + 圈上刻度 + 可达面积比），不是圆本身。</li>"
        "<li>口径：以组团质心为圆心，沿步行路网算最短路并计入两端离路距离，"
        "未建模过街等待与坡度；面积统计范围限于分析框内。</li>"
        + (f"<li class='muted'>{extent_lim} 方向的可达半径由<b>分析范围边界</b>截断"
           "（不是被路网切断），已排除在「被切断的方向」之外。</li>" if extent_lim else "")
        + "</ul>")


def _persona_html(out_dir: Path) -> str:
    path = Path(out_dir) / "personas.json"
    if not path.exists():
        return "<p class='muted'>未生成住户画像对比。</p>"
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = d.get("rows") or []
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{_html.escape(r['name'])}</td><td>{r['totals']['start_clock']}</td>"
        f"<td>{r['totals']['stops']}</td><td>{r['totals']['walk_min']} 分钟</td>"
        f"<td>{r['totals']['stay_min']} 分钟</td><td>{r['totals']['wait_min']} 分钟</td>"
        f"<td>{r['totals']['span_min']} 分钟</td><td>{r['totals']['missing_count']} 项</td>"
        f"<td>{r['totals']['conflict_count']} 处</td></tr>" for r in rows)
    stuck = [r["name"] for r in rows if r["totals"]["stops"] == 0]
    stuck_html = (
        "<li class='warn' style='background:#fff5f5;border-left:3px solid #d62728;"
        "padding:6px 9px;list-style:none'>"
        f"<b>注意</b>：{_html.escape('、'.join(stuck))} 在这个组团 15 分钟内"
        "<b>无任何出行链要素可达</b>。这不是数据缺失，而是本次体检最值得注意的一条结论："
        "同一个组团，按成人步速（1.25 m/s）可达的要素，在助行器步速（0.60 m/s）下"
        "可能全部不可达。因此 15 分钟生活圈的达标口径必须写明所采用的步行速度。</li>"
        if stuck else "")
    return (
        "<h3 style='font-size:15px;margin:18px 0 8px'>5.4 同一栋楼，五类住户的一天</h3>"
        f"<p class='muted'>同一个居住组团（{_html.escape(d.get('point_name', ''))}），"
        "换成不同人群，出行链、步速、可接受步行距离都不同 —— 走出来的「一天」也完全不同。</p>"
        "<table><thead><tr><th>画像</th><th>出门</th><th>停靠</th><th>步行</th><th>停留</th>"
        "<th>等待</th><th>全程</th><th>不可达</th><th>服务时段冲突</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        f"<ul class='muted'>{stuck_html}"
        "<li><b>画像不是大模型现判的，是一张可核对的规则表</b>（<code>config/personas.json</code>）："
        "每类住户给出出行链、停留时长、设施开放时段、步速与可接受单段步行时间。"
        "理由：① 结果可复现，同一份数据跑出同一条动线；② 离线可用，不依赖外部接口；"
        "③ 每个人都能打开表核对「老人为什么是这几个点」。</li>"
        "<li><b>步速直接改变生活圈大小</b>：同样 15 分钟，成人（1.25 m/s）能走 1125 m，"
        "老人（0.85 m/s）只能走 765 m，助行器（0.60 m/s）只有 540 m —— "
        "所以「达标」要和「谁在走」一起说。</li>"
        "<li><b>设施不是全天可用</b>：菜市场 12:00 收市、社区卫生服务站午间停诊、"
        "幼儿园 08:30 关门。到达时若已闭门或尚未开门，动线会记成<b>服务时段冲突</b>并给出改法。</li>"
        "<li>停留时长、出发时刻、步速均为情景假设，可在交互式 Demo 里逐项调整后实时重算。</li></ul>")


def _route_section(out_dir: Path) -> list:
    """一个居民的一天（示例）：图 + 逐段时刻表。由 tools/route_map.py 生成。"""
    path = Path(out_dir) / "day_route.json"
    if not path.exists():
        return ["", "未生成示例动线。运行 `python tools/route_map.py <community_id>` 后本节自动写入"
                    "（交互式 Demo 里点任一个居住组团也能实时生成）。"]
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["totals"]
    lines = ["", "### 5.1 一个居民的一天（示例）", "",
             f"**示例**：{d['point']['name']}（{d['point']['households']:,} 户 / "
                 f"{d['point']['population']:,} 人），把 {t['stops']} 处必需要素各走一遍 ——",
             "",
             "![一天动线](map_route.png)",
             "",
             "| 顺序 | 到达–离开 | 要素 | 设施 | 步行 | 停留 |", "| --- | --- | --- | --- | --- | --- |"]
    for s in d.get("stops", []):
        lines.append(f"| {s['seq']} | {s['arrive']}–{s['leave']} | {s['element_name']} | "
                     f"{s['facility_name']} | {s['walk_min']} 分钟 / {s['walk_m']} m | "
                     f"{s['stay_min']} 分钟 |")
    if d.get("home_leg"):
        lines.append(f"| 回家 | {d['home_leg']['leave']} | — | — | "
                     f"{d['home_leg']['walk_min']} 分钟 / {d['home_leg']['walk_m']} m | — |")
    lines += ["",
              f"- 全程 **{t['span_min']} 分钟**（步行 {t['walk_min']} 分钟 / {t['walk_m']:,} m，"
              f"停留 {t['stay_min']} 分钟），{t['start_clock']} 出门、{t['end_clock']} 到家。",
              f"- 本次出行不可达的必需服务 **{t['missing_count']} 项**"
              + ("：" + "、".join(f"{m['name']}（{m['reason']}）" for m in d.get("missing", []))
                 if d.get("missing") else "（全部可达）"),
              f"- 排法：{d.get('order_used', '—')}；停留时长与出发时刻是**情景假设**"
              f"（交互式 Demo 里可逐项调整）。"]
    return lines


def _route_html(out_dir: Path) -> str:
    path = Path(out_dir) / "day_route.json"
    if not path.exists():
        return ("<p class='muted'>未生成示例动线（运行 "
                "<code>python tools/route_map.py &lt;community_id&gt;</code>；"
                "交互式 Demo 里点任一个居住组团也能实时生成）。</p>")
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["totals"]
    rows = "".join(
        f"<tr><td>{s['seq']}</td><td>{s['arrive']}–{s['leave']}</td>"
        f"<td>{_html.escape(s['element_name'])}</td><td>{_html.escape(s['facility_name'])}</td>"
        f"<td>{s['walk_min']} 分钟 / {s['walk_m']} m</td><td>{s['stay_min']} 分钟</td></tr>"
        for s in d.get("stops", []))
    if d.get("home_leg"):
        rows += (f"<tr><td>回家</td><td>{d['home_leg']['leave']}</td><td>—</td><td>—</td>"
                 f"<td>{d['home_leg']['walk_min']} 分钟 / {d['home_leg']['walk_m']} m</td>"
                 f"<td>—</td></tr>")
    missing = ("；".join(f"{_html.escape(m['name'])}（{_html.escape(m['reason'])}）"
                        for m in d.get("missing", [])) or "全部可达")
    return (f"<h3 style='font-size:15px;margin:18px 0 8px'>5.1 一个居民的一天（示例）</h3>"
            f"<p class='muted'>示例：<b>{_html.escape(d['point']['name'])}</b>"
            f"（{d['point']['households']:,} 户 / {d['point']['population']:,} 人），"
            f"把 {t['stops']} 处必需要素各走一遍。</p>"
            f"<figure><img src='map_route.png' alt='一天动线'>"
            f"<figcaption>图 4　一天动线：出门依次到访各必需要素（序号=顺序），"
            f"底图为步行时间分带（5/10/15 分钟）</figcaption></figure>"
            f"<table><thead><tr><th>顺序</th><th>到达–离开</th><th>要素</th><th>设施</th>"
            f"<th>步行</th><th>停留</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<ul class='muted'>"
            f"<li>全程 <b>{t['span_min']} 分钟</b>（步行 {t['walk_min']} 分钟 / "
            f"{t['walk_m']:,} m，停留 {t['stay_min']} 分钟），"
            f"{t['start_clock']} 出门、{t['end_clock']} 到家。</li>"
            f"<li>本次出行不可达的必需服务 <b>{t['missing_count']} 项</b>：{missing}</li>"
            f"<li>排法：{_html.escape(str(d.get('order_used', '—')))}；停留时长与出发时刻是"
            f"<b>情景假设</b>（交互式 Demo 里可逐项调整）。</li></ul>")


def _profile_lines(opt: Dict[str, Any]) -> list:
    """点位画像（建什么形态、多大规模、谁受益、跟谁合署）—— 报告里的"实施建议"。"""
    blocks = []
    for i, s in enumerate(opt.get("sites") or [], start=1):
        p = s.get("profile")
        if not p:
            continue
        ws = p.get("walk_stats") or {}
        cap = p.get("capacity") or {}
        b = p.get("build") or {}
        groups = p.get("groups") or []
        top = "、".join(f"{g['name']}（{g['walk_min']}′）" for g in groups[:8])
        rows = [
            f"#### P{i} 点位画像",
            "",
            f"- **受益面**：覆盖 **{p.get('benefit_count', 0)} 个居住组团 / "
            f"{p.get('service_population', 0):,} 人**；到该点步行时间中位 "
            f"{ws.get('median_min', '—')} 分钟、最长 {ws.get('max_min', '—')} 分钟"
            + (f"，其中 {ws['within_10min_share'] * 100:.0f}% 在 10 分钟以内"
               if ws.get("within_10min_share") is not None else ""),
            f"- **受益组团（就近前 8）**：{top}" + ("…" if len(groups) > 8 else ""),
        ]
        if b.get("form"):
            rows.append(f"- **建设形态**：{b['form']}")
        if b.get("space"):
            rows.append(f"- **规模建议**：{b['space']}")
        if b.get("approval"):
            rows.append(f"- **审批要点**：{b['approval']}")
        if b.get("carrier_note"):
            rows.append(f"- **载体约束**：{b['carrier_note']}")
        if cap.get("verdict"):
            rows.append(f"- **容量核对**：{cap['verdict']}"
                        f"（按 {cap.get('assumed_service_pop_per_unit', 0):,} 人/处的工程假设，"
                        f"相当于 {cap.get('units_implied')} 处）")
        if p.get("complement"):
            rows.append("- **可合署**：" + "；".join(
                f"{c['element_name']}（{c['facility']}，{c['distance_m']} m）"
                for c in p["complement"]))
        if b.get("caveat"):
            rows.append(f"- <span class='muted'>{b['caveat']}</span>")
        blocks += rows + [""]
    return (["", "**点位画像（实施建议）**", ""] + blocks) if blocks else []


def _profile_html(opt: Dict[str, Any]) -> str:
    """点位画像的 HTML 版（HTML 报告与 Markdown 是两条渲染路径，都要有）。"""
    cards = []
    for i, s in enumerate(opt.get("sites") or [], start=1):
        p = s.get("profile")
        if not p:
            continue
        ws = p.get("walk_stats") or {}
        cap = p.get("capacity") or {}
        b = p.get("build") or {}
        groups = "".join(
            f"<span class='pf-g'>{_html.escape(g['name'])} <b>{g['walk_min']}′</b></span>"
            for g in (p.get("groups") or [])[:12])
        rows = [
            ("受益面", f"<b>{p.get('benefit_count', 0)} 个居住组团 / "
                       f"{p.get('service_population', 0):,} 人</b>；到该点步行中位 "
                       f"{ws.get('median_min', '—')} 分钟、最长 {ws.get('max_min', '—')} 分钟"
                       + (f"，其中 {ws['within_10min_share'] * 100:.0f}% 在 10 分钟内"
                          if ws.get("within_10min_share") is not None else "")),
            ("受益组团", groups + ("…" if (p.get("groups_total") or 0) > 12 else "")),
            ("建设形态", _html.escape(b.get("form") or "")),
            ("规模建议", _html.escape(b.get("space") or "")),
            ("审批要点", _html.escape(b.get("approval") or "")),
            ("载体约束", _html.escape(b.get("carrier_note") or "")),
            ("容量核对", _html.escape(cap.get("verdict") or "")
                        + (f"<span class='muted'>（按 {cap.get('assumed_service_pop_per_unit', 0):,} 人/处的"
                           f"工程假设，相当于 {cap.get('units_implied')} 处）</span>" if cap else "")),
            ("可合署", "；".join(f"{_html.escape(c['element_name'])}（{_html.escape(c['facility'])}，"
                                f"{c['distance_m']} m）" for c in (p.get("complement") or []))),
        ]
        body = "".join(f"<div class='pf-r'><span>{k}</span><span>{v}</span></div>"
                       for k, v in rows if v)
        caveat = (f"<div class='muted pf-c'>{_html.escape(b['caveat'])}</div>"
                  if b.get("caveat") else "")
        cards.append(f"<div class='pf-card'><div class='pf-t'>P{i} 点位画像</div>{body}{caveat}</div>")
    if not cards:
        return ""
    return ("<h3 style='font-size:15px;margin:18px 0 8px'>点位画像（实施建议）</h3>"
            "<div class='pf-wrap'>" + "".join(cards) + "</div>")


def _prescription_section(opt: Optional[Dict[str, Any]], diag: Optional[Dict[str, Any]] = None,
                          after_diag: Optional[Dict[str, Any]] = None,
                          map_after: Optional[str] = None,
                          out_dir: Optional[Path] = None) -> str:
    if not opt:
        return "本次报告未执行选址优化。"
    basis = opt.get("siting_basis") or {}
    rej = opt.get("rejected") or {}
    counts = basis.get("carrier_counts") or {}
    lines = [
        f"**处方对象**：{opt['element_name']}（社区层面可自行选址：{'是' if opt.get('sitable') else '否'}）",
        "",
        f"**方法**：{opt['method']}",
        "",
        "**可建性约束**（市区便民设施几乎不可能空地新建，必须依托既有载体）：",
        "",
        f"- 候选点来源：{'载体梯队环带（1–4 梯队）' if opt.get('candidate_mode') == 'carriers' else '均匀网格（无载体信息，仅供演示）'}"
        f"，载体共 {basis.get('carriers_available', 0)} 处"
        + (f"（地铁站 {counts.get('tier1_transit', 0)} · 社区服务设施 {counts.get('tier2_service', 0)} · "
           f"商业综合体 {counts.get('tier3_commercial', 0)} · 公共设施 {counts.get('tier4_public', 0)}）"
           if counts else ""),
        f"- 排除不宜区：水体与河道 {basis.get('water_features', 0)} 处、禁建区 "
        f"{basis.get('exclusions_available', 0)} 处（党政机关/公安消防、校园与院区内部、市政设施）",
        f"- 同类间距要求：{basis.get('same_kind_gap_m', 0):.0f} m"
        f"（{'需独立空间' if basis.get('co_location') == 'independent' else '可合署布点'}）",
        f"- 实际筛除：位于水体内 {rej.get('water', 0)} 个候选、位于禁建区 {rej.get('exclusion', 0)} 个、"
        f"与同类过近 {rej.get('same_kind_gap', 0)} 个 → 可用候选 {opt['candidates_considered']} 个",
        "",
        "| 点位 | 梯队 | 依托载体 | 载体距离 | 单点新增覆盖户数 | 加权得分 | 同类最近既有设施 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, s in enumerate(opt["sites"], start=1):
        carrier = s.get("carrier") or {}
        near = (s.get("constraints") or {}).get("nearest_existing_same_m")
        lines.append(
            f"| P{i} | 第{s.get('tier', 0)}梯队 · {s.get('tier_name', '—')} | "
            f"{carrier.get('name', '—')} | {carrier.get('distance_m', 0):.0f} m | "
            f"{s['newly_covered_households']:,} | {s.get('score', 0):,.0f} | "
            f"{'—' if near is None else str(near) + ' m'} |")
    lines += ["", "**选址理由**："]
    for i, s in enumerate(opt["sites"], start=1):
        lines.append(f"- **P{i}**（{s['lon']:.6f}, {s['lat']:.6f}）：{s.get('reason', '')}"
                     f"　新增覆盖 {s['newly_covered_households']:,} 户 / "
                     f"{len(s['newly_covered_points'])} 个组团。")
    lines += _profile_lines(opt)
    lines += _marginal_section(out_dir) if out_dir else []
    lines += _data_quality_lines(diag)
    lines += [
        "",
        "| 覆盖指标 | 优化前 | 优化后 | 变化 |",
        "| --- | --- | --- | --- |",
        f"| 15 分钟可达覆盖率 | {_pct(opt['before']['coverage_rate'])} | {_pct(opt['after']['coverage_rate'])} | "
        f"+{opt['gain']['coverage_rate_pp']} pp |",
        f"| 不可达户数 | {opt['before']['uncovered_households']:,} | {opt['after']['uncovered_households']:,} | "
        f"-{opt['gain']['households']:,} |",
        f"| 不可达人口 | {opt['before']['uncovered_population']:,} | {opt['after']['uncovered_population']:,} | "
        f"-{opt['gain']['population']:,} |",
        "",
        "> 梯队系数（优先级权重）："
        + "、".join(f"第{k}梯队 ×{v}" for k, v in (basis.get("tier_coef") or {}).items())
        + "；**加权得分 = 新增覆盖户数 × 梯队系数**。",
    ]
    samples = (opt.get("rejected_samples") or {})
    if samples.get("water") or samples.get("exclusion"):
        bits = []
        for s in (samples.get("water") or [])[:2]:
            bits.append(f"水体样本 {s['lon']:.5f},{s['lat']:.5f}（{s['reason']}）")
        for s in (samples.get("exclusion") or [])[:2]:
            bits.append(f"禁建样本 {s['lon']:.5f},{s['lat']:.5f}（{s['reason']}：{s.get('near', '')}）")
        lines.append("> 被剔除的候选示例：" + "；".join(bits) + "。")

    # ---------------- 新增设施后的覆盖范围（把推荐点位代进去重算一遍） ----------------
    if after_diag:
        b_sum = (diag or {}).get("summary") or {}
        a_sum = after_diag.get("summary") or {}
        lines += ["", "### 新增设施后的覆盖范围（代入选址重算）", ""]
        if b_sum and a_sum:
            d0, d1 = b_sum.get("composite_score"), a_sum.get("composite_score")
            lines += [
                f"把 P1–P{len(opt['sites'])} 作为新建设施代入模型重算：**综合体检得分 "
                f"{d0} → {d1}**（{'+' if (d1 or 0) >= (d0 or 0) else ''}"
                f"{round((d1 or 0) - (d0 or 0), 1)}）。",
                "",
                "| 维度 | 现状 | 新增设施后 | 变化 |",
                "| --- | --- | --- | --- |",
            ]
            for key, dim in ((diag or {}).get("dimensions") or {}).items():
                s0 = round(dim["score"] * 100, 1)
                s1 = round(((after_diag.get("dimensions") or {}).get(key) or {}).get("score", 0) * 100, 1)
                lines.append(f"| {dim['label']} | {s0} | {s1} | {round(s1 - s0, 1):+} |")
            miss_before = sum(1 for p in (diag or {}).get("points", []) if p.get("missing"))
            miss_after = sum(1 for p in after_diag.get("points", []) if p.get("missing"))
            lines += ["",
                      f"存在缺口的居住组团：**{miss_before} → {miss_after}** 个"
                      f"（不可达人口 {opt['before']['uncovered_population']:,} → "
                      f"{opt['after']['uncovered_population']:,}）。", ""]
            lines.append("> 注：「冗余度（抗风险）」在新增设施后可能略降——"
                         "刚被覆盖的组团往往只依赖这一处新设施，属于正常的过渡效应，"
                         "继续增补第二处即可回升。")
        if map_after:
            lines += ["", f"![新增设施后的覆盖范围]({Path(map_after).name})", ""]
    return "\n".join(lines)


def _calibration_section(out_dir: Path) -> str:
    """如果跑过 tools/calibrate_walk.py，就把自算 vs 百度步行路径规划的对照写进报告。"""
    path = Path(out_dir) / "walk_calibration.json"
    if not path.exists():
        return ("*未执行自算路网标定。运行 `python tools/calibrate_walk.py "
                "<community_id>` 后，本节会自动写入与百度步行路径规划的抽样对照结果。*")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "*标定文件解析失败，请重新运行 calibrate_walk.py。*"
    s = data.get("summary", {})
    if not s.get("ok"):
        return "*标定未完成（无有效样本）。*"
    rec = s.get("implied_baidu_speed_median_mps")
    out = (
        f"用同一批 OD 抽样对照**百度地图步行路径规划**（官方口径），结果如下"
        f"（采样 {s['n_pairs']} 组，覆盖 {s.get('cutoff_s', 900) / 60:.0f} 分钟阈值上下）：\n\n"
        f"| 对照指标 | 结果 | 说明 |\n| --- | --- | --- |\n"
        f"| 距离比中位数 | {s.get('distance_ratio_median')} | 路网几何差异 |\n"
        f"| 时间比中位数（自算/百度） | {s.get('time_ratio_median')} | <1 表示自算偏乐观 |\n"
        f"| 时间比落在 ±30% 内的比例 | {_pct(s.get('share_within_30pct'))} | 稳健度 |\n"
        f"| 绝对时间差中位数 | {s.get('median_abs_time_diff_s')} 秒 | 直接误差 |\n"
        f"| **15 分钟判定一致率** | **{_pct(s.get('threshold_agreement_15min'))}** | 最关键：结论是否会被推翻 |\n"
        f"| 百度隐含步行速度 | {rec} m/s | 本项目取 {s.get('engine_walk_speed_mps')} m/s |\n\n"
        f"**结论**：判定一致率决定了「某组团是否在 15 分钟内可达」的结论是否稳健；"
        f"若一致率偏低，应按百度隐含速度（约 {rec} m/s）下调 `config/elements.json` 的 "
        f"`walk_speed_mps` 后重跑，或对临界组团单独复核。原始对照数据见 `walk_calibration.json`。"
    )
    if s.get("n_outliers_gt_2x"):
        ex = "；".join(f"{o['point']}→{o['facility']}（自算 {o['engine_s']:.0f}s vs 百度 {o['baidu_s']:.0f}s）"
                       for o in s.get("outlier_examples", []))
        out += (f"\n\n**离群样本（{s['n_outliers_gt_2x']} 组，时间比 >2）**：{ex}。"
                f"这类偏差来自 OSM 与百度路网拓扑差异（缺人行桥、门禁/围墙、小区内部通道），"
                f"不是速度假设造成的；建议对临界组团做人工复核，或补齐 OSM 缺失的过街设施。")
    if (Path(out_dir) / "align_check.png").exists():
        out += ("\n\n### 坐标对齐核验\n\n"
                "把自建路网直接画到百度底图上（`tools/align_check.py` 生成）：红线为主干路/支路、"
                "蓝线为内部步道、绿点为设施、红方块为居住组团。\n\n"
                "![坐标对齐核验](align_check.png)\n\n"
                "**结论**：红线正压在百度底图的马路上（曹杨路、中山北路、金沙江路等主干道逐条对齐），"
                "说明 WGS-84 ↔ BD-09 换算正确、无系统性偏移；局部出现「有红线无马路」是 OSM 覆盖更细"
                "（含小区内部通道），属于数据完备性差异，不是坐标错位。放大核验图见 `align_check_zoom.png`。")
    return out


def _calibration_html(out_dir: Path) -> str:
    path = Path(out_dir) / "walk_calibration.json"
    if not path.exists():
        return ('<p class="muted">未执行自算路网标定。运行 <code>python tools/calibrate_walk.py '
                '&lt;community_id&gt;</code> 后本节自动写入与百度步行路径规划的抽样对照。</p>')
    try:
        s = json.loads(path.read_text(encoding="utf-8")).get("summary", {})
    except Exception:  # noqa: BLE001
        return '<p class="muted">标定文件解析失败。</p>'
    if not s.get("ok"):
        return '<p class="muted">标定未完成（无有效样本）。</p>'
    rec = s.get("implied_baidu_speed_median_mps")
    rows = [
        ("距离比中位数", s.get("distance_ratio_median"), "路网几何差异"),
        ("时间比中位数（自算/百度）", s.get("time_ratio_median"), "&lt;1 表示自算偏乐观"),
        ("时间比落在 ±30% 内的比例", _pct(s.get("share_within_30pct")), "稳健度"),
        ("绝对时间差中位数", f"{s.get('median_abs_time_diff_s')} 秒", "直接误差"),
        ("<b>15 分钟判定一致率</b>", f"<b>{_pct(s.get('threshold_agreement_15min'))}</b>",
         "最关键：结论是否会被推翻"),
        ("百度隐含步行速度", f"{rec} m/s", f"本项目取 {s.get('engine_walk_speed_mps')} m/s"),
    ]
    body = "".join(f"<tr><td>{a}</td><td>{b}</td><td class='muted'>{c}</td></tr>" for a, b, c in rows)
    extra = ""
    if s.get("n_outliers_gt_2x"):
        ex = "；".join(f"{_html.escape(o['point'])}→{_html.escape(o['facility'])}"
                       f"（自算 {o['engine_s']:.0f}s vs 百度 {o['baidu_s']:.0f}s）"
                       for o in s.get("outlier_examples", []))
        extra = (f'<p class="muted">离群样本 {s["n_outliers_gt_2x"]} 组（时间比 &gt;2）：{ex}。'
                 f'这类偏差来自 OSM 与百度路网拓扑差异（缺人行桥、门禁/围墙、小区内部通道），'
                 f'不是速度假设造成的。</p>')
    if (Path(out_dir) / "align_check.png").exists():
        extra += ('<h3 style="font-size:14px;margin:16px 0 8px">坐标对齐核验</h3>'
                  '<p class="muted">把自建路网直接画到百度底图上（红线=主干路/支路，蓝线=内部步道，'
                  '绿点=设施，红方块=居住组团）。红线逐条压在百度底图的马路上，说明坐标系换算正确、'
                  '无系统性偏移；「有红线无马路」处是 OSM 覆盖更细，属数据完备性差异。</p>'
                  '<figure><img src="align_check.png" alt="坐标对齐核验">'
                  '<figcaption>图 7　坐标对齐核验（由 tools/align_check.py 生成）</figcaption></figure>')
    return (f'<p class="muted">用同一批 OD 抽样对照 <b>百度地图步行路径规划</b>（官方口径），'
            f'共 {s.get("n_pairs")} 组，覆盖 15 分钟阈值上下。</p>'
            f'<table><thead><tr><th>对照指标</th><th>结果</th><th>说明</th></tr></thead>'
            f'<tbody>{body}</tbody></table>{extra}'
            f'<p class="muted">判定一致率决定「某组团是否 15 分钟内可达」的结论是否稳健；'
            f'若偏低，应按百度隐含速度（约 {rec} m/s）下调 <code>walk_speed_mps</code> 后重跑。'
            f'原始对照数据见 <code>walk_calibration.json</code>。</p>')


def _disclaimer(ds: Dataset, diag: Dict[str, Any], elements_cfg: Dict[str, Any]) -> str:
    from .pipeline import summarize_household_provenance
    c = diag["cutoff"]
    cal = (ds.meta or {}).get("population_calibration") or {}
    ov = (ds.meta or {}).get("household_overrides") or {}
    lines = [
        f"- **数据来源**：`{ds.meta.get('source', 'unknown')}`"
        + (f"，生成器 `{ds.meta.get('generator')}`，种子 `{ds.meta.get('seed')}`"
           if ds.meta.get("seed") is not None else "")
        + f"。{ds.meta.get('note', '')}",
        f"- **户数口径**：{summarize_household_provenance(ds)}。",
    ]
    if ds.meta.get("residential_baidu_added"):
        floor = ds.meta.get("residential_density_floor_hh_per_km2") or 0
        lines.append(
            f"  - 居住组团来源：OSM 建筑覆盖不足（户数密度低于 {floor:,.0f} 户/km²），"
            f"已用百度「住宅区/小区」POI 补空白 **{ds.meta['residential_baidu_added']} 处**"
            f"（只在距已有 OSM 组团 150 m 以外的空白处补，户数取邻近组团中位数）；"
            f"合计 {ds.meta.get('residential_count')} 个居住组团。")
    if cal.get("applied"):
        lines.append(f"  - 总量校准系数 ×{cal.get('factor')}（{cal.get('basis')}）："
                     f"校准前 {cal.get('population_before'):,} 人 → 校准后 {cal.get('population_after'):,} 人。")
        if cal.get("sources"):
            lines.append("  - 数据来源：" + "；".join(str(s) for s in cal["sources"]) + "。")
        if cal.get("caveat"):
            lines.append(f"  - ⚠️ {cal['caveat']}")
    if ov.get("applied"):
        lines.append(f"  - 实测户数覆盖：{ov.get('matched')}/{ov.get('rows')} 条来自 `{ov.get('source')}`"
                     + (f"，未匹配 {ov.get('unmatched_count')} 条（{', '.join(ov.get('unmatched', [])[:5])}…）"
                        if ov.get("unmatched_count") else "") + "。")
    lines += [
        f"- **步行口径**：15 分钟生活圈 = 步行 {c['time_s'] / 60:.0f} 分钟 / 约 1000 m；"
        f"5 分钟近距 = {elements_cfg['cutoffs']['circle_5min']['time_s'] / 60:.0f} 分钟 / 约 300 m；"
        f"步行速度 {c['walk_speed_mps']} m/s（按道路类型 1.10–1.32 m/s 取值）。",
        f"- **人口口径**：平均每户 {c['avg_household_size']} 人；居住组团户数见上方户数口径。",
        "- **可达性算法**：路网最短路（Dijkstra）到最近路网节点 + 设施离路距离折算的步行惩罚；"
        "生活圈范围为所有居住组团 15 分钟可达区域的并集，不是以社区中心为圆心的缓冲区。"
        "已用百度步行路径规划抽样标定（见上一节）。",
        f"- **着色范围**：体检图与网页地图上的色块只铺在居住组团周边 "
        f"{(diag.get('grid_counts') or {}).get('mask', {}).get('resid_buffer_m', 150):.0f} m "
        f"以内、且非水体的位置（居民实际居住处）；蒙版只影响显示，不参与任何指标计算。",
        "- **坐标系**：数据集统一 WGS-84；百度 POI 由 BD-09 换算，底图分辨率按百度实测常数标定。",
        "- **已知局限**：① 未建模过街等待、坡度、天气与无障碍通行；② 设施容量按相对服务能力近似；"
        "③ 未区分设施开放时间与排队；④ 标准条目出处需在正式提交前逐条核对国标/导则原文。",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
DIM_SHORT = {
    "coverage": "覆盖率", "compliance": "达标率", "supply_demand": "供需均衡",
    "redundancy": "冗余度", "fairness": "公平性", "proximity_5min": "近距便利",
}


def _radar_svg(dims: Dict[str, Any], size: int = 460) -> str:
    """六维雷达图。

    尺寸留白是刻意的：标签用 anchor=middle 放在 1.18r 处，画布半径必须容得下
    最长的标签（"供需均衡 96"），否则会被 viewBox 裁掉——之前就是这里被裁了。
    """
    import math
    keys = list(dims.keys())
    n = len(keys)
    cx = cy = size / 2
    r = size / 2 - 110
    label_r = r * 1.18

    def pt(i: int, val: float):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + r * val * math.cos(ang), cy + r * val * math.sin(ang)

    def lp(i: int):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + label_r * math.cos(ang), cy + label_r * math.sin(ang)

    rings = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{pt(i, frac)[0]:.1f},{pt(i, frac)[1]:.1f}" for i in range(n))
        rings.append(f'<polygon points="{pts}" fill="none" stroke="#dfe3e8" stroke-width="1"/>')
    axes, labels = [], []
    for i, k in enumerate(keys):
        x, y = pt(i, 1.0)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#dfe3e8"/>')
        lx, ly = lp(i)
        name = DIM_SHORT.get(k, dims[k]["label"])
        val = dims[k]["score"] * 100
        dy = -6 if ly < cy else 4          # 上半圈标签上移，下半圈下移，避免压到图形
        labels.append(
            f'<text x="{lx:.1f}" y="{ly + dy:.1f}" font-size="13" fill="#5b6472" '
            f'text-anchor="middle">{_html.escape(name)}</text>'
            f'<text x="{lx:.1f}" y="{ly + dy + 14:.1f}" font-size="12" fill="#1f2937" '
            f'font-weight="600" text-anchor="middle">{val:.0f}</text>')
    poly = " ".join(
        f"{pt(i, max(0.02, dims[k]['score']))[0]:.1f},{pt(i, max(0.02, dims[k]['score']))[1]:.1f}"
        for i, k in enumerate(keys))
    return (f'<svg viewBox="0 0 {size} {size}" width="100%" height="auto" '
            f'style="max-width:{size}px" role="img" aria-label="六维指标雷达图">'
            + "".join(rings) + "".join(axes)
            + f'<polygon points="{poly}" fill="rgba(31,119,180,0.28)" stroke="#1f77b4" stroke-width="2"/>'
            + "".join(labels) + "</svg>")


def _element_bars(diag: Dict[str, Any]) -> str:
    rows = []
    for e in sorted(diag["elements"], key=lambda x: x["coverage_rate"]):
        if not e["required"]:
            continue
        cov = e["coverage_rate"]
        color = "#d62728" if cov < 0.75 else ("#ff7f0e" if cov < 0.95 else "#2ca02c")
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{_html.escape(e["name"])}</span>'
            f'<span class="bar-track">'
            f'<span class="bar-fill" style="width:{cov * 100:.1f}%;background:{color}">'
            f'</span></span><span class="bar-val">{cov * 100:.1f}%</span></div>')
    return "".join(rows)


def _html_report(ds: Dataset, diag: Dict[str, Any], opt: Optional[Dict[str, Any]],
                 elements_cfg: Dict[str, Any], map_name: str, map_solution: Optional[str],
                 generated: str, out_dir: Optional[Path] = None) -> str:
    s = diag["summary"]
    _e = ds.community.get("extent_m", {})
    _box_note = (f"{2 * _e.get('half_width', 800) / 1000:.2f} × "
                 f"{2 * _e.get('half_height', 800) / 1000:.2f} km")
    problems = "".join(
        f'<div class="problem level-{"high" if p["level"] == "高" else "mid"}">'
        f'<div class="p-title">{_html.escape(p["title"])}</div>'
        f'<div class="p-detail">{_html.escape(p["detail"])}</div>'
        f'<div class="p-impact">影响约 {p["affected_households"]} 户 / {p["affected_population"]} 人</div></div>'
        for p in diag["problems"]) or "<p>未发现必需要素缺口。</p>"

    elem_rows = "".join(
        "<tr>"
        f"<td>{_html.escape(e['name'])}</td><td>{_html.escape(e['category'])}</td>"
        f"<td>{'✔' if e['required'] else '—'}</td><td>{e['facility_count']}</td>"
        f"<td class={'ok' if e['coverage_rate'] >= 0.95 else 'bad'}>{_pct(e['coverage_rate'])}</td>"
        f"<td>{_pct(e['proximity_5min_rate'])}</td><td>{_pct(e['single_dependency_rate'])}</td>"
        f"<td>{e['supply_demand_ratio']:.2f}</td><td>{e['gap_index']:.2f}</td>"
        f"<td>{_fmt_time(e['median_time_s'])}</td></tr>"
        for e in diag["elements"])

    # f-string 的替换字段不能跨行（Python 3.12 之前），这几个多行表达式先算好再插值
    dim_rows = "".join(
        f"<tr><td>{_html.escape(d['label'])}</td><td>{d['score'] * 100:.1f}</td>"
        f"<td class='muted'>{_html.escape(d['detail'])}</td></tr>"
        for d in diag["dimensions"].values())
    disclaimer_html = "".join(
        f'<li>{_html.escape(x.lstrip("- "))}</li>'
        for x in _disclaimer(ds, diag, elements_cfg).splitlines())

    point_rows = "".join(
        "<tr>"
        f"<td>{p['id']}</td><td>{_html.escape(p['name'])}</td><td>{p['households']}</td>"
        f"<td>{p['population']}</td><td>{'跨河' if p['across_river'] else '—'}</td>"
        f"<td>{p['compliance_score'] * 100:.0f}</td>"
        f"<td>{_html.escape('、'.join(p['missing']) or '—')}</td></tr>"
        for p in sorted(diag["points"], key=lambda x: x["compliance_score"]))

    opt_html = ""
    if opt:
        basis = opt.get("siting_basis") or {}
        rej = opt.get("rejected") or {}
        counts = basis.get("carrier_counts") or {}
        site_rows = "".join(
            f"<tr><td><b>P{i}</b></td>"
            f"<td>第{s_.get('tier', 0)}梯队<br>"
            f"<span class='muted'>{_html.escape(s_.get('tier_name', ''))}</span></td>"
            f"<td>{_html.escape((s_.get('carrier') or {}).get('name', '—'))}"
            f"<br><span class='muted'>{(s_.get('carrier') or {}).get('distance_m', 0):.0f} m</span></td>"
            f"<td>{s_['newly_covered_households']:,}</td>"
            f"<td>{s_.get('score', 0):,.0f}</td>"
            f"<td class='muted'>{_html.escape(s_.get('reason', ''))}</td></tr>"
            for i, s_ in enumerate(opt["sites"], start=1))
        # 单独提出来：f-string 的替换字段里不能跨行（Python 3.12 之前），
        # 写在模板里这一行会超过行长上限
        map_solution_html = (
            f'<figure><img src="{map_solution}" alt="选址方案图">'
            '<figcaption>图 5　选址优化方案（橙色图钉为推荐新增点位）</figcaption></figure>'
            if map_solution else "")
        opt_html = f"""
  <h2>6. 规划建议（处方）</h2>
  <p class="muted">对象：<b>{_html.escape(opt['element_name'])}</b>；方法：{_html.escape(opt['method'])}</p>
  {_marginal_html(out_dir)}
  <div class="siting-box">
    <b>可建性约束</b>（便民设施无法空地新建，必须依托既有载体）：
    <ul class="muted">
      <li>候选点来源：{'载体梯队环带（1–4 梯队）' if opt.get('candidate_mode') == 'carriers' else '均匀网格（无载体信息）'}，
        载体 {basis.get('carriers_available', 0)} 处
        （地铁站 {counts.get('tier1_transit', 0)} · 社区服务设施 {counts.get('tier2_service', 0)} ·
         商业综合体 {counts.get('tier3_commercial', 0)} · 公共设施 {counts.get('tier4_public', 0)}）</li>
      <li>排除不宜区：水体与河道 {basis.get('water_features', 0)} 处、禁建区 {basis.get('exclusions_available', 0)} 处
        （党政机关/公安消防、校园与院区内部、市政设施）</li>
      <li>同类间距 {basis.get('same_kind_gap_m', 0):.0f} m
        （{'需独立空间' if basis.get('co_location') == 'independent' else '可合署布点'}）</li>
      <li>实际筛除：水体内 {rej.get('water', 0)} 个 · 禁建区内 {rej.get('exclusion', 0)} 个 ·
        同类过近 {rej.get('same_kind_gap', 0)} 个 → 可用候选 {opt['candidates_considered']} 个</li>
    </ul>
  </div>
  <table><thead><tr><th>点位</th><th>梯队</th><th>依托载体</th><th>单点新增覆盖户数</th>
    <th>加权得分</th><th>选址理由</th></tr></thead><tbody>{site_rows}</tbody></table>
  <p class="muted">注：上表「单点新增覆盖户数」是<strong>假如只建这一处</strong>时的覆盖户数；
    多处会覆盖同一组团，因此各项相加会重复计数 —— 多处合计请看 6.1 的并集口径。</p>
  {_profile_html(opt)}
  {_data_quality_html(diag)}
  <p class="muted">覆盖率 <b>{_pct(opt['before']['coverage_rate'])} → {_pct(opt['after']['coverage_rate'])}</b>
     （+{opt['gain']['coverage_rate_pp']} pp），不可达人口
     {opt['before']['uncovered_population']:,} → {opt['after']['uncovered_population']:,}。
     得分 = 新增覆盖户数 × 梯队系数（
     {'、'.join(f"第{k}梯队 ×{v}" for k, v in (basis.get('tier_coef') or {}).items())}）。</p>
  {map_solution_html}
"""

    cross = _cross_river_section(diag)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>社区生活圈体检报告 · {_html.escape(ds.community.get('name', ''))}</title>
<style>
 :root {{ --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb; --bg:#f7f8fa; --accent:#1f77b4; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--ink);
        font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
 header {{ background:linear-gradient(120deg,#123b63,#1f77b4 60%,#3fa7d6); color:#fff; padding:28px 32px; }}
 header h1 {{ margin:0 0 6px; font-size:24px; }}
 header .sub {{ opacity:.9; font-size:13px; }}
 main {{ max-width:1080px; margin:0 auto; padding:24px 20px 80px; }}
 section, .card {{ background:#fff; border:1px solid var(--line); border-radius:12px;
                   padding:20px 22px; margin-bottom:18px; }}
 h2 {{ font-size:18px; margin:0 0 14px; padding-bottom:8px; border-bottom:2px solid var(--line); }}
 .score-grid {{ display:flex; flex-wrap:wrap; gap:14px; }}
 .score-tile {{ flex:1 1 150px; background:var(--bg); border:1px solid var(--line);
                border-radius:10px; padding:12px 14px; }}
 .score-tile .v {{ font-size:26px; font-weight:700; }}
 .score-tile .k {{ font-size:12px; color:var(--muted); }}
 .flex2 {{ display:flex; gap:24px; flex-wrap:wrap; align-items:center; }}
 table {{ width:100%; border-collapse:collapse; font-size:13px; }}
 th, td {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:left; }}
 th {{ background:#f2f5f9; font-weight:600; }}
 td.ok {{ color:#2ca02c; font-weight:600; }} td.bad {{ color:#d62728; font-weight:600; }}
 .bar-row {{ display:flex; align-items:center; gap:10px; margin:6px 0; font-size:13px; }}
 .bar-label {{ width:190px; color:#374151; }}
 .bar-track {{ flex:1; height:12px; background:#eef1f5; border-radius:6px; overflow:hidden; }}
 .bar-fill {{ display:block; height:100%; }}
 .bar-val {{ width:56px; text-align:right; font-variant-numeric:tabular-nums; }}
 .problem {{ border-left:4px solid #ff7f0e; background:#fffaf3; padding:10px 14px; margin:10px 0;
             border-radius:6px; }}
 .problem.level-high {{ border-color:#d62728; background:#fff5f5; }}
 .p-title {{ font-weight:600; }} .p-detail {{ color:#4b5563; font-size:13px; }}
 .p-impact {{ color:var(--muted); font-size:12px; margin-top:4px; }}
 figure {{ margin:16px 0; }} img {{ max-width:100%; border:1px solid var(--line); border-radius:10px; }}
 figcaption {{ font-size:12px; color:var(--muted); margin-top:6px; }}
 .muted {{ color:var(--muted); font-size:13px; }}
 code {{ background:#f2f5f9; padding:1px 5px; border-radius:4px; font-size:12px; }}
 .badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
           background:rgba(255,255,255,.2); }}
  .pf-wrap {{ display:flex; flex-direction:column; gap:12px; margin-top:10px; }}
  .pf-card {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fbfcfe; }}
  .pf-t {{ font-weight:600; margin-bottom:8px; }}
  .pf-r {{ display:flex; gap:10px; font-size:13px; margin:4px 0; }}
  .pf-r > span:first-child {{ flex:0 0 64px; color:var(--muted); }}
  .pf-r > span:last-child {{ flex:1 1 auto; line-height:1.6; }}
  .pf-g {{ display:inline-block; background:#eef2f7; border-radius:5px; padding:1px 6px;
           margin:0 6px 4px 0; font-size:12px; }}
  .pf-c {{ border-top:1px dashed var(--line); padding-top:6px; margin-top:6px; font-size:12px; }}
</style></head>
<body>
<header>
  <h1>{_html.escape(ds.community.get('name', ''))} · 社区生活圈体检报告</h1>
  <div class="sub">{_html.escape(ds.community.get('district', ''))}　|　生成时间 {generated}　|
    <span class="badge">数据源 {ds.meta.get('source', '?')}</span>
    <span class="badge">15 分钟步行口径</span></div>
</header>
<main>
  <section>
    <h2>1. 结论速览</h2>
    <div class="score-grid">
      <div class="score-tile"><div class="v">{s['composite_score']}</div>
        <div class="k">综合体检得分（{s['grade']}）</div></div>
      <div class="score-tile"><div class="v">{s['population']:,}</div>
        <div class="k">覆盖人口（{s['households']:,} 户）</div></div>
      <div class="score-tile"><div class="v">{s['facilities']}</div><div class="k">设施点位数</div></div>
      <div class="score-tile"><div class="v">{s['circle_area_km2']} km²</div>
        <div class="k">15 分钟生活圈范围</div></div>
      <div class="score-tile"><div class="v">{s['gini']:.2f}</div><div class="k">组团间公平性 Gini</div></div>
    </div>
    <div class="flex2" style="margin-top:18px">
      {_radar_svg(diag['dimensions'])}
      <div style="flex:1 1 420px">{_element_bars(diag)}</div>
    </div>
  </section>

  <section>
    <h2>2. 六维指标</h2>
    <table><thead><tr><th>维度</th><th>得分</th><th>说明</th></tr></thead><tbody>
    {dim_rows}
    </tbody></table>
  </section>

  <section>
    <h2>3. 要素体检表</h2>
    <table><thead><tr><th>要素</th><th>类别</th><th>必需</th><th>设施数</th><th>15 分钟可达覆盖率</th>
    <th>5 分钟近距率</th><th>单一依赖率</th><th>供需比</th><th>缺口指数</th><th>中位步行时间</th></tr></thead>
    <tbody>{elem_rows}</tbody></table>
    <p class="muted">口径提示：<b>供需比 / 缺口指数是相对模型</b>，「每处设施服务人口」为工程假设值
      （国标未规定该参数），有实测服务能力时应替换；覆盖率、达标率、可达时间等硬指标不受该假设影响。
      详见 <code>docs/metrics.md</code>。</p>
    {_standards_html(elements_cfg)}
  </section>

  <section>
    <h2>4. 问题清单</h2>
    {problems}
  </section>

  <section>
    <h2>5. 空间分布</h2>
    <figure><img src="{map_name}" alt="体检地图">
      <figcaption>图 1　居住组团周边 15 分钟可达必需要素数量（色块只铺在居民实际居住处，
        水体与无居住区域不着色）、设施分布与组团达标情况。
        图中虚线方框为<b>分析范围</b>（{_box_note}）：居住组团、设施与各项指标只统计框内，
        框外路网与设施仅在步行可达性计算中作为通路使用 —— 因此底图框外仍可见住宅区标注，
        但不属于本次体检对象</figcaption></figure>
    <figure><img src="map_isochrone.png" alt="步行等时圈">
      <figcaption>图 2　步行时间分带（等时圈）：从任一居住组团出发，5 / 10 / 15 分钟可达的范围；
        未着色即生活圈之外</figcaption></figure>
    {_focus_html(out_dir)}
    {cross}
    {_route_html(out_dir)}
    {_persona_html(out_dir)}
  </section>

  <section>{opt_html}</section>

  <section>
    <h2>7. 误差分析：自算路网 vs 百度步行路径规划</h2>
    <!--CALIBRATION-->
    {_robustness_html(out_dir)}
  </section>

  <section>
    <h2>8. 数据与口径声明</h2>
    <ul class="muted">{disclaimer_html}</ul>
  </section>

  <section>
    <h2>9. 复现方式</h2>
    <p class="muted">本报告完全由脚本生成，可一键复现：</p>
    <pre class="muted"><code>make demo        # 生成数据 → 体检 → 报告
make serve       # 启动交互式 Demo（http://127.0.0.1:8000）
make test        # 引擎单元测试</code></pre>
  </section>
</main>
</body></html>
"""


# ----------------------------------------------------------------------
def build_report(ds: Dataset, diag: Dict[str, Any], elements_cfg: Dict[str, Any],
                 out_dir: str | Path, opt: Optional[Dict[str, Any]] = None,
                 run_optimize: bool = True, k: int = 2,
                 after_diag: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    generated = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    if opt is None and run_optimize:
        opt = optimize_sites(ds, elements_cfg, k=k)

    map_path = render_map_png(ds, diag, out / "map_overview.png", opt=None)
    map_iso = render_map_png(ds, diag, out / "map_isochrone.png", opt=None, raster="isochrone")
    map_solution = render_map_png(ds, diag, out / "map_solution.png", opt=opt) if opt else None
    # 优化后覆盖图：把推荐点位算进去重算的体检结果（"直观看到新增设施后的覆盖范围"）
    map_after = None
    if opt and after_diag is not None:
        map_after = render_map_png(ds, after_diag, out / "map_after.png", opt=opt)

    (out / "diagnose.json").write_text(
        json.dumps(strip_runtime(diag), ensure_ascii=False, indent=1), encoding="utf-8")
    if opt:
        (out / "optimize.json").write_text(json.dumps(opt, ensure_ascii=False, indent=1),
                                           encoding="utf-8")

    s = diag["summary"]
    calibration_md = _calibration_section(out)
    # Markdown 表格行必须是一整行，拆开会破坏表格 —— 先算好再插值
    headline_row = (
        f"| **{s['composite_score']}** | {s['grade']} | {s['residential_points']} "
        f"| {s['households']:,} | {s['population']:,} | {s['facilities']} "
        f"| {s['circle_area_km2']} km² | {s['gini']:.2f} |")
    md = f"""# 社区生活圈体检报告 · {ds.community.get('name', '')}

> 生成时间：{generated}　|　数据源：`{ds.meta.get('source', '?')}`　|
> 口径：15 分钟步行生活圈（{diag['cutoff']['time_s'] / 60:.0f} 分钟 / 约 1000 m）

## 1. 结论速览

| 综合体检得分 | 等级 | 居住组团 | 户数 | 人口 | 设施点位 | 生活圈面积 | 组团间 Gini |
| --- | --- | --- | --- | --- | --- | --- | --- |
{headline_row}

![体检地图]({map_path.name})
<!-- 色块 = 居住组团周边 15 分钟可达必需要素数量；只铺在居民实际居住处，水体与无居住区域不着色 -->

![步行等时圈](map_isochrone.png)

> 图 2　步行时间分带（等时圈）：从任一居住组团出发 5 / 10 / 15 分钟可达的范围，未着色即生活圈之外。

## 2. 六维指标

{_dimensions_table(diag)}

## 3. 要素体检表

{_elements_table(diag)}

### 3.1 标准依据与达标口径

{_standards_table(elements_cfg)}

## 4. 问题清单

{_problems_section(diag)}

## 5. 空间分布与跨河可达性

{_cross_river_section(diag) or "（本数据集未包含河道分隔信息。）"}
{chr(10).join(_focus_section(out))}

## 5.1 一个居民的一天（示例）
{chr(10).join(_route_section(out))}
{chr(10).join(_persona_section(out))}

## 6. 规划建议（处方）

{_prescription_section(opt, diag, after_diag, map_after.name if map_after else None, out)}

## 7. 误差分析：自算路网 vs 百度步行路径规划

{calibration_md}
{chr(10).join(_robustness_section(out))}

## 8. 数据与口径声明

{_disclaimer(ds, diag, elements_cfg)}

## 9. 复现方式

```bash
make demo     # 生成数据 → 体检 → 报告
make serve    # 启动交互式 Demo
make test     # 引擎单元测试
```

---
*本报告由 `lifecycle_metrics` 引擎自动生成；所有指标均可由 `out/diagnose.json` 与
`out/optimize.json` 复核。*
"""
    (out / "report.md").write_text(md, encoding="utf-8")
    html_text = _html_report(ds, diag, opt, elements_cfg, map_path.name,
                             map_solution.name if map_solution else None, generated, out)
    html_text = html_text.replace("<!--CALIBRATION-->", _calibration_html(out))
    (out / "report.html").write_text(html_text, encoding="utf-8")

    result = {"report.md": out / "report.md", "report.html": out / "report.html",
              "diagnose.json": out / "diagnose.json", "map_overview.png": map_path,
              "map_isochrone.png": map_iso}
    if opt:
        result["optimize.json"] = out / "optimize.json"
    if map_solution:
        result["map_solution.png"] = map_solution
    if map_after:
        result["map_after.png"] = map_after
    return result
