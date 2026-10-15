"""一次性配置补充：给可落地要素加"建设形态/规模/审批"的工程建议字段。

    python tools/add_build_notes.py          # 幂等：已存在则跳过

字段说明（写进 config/elements.json，报告与前端直接读）：
    build_form    建设形态建议（依托什么载体、放在什么位置）
    space_hint    规模建议 —— **工程建议值**，不是标准强制条款
    approval      审批/合规要点
    caveat        免责说明（与 source_caveat 呼应）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import repo_path                            # noqa: E402

CAVEAT = ("以上为建设形态的工程建议，不是标准强制条款；面积与审批要求以规划、"
          "住建与行业主管部门的现行规定为准，正式报批前须逐条核对原文。")

NOTES = {
    "market": {
        "build_form": "依托既有沿街商业铺面或社区商业网点改造，优先一层、带独立卸货口；"
                      "不宜设在二层以上（老年人拎菜上下楼不现实）",
        "space_hint": "工程建议：营业面积 500–1000 m²，含卸货位与垃圾分类暂存点",
        "approval": "市场监管（食品经营许可）、消防备案；占用小区内部空间需业主共同决定",
    },
    "community_health": {
        "build_form": "与社区卫生服务中心合署或利用既有公建配套首层，需独立出入口与无障碍坡道",
        "space_hint": "工程建议：建筑面积 200–400 m²（诊室、治疗室、药房、候诊分区）",
        "approval": "卫健部门设置审批、医疗废物转运协议；涉及放射性设备另需环评",
    },
    "canteen": {
        "build_form": "与社区文化活动中心、党群服务中心或养老设施合署，共用厨房与就餐区",
        "space_hint": "工程建议：就餐区 150–300 m²，另计厨房与备餐间",
        "approval": "食品经营许可、消防；若面向老年人助餐还需民政备案",
    },
    "kindergarten": {
        "build_form": "独立用地或小区配套园，须有独立出入口、室外活动场地，不得与商业混层",
        "space_hint": "工程建议：按班级数定规模，室外活动场地人均不低于 2 m²（以教育部门规定为准）",
        "approval": "教育部门办学许可、消防与抗震专项审查",
    },
    "culture_center": {
        "build_form": "优先与既有公共建筑（党群服务中心、文化馆分馆、学校体育馆）合署分时开放，"
                      "社区层面单独新建成本高",
        "space_hint": "工程建议：多功能活动室 + 阅览 + 排练，合计 300–800 m²，宜设在一至三层",
        "approval": "文旅/街道备案；若利用学校场地需教育部门同意并做开放时段隔离",
    },
    "fitness": {
        "build_form": "利用小区内部空地、屋顶或桥下空间设置健身点与步道，成本最低、见效最快",
        "space_hint": "工程建议：健身点 100–200 m²，步道宽度不低于 1.5 m",
        "approval": "属地街道备案；占用绿地或消防通道需专项审批",
    },
    "park": {
        "build_form": "口袋公园/街角绿地改造，或与河道蓝线、单位附属绿地开放共享结合",
        "space_hint": "工程建议：300–1000 m²，含遮荫、座椅与夜间照明",
        "approval": "绿化部门审批（占用绿地须占补平衡）",
    },
}


def main() -> int:
    path = repo_path("config/elements.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    added, skipped = [], []
    for el in cfg["elements"]:
        note = NOTES.get(el["key"])
        if not note:
            continue
        if el.get("build_notes"):
            skipped.append(el["key"])
            continue
        el["build_notes"] = {**note, "caveat": CAVEAT}
        added.append(el["key"])
    if added:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"added": added, "already_present": skipped,
                      "file": str(path.relative_to(ROOT))}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
