"""跨社区横向对比：同一套指标口径下，把多个社区的体检结果放在一起看。

    python tools/compare.py [community_id ...]

产出：
    out/compare.json   机器可读的对比表
    out/compare.html   自包含的对比页（无外部资源，可直接发给别人看）

回答的问题是"**这套方法能不能换个街道直接跑通**"——六个社区用同一份要素清单、
同一套阈值与引擎，只有数据来源与人口校准不同。纯本地读取已有结果，不联网。
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import load_communities, load_elements, repo_path  # noqa: E402

DIM_ORDER = ["coverage", "compliance", "supply_demand", "redundancy", "fairness",
             "proximity_5min"]
DIM_LABEL = {"coverage": "可达覆盖率", "compliance": "要素达标率", "supply_demand": "供需均衡度",
             "redundancy": "冗余度（抗风险）", "fairness": "公平性",
             "proximity_5min": "5 分钟近距便利度"}


def _load(path: Path) -> Optional[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def collect(cids: List[str]) -> List[Dict[str, Any]]:
    cfg_all = load_communities()
    rows = []
    for c in cfg_all["communities"]:
        cid = c["id"]
        if cids and cid not in cids:
            continue
        out = repo_path("out") / cid
        diag = _load(out / "diagnose.json")
        if not diag:
            continue
        opt = _load(out / "optimize.json") or {}
        rob = _load(out / "robustness.json") or {}
        ds_meta = (diag.get("meta") or {})
        s = diag["summary"]
        dims = {k: round(v["score"] * 100, 1) for k, v in diag.get("dimensions", {}).items()}
        req = [e for e in diag["elements"] if e["required"]]
        worst = min(req, key=lambda e: e["coverage_rate"]) if req else {}
        rows.append({
            "id": cid, "name": c.get("name", cid),
            "district": c.get("district") or "—",
            "source": ds_meta.get("source", "—"),
            "provider": c.get("provider", "—"),
            "calibrated": bool((ds_meta.get("population_calibration") or {}).get("applied")),
            "household_source": ds_meta.get("residential_source"),
            "points": s["residential_points"], "households": s["households"],
            "population": s["population"],
            "assessable_population": s.get("assessable_population"),
            "facilities": s["facilities"], "facilities_fetched": s.get("facilities_fetched"),
            "road_nodes": s["road_nodes"],
            "score": s["composite_score"], "grade": s["grade"],
            "dims": dims, "gini": s["gini"], "circle_km2": s["circle_area_km2"],
            "worst_element": worst.get("name"), "worst_coverage": worst.get("coverage_rate"),
            "prescription": ({"element": opt.get("element_name"),
                              "before": opt.get("before", {}).get("coverage_rate"),
                              "after": opt.get("after", {}).get("coverage_rate"),
                              "sites": len(opt.get("sites") or [])} if opt else None),
            "robustness": ({"min": rob.get("score_min"), "max": rob.get("score_max"),
                            "span": rob.get("score_span"),
                            "worst_stable": rob.get("worst_element_stable")} if rob else None),
            "stitched": s.get("stitched_edges"),
            "unassessable_households": s.get("unassessable_households"),
            "components": s.get("network_components"),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def _bar_svg(rows: List[Dict[str, Any]]) -> str:
    """综合体检得分与 15 分钟可达覆盖率的对比条（纯 SVG，不依赖任何前端库）。

    名称栏宽度按最长社区名算，不写死 —— 写死 120 px 时"曹杨新村街道（真实数据）"
    这类长名会被柱子压住（实拍踩到过）。
    """
    gutter = min(280, max(120, int(max(len(r["name"]) for r in rows) * 12.6) + 12))
    scale = 4.6
    bar_x = gutter + 10
    width = int(bar_x + 100 * scale + 52)
    h = 32 * len(rows) + 34
    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" height="{h}" role="img">',
             '<text x="0" y="13" font-size="12" fill="#6b7280">'
             '综合体检得分（蓝）／15 分钟可达覆盖率（绿）　满分 100</text>',
             f'<line x1="{bar_x}" y1="18" x2="{bar_x}" y2="{h - 6}" stroke="#e5e7eb"/>']
    for i, r in enumerate(rows):
        y = 26 + i * 32
        parts.append(f'<text x="{gutter}" y="{y + 16}" font-size="12.5" fill="#374151" '
                     f'text-anchor="end">{_html.escape(r["name"])}</text>')
        for j, (v, color) in enumerate(((r["score"], "#1f77b4"),
                                        (r["dims"].get("coverage", 0), "#2ca02c"))):
            w = max(2.0, v * scale)
            yy = y + j * 13
            parts.append(f'<rect x="{bar_x}" y="{yy + 2}" width="{w:.1f}" height="10" '
                         f'fill="{color}" rx="2"/>')
            parts.append(f'<text x="{bar_x + w + 6:.1f}" y="{yy + 11}" font-size="11.5" '
                         f'fill="{color}">{v}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
    head = ("<tr><th>社区</th><th>数据源</th><th>组团</th><th>户数</th><th>人口</th>"
            "<th>设施</th><th>得分</th><th>等级</th>"
            + "".join(f"<th>{DIM_LABEL[k]}</th>" for k in DIM_ORDER)
            + "<th>生活圈</th><th>最短板要素</th><th>处方（覆盖率）</th><th>稳健区间</th>"
            "<th>路网</th></tr>")
    body = []
    for r in rows:
        p = r["prescription"]
        rb = r["robustness"]
        body.append(
            "<tr>"
            f"<td><b>{_html.escape(r['name'])}</b><br><span class='muted'>{_html.escape(r['district'])}"
            f"{'（已人口校准）' if r['calibrated'] else '（未校准）'}</span></td>"
            f"<td class='muted'>{_html.escape(str(r['source']))}<br>"
            f"户数源 {_html.escape(str(r['household_source'] or '—'))}</td>"
            f"<td>{r['points']}</td><td>{r['households']:,}</td><td>{r['population']:,}</td>"
            f"<td>{r['facilities']}<span class='muted'> /{r['facilities_fetched'] or '—'}</span></td>"
            f"<td><b>{r['score']}</b></td><td>{_html.escape(r['grade'])}</td>"
            + "".join(f"<td>{r['dims'].get(k, '—')}</td>" for k in DIM_ORDER)
            + f"<td>{r['circle_km2']} km²</td>"
            f"<td>{_html.escape(str(r['worst_element'] or '—'))}"
            f"<span class='muted'> {r['worst_coverage'] * 100:.1f}%</span></td>"
            + (f"<td>{_html.escape(str(p['element']))}<br>"
               f"<span class='muted'>{p['before'] * 100:.1f}% → {p['after'] * 100:.1f}%"
               f"（{p['sites']} 处）</span></td>" if p else "<td>—</td>")
            + (f"<td>{rb['min']}–{rb['max']}<span class='muted'><br>跨度 {rb['span']}"
               f"{'，短板要素稳定' if rb['worst_stable'] else ''}</span></td>" if rb else "<td>—</td>")
            + (f"<td class='muted'>{r['components']} 块<br>补连 {r['stitched']} 处<br>"
               + (f"<b style='color:#d62728'>{r['unassessable_households']:,} 户无法评估</b>"
                  if r.get("unassessable_households") else "全部组团可评估")
               + "</td>")
            + "</tr>")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>跨社区横向对比 · 社区生活圈体检</title><style>
 body {{ margin:0; background:#f7f8fa; color:#1f2430;
   font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
 header {{ background:linear-gradient(120deg,#123b63,#1f77b4 60%,#3fa7d6); color:#fff;
   padding:26px 32px; }}
 header h1 {{ margin:0 0 6px; font-size:22px; }}
 header .sub {{ opacity:.9; font-size:13px; }}
 main {{ max-width:1500px; margin:0 auto; padding:22px 20px 70px; }}
 section {{ background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:18px 20px;
   margin-bottom:18px; }}
 h2 {{ font-size:17px; margin:0 0 12px; padding-bottom:8px; border-bottom:2px solid #e5e7eb; }}
 table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
 th, td {{ border-bottom:1px solid #e5e7eb; padding:6px 7px; text-align:left;
   vertical-align:top; }}
 th {{ background:#f2f5f9; font-weight:600; position:sticky; top:0; }}
 .muted {{ color:#6b7280; font-size:12px; }}
 .tbl-wrap {{ overflow-x:auto; }}
 ul {{ margin:6px 0 0 18px; padding:0; }}
</style></head><body>
<header>
  <h1>跨社区横向对比 · 15 分钟生活圈体检</h1>
  <div class="sub">同一份要素清单（{len(cfg.get('elements', []))} 项）· 同一套判定阈值
    （步行 15 分钟 / 5 分钟）· 同一个计算引擎 · 只有数据来源与人口口径不同</div>
</header>
<main>
  <section>
    <h2>1. 总览</h2>
    {_bar_svg(rows)}
  </section>
  <section>
    <h2>2. 逐项对比</h2>
    <div class="tbl-wrap"><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>
    <p class="muted">「可达覆盖率」= 15 分钟步行可达该要素的人口占比；「要素达标率」= 11 项
      必需要素全部可达的人口占比。设施列：分析范围内的点位数 / 抓取到的总数
      （抓取范围外扩 1200 m，框外设施只参与可达性计算，不计入点位数）。</p>
  </section>
  <section>
    <h2>3. 怎么读这张表</h2>
    <ul class="muted">
      <li><b>可比</b>：要素清单、阈值、权重、算法、坐标口径完全一致。</li>
      <li><b>不可比</b>：数据来源（百度 POI + OSM 覆盖度）与人口口径。「已人口校准」
        的社区用七普街道密度把户数校准到真实量级；未校准的自选社区是估算值。
        比较时看结构与短板，不要直接比绝对人口。</li>
      <li><b>稳健区间</b>：7 个情景（步速 1.10/1.25/1.35 m/s × 阈值 ±10%）重算的得分范围，
        区间窄说明结论不依赖假设。</li>
      <li><b>路网列</b>：连通块数与断头路补连处数，补连越少说明 OSM 路网越完整。</li>
    </ul>
  </section>
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨社区横向对比")
    ap.add_argument("community_ids", nargs="*")
    args = ap.parse_args(argv)
    rows = collect(args.community_ids)
    cfg = load_elements(repo_path("config/elements.json"))
    out_dir = repo_path("out")
    (out_dir / "compare.json").write_text(
        json.dumps({"rows": rows, "dim_label": DIM_LABEL}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (out_dir / "compare.html").write_text(render_html(rows, cfg), encoding="utf-8")
    for r in rows:
        print(f"{r['name'][:16]:18s} {r['score']:>5} {r['grade']:4s} "
              f"覆盖率 {r['dims'].get('coverage'):>5} 最短板 {str(r['worst_element'])[:12]}")
    print(f"→ out/compare.html（{len(rows)} 个社区）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
