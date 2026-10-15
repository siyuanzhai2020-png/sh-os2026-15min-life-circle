"""给要素清单补"标准依据"字段（幂等）。

    python tools/add_standard_refs.py

**重要**：本脚本里的出处来自公开检索（检索结果见 source_url），**不是逐字核对标准原文得到的**，
所以每条都带 `verified: false`，界面上会显示"待核对原文"。正式提交前请用纸质/官方 PDF 原文
逐条核对并补页码，核对完把 verified 改成 true 即可（报告会自动去掉"待核对"标记）。

字段：
    level            配置层级（五分钟 / 十分钟 / 十五分钟生活圈居住区）
    doc              标准/文件名称
    requirement      应配置 / 宜配置（GB 50180-2018 附录B 用 ▲ 表示应配置）
    walk_distance_m  该层级对应的步行距离（GB 50180-2018 表 3.0.4：300 / 500 / 800–1000 m）
    building_area    建筑面积（只有检索到明确出处才填）
    people_per_unit  每处服务人口（仅当出处给出"每 X 万人设一处"时填）
    note             口径说明
    source_url       检索到的公开出处
    verified         是否已人工核对原文
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import repo_path                            # noqa: E402

GB = "GB 50180-2018《城市居住区规划设计标准》附录B 居住区配套设施设置规定"
GB_WALK = ("GB 50180-2018 表 3.0.4 居住区分级控制规模：步行距离 五分钟 300 m / "
           "十分钟 500 m / 十五分钟 800–1000 m")
SH = "上海市《15 分钟社区生活圈行动·社区生活圈规划导则》（沪住建委）"
GB_URL = "https://gf.cabr-fire.com/article-28028.htm"
SH_URL = ("https://zjw.sh.gov.cn/cmsres/46/"
          "46a920ce8f904888a1535f7d40a7be5f/b7f3c903e57f0d13d0fc99a4400c033f.pdf")

PENDING = "出处来自公开检索（见 source_url），正式提交前须逐条核对标准原文并补页码"

REFS = {
    "market": {
        "level": "五分钟生活圈居住区", "doc": GB, "requirement": "应配置（社区商业网点）",
        "walk_distance_m": 300, "building_area": None, "people_per_unit": None,
        "note": "本项目口径：五个分钟生活圈内的菜市场/生鲜菜场，按步行 15 分钟覆盖判定（更宽）",
    },
    "community_health": {
        "level": "五～十分钟生活圈居住区", "doc": GB, "requirement": "应配置",
        "walk_distance_m": 300, "building_area": "每处 120–270 m²",
        "people_per_unit": None,
        "note": "建筑面积口径来自青岛市控规文件转引 GB 50180-2018 与 TD/T 1062-2021",
        "source_url": "http://www.qingdao.gov.cn/zwgk/xxgk/zygh/gkml/gwfg/xzgw/202504/"
                      "P020250425407414542328.pdf",
    },
    "canteen": {
        "level": "五～十五分钟生活圈居住区（上海：社区食堂/老年助餐点）", "doc": SH,
        "requirement": "上海导则鼓励设置社区食堂与助餐点", "walk_distance_m": 500,
        "building_area": "社区综合为老服务中心一般不小于 1000 m²（含助餐功能）",
        "people_per_unit": None,
        "note": "社区食堂/助餐点属上海地方口径，国标未单列该条目；面积口径来自区级文件",
        "source_url": "https://www.jingan.gov.cn/main/3d6e0059-ee66-43ab-a007-37521ce67285/"
                      "90a91934-0b16-42c8-9c09-c22ab478f30f/决策草案及说明.pdf",
    },
    "pharmacy": {
        "level": "五分钟生活圈居住区", "doc": GB, "requirement": "应配置（社区商业网点）",
        "walk_distance_m": 300, "building_area": None, "people_per_unit": None,
        "note": "药店属社区商业网点类，国标按「应配置」处理，未规定单体面积",
    },
    "kindergarten": {
        "level": "五分钟生活圈居住区", "doc": "《幼儿园建设标准》建标 175-2016（配套 " + GB + "）",
        "requirement": "应配置", "walk_distance_m": 300,
        "building_area": "3 班（服务 3000 人）796–1009 m²；6 班（3001–6000 人）2193–2665 m²",
        "people_per_unit": "3 班园约 3000 人 / 6 班园约 6000 人",
        "note": "面积与班数口径来自《幼儿园建设标准》",
        "source_url": "http://www.yuxi.gov.cn/u/cms/yxszfxxgk/202412/19094828h9va.pdf",
    },
    "primary_school": {
        "level": "十分钟生活圈居住区", "doc": GB, "requirement": "应配置",
        "walk_distance_m": 500, "building_area": None, "people_per_unit": None,
        "note": "小学由区级教育部门统筹布点，社区层面无法自行选址（本项目 sitable=false 的依据）",
    },
    "culture_center": {
        "level": "十五分钟生活圈居住区（街道级）", "doc": GB + "；" + SH,
        "requirement": "应配置", "walk_distance_m": 900,
        "building_area": None, "people_per_unit": None,
        "note": "本项目按 15 分钟步行覆盖判定；上海导则强调一站式综合服务中心、功能复合",
        "source_url": SH_URL,
    },
    "fitness": {
        "level": "五分钟生活圈居住区（室外综合健身场地）", "doc": GB,
        "requirement": "应配置", "walk_distance_m": 300, "building_area": None,
        "people_per_unit": None,
        "note": "本项目把健身点/步道/球场合并为一类，按 15 分钟覆盖判定（更宽）",
    },
    "park": {
        "level": "五分钟 / 十五分钟生活圈居住区（公共绿地）", "doc": GB + "；" + SH,
        "requirement": "应配置", "walk_distance_m": 500, "building_area": None,
        "people_per_unit": None,
        "note": "上海口径常表述为「300 m 见绿、500 m 见园」，本项目按 15 分钟步行覆盖判定",
        "source_url": SH_URL,
    },
    "bus_stop": {
        "level": "五分钟生活圈居住区（公共交通车站）", "doc": GB, "requirement": "应配置",
        "walk_distance_m": 300, "building_area": None, "people_per_unit": None,
        "note": "公交站点由公交企业/交通部门布设，社区无法自行选址（sitable=false）",
    },
    "metro": {
        "level": "十五分钟生活圈居住区（公共交通车站）", "doc": GB, "requirement": "应配置",
        "walk_distance_m": 900, "building_area": None, "people_per_unit": None,
        "note": "轨道交通站点由市级规划决定，本项目只体检不处方（sitable=false）",
    },
    "convenience": {
        "level": "五分钟生活圈居住区（社区商业网点）", "doc": GB + "；上海区级任务清单",
        "requirement": "宜配置", "walk_distance_m": 300,
        "building_area": "生活服务中心类一般规模约 100 m²/处",
        "people_per_unit": "约 0.5 万人设一处",
        "note": "非必需要素：本项目计入体检展示但不计入达标要求",
        "source_url": "https://www.shcm.gov.cn/shcm/c1186da3-53a0-49df-9859-2fa4d1ec5395/",
    },
}


def main() -> int:
    path = repo_path("config/elements.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    added, skipped = [], []
    for el in cfg["elements"]:
        ref = REFS.get(el["key"])
        if not ref:
            continue
        if el.get("standard_ref"):
            skipped.append(el["key"])
            continue
        el["standard_ref"] = {**ref,
                              "walk_basis": GB_WALK,
                              "source_url": ref.get("source_url", GB_URL),
                              "verified": False,
                              "check_note": PENDING}
        added.append(el["key"])
    if added:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"added": added, "already_present": skipped,
                      "file": str(path.relative_to(ROOT))}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
