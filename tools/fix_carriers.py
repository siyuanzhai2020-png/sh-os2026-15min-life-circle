"""就地修正已有数据集里的载体表（离线，不联网、不吃百度额度）。

    python tools/fix_carriers.py [community_id ...]

修什么：早期版本把地铁**出入口/通道口** POI 当成了载体，而且只用 120 m 去重 ——
同一座站的多个口往往隔得更远，于是同一座站被算成 2–3 个独立载体：
候选环带在同一位置叠好几遍、tier1 的 1.5 倍系数被重复放大，
点位卡片还会写成"依托 行知路地铁站-1口"，读起来像要把设施建在地铁口里面。

现在按 `cluster_stations()` 归并：一座站只留一条，站体 POI 优先当代表，
站名归一（"行知路地铁站-1口" → "行知路地铁站"），出入口数量记进 name。

只改 `data/*.dataset.json` 里的 `meta.carriers` 与 `meta.carrier_counts`，
不动路网/设施/户数；改完重跑 optimize 与报告即可。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import load_communities, repo_path          # noqa: E402
from lifecycle_metrics.providers.baidu import (CARRIER_META,             # noqa: E402
                                               cluster_stations,
                                               is_valid_station_carrier)


def fix_dataset(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    meta = raw.setdefault("meta", {})
    carriers: List[Dict[str, Any]] = meta.get("carriers") or []
    if not carriers:
        return {"changed": False, "reason": "无载体表"}

    stations = [c for c in carriers if c.get("kind") == "tier1_transit"]
    dropped = [c for c in stations if not is_valid_station_carrier(c.get("name", ""))]
    stations = [c for c in stations if is_valid_station_carrier(c.get("name", ""))]
    others = [c for c in carriers if c.get("kind") != "tier1_transit"]
    merged = cluster_stations(stations)
    # carrier_meta 是建数据集时一起写进文件的：梯队口径（邻近/合署、注释）改了必须同步刷新，
    # 否则界面读的还是文件里的旧文案
    meta_stale = meta.get("carrier_meta") != CARRIER_META
    if (len(merged) == len(stations) and not dropped and not meta_stale
            and all(m.get("name") == s.get("name") for m, s in zip(merged, stations))):
        return {"changed": False, "reason": "已经是归并后的结果"}

    meta["carriers"] = others + merged
    meta["carrier_meta"] = CARRIER_META
    meta["carrier_counts"] = {k: sum(1 for c in meta["carriers"] if c.get("kind") == k)
                              for k in CARRIER_META}
    meta["carrier_fix_note"] = ("地铁载体已按站点归并（出入口/通道口 POI 不再单列），"
                                "见 tools/fix_carriers.py 与 docs/metrics.md")
    bak = path.with_suffix(".json.bak")
    if not bak.exists():                 # 只保留第一次（未修改前）的备份
        shutil.copy2(path, bak)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return {"changed": True, "before": len(stations) + len(dropped), "after": len(merged),
            "dropped": [c.get("name") for c in dropped],
            "names_before": [c.get("name") for c in stations],
            "names_after": [c.get("name") for c in merged]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="就地归并数据集里的地铁载体")
    ap.add_argument("community_ids", nargs="*")
    args = ap.parse_args(argv)
    wanted = set(args.community_ids)

    for c in load_communities()["communities"]:
        cid = c["id"]
        if wanted and cid not in wanted:
            continue
        path = repo_path(c["dataset"])
        if not path.exists():
            continue
        r = fix_dataset(path)
        if not r.get("changed"):
            print(f"{cid:16s} 跳过（{r.get('reason')}）")
            continue
        print(f"{cid:16s} tier1 载体 {r['before']} → {r['after']}")
        for n in r["names_before"]:
            print(f"                    - {n}")
        for n in r["names_after"]:
            print(f"                    + {n}")
        for n in r.get("dropped", []):
            print(f"                    × {n}（站内设施/蹭词商家，已剔除）")
    print("\n下一步：python tools/build_demo.py <社区> 重算选区与报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
