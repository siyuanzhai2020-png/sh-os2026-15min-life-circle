"""给已有数据集补连"地图上没接上的断头路"（纯本地：不请求百度，也不需要 Overpass）。

    python tools/stitch_network.py [community_id ...] [--dry-run] [--max-m 60]

为什么需要：OSM 里小区内部路常常画到离大马路几十米处就停了，我们的建图只按
"端点坐标重合"合并，于是这段路成了孤岛；岛上的组团会"一处设施都到不了"，
被判成 0 分 —— 那是数据缺口，不是设施缺口。本脚本把断头端点就近补连（跨水不连），
并把每一处补连写进 meta.stitched_edges 备查。

只增删 `edges`（新增 kind=connector 的边）与 `meta.stitched_edges`，
不动节点/组团/设施/户数；跑完可对比 diagnose.json 的 summary 自证变化范围。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import load_communities, repo_path             # noqa: E402
from lifecycle_metrics.network import analyze_network, stitch_components     # noqa: E402
from lifecycle_metrics.graph import RoadGraph                                # noqa: E402
from lifecycle_metrics.schema import load_dataset, save_dataset              # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="路网断头路补连（本地离线）")
    ap.add_argument("community_ids", nargs="*", help="缺省=全部社区")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--max-m", type=float, default=60.0, help="补连长度上限（米）")
    args = ap.parse_args(argv)

    wanted = set(args.community_ids)
    results = []
    for community in load_communities()["communities"]:
        cid = community["id"]
        if wanted and cid not in wanted:
            continue
        path = repo_path(community["dataset"])
        if not path.exists():
            continue
        ds = load_dataset(path)
        before = analyze_network(ds, RoadGraph.from_dataset(ds))
        new_edges, stitches = stitch_components(
            ds.nodes, ds.edges,
            water_polygons=ds.meta.get("water_polygons") or [],
            water_lines=ds.meta.get("water_lines") or [],
            max_m=args.max_m)
        ds.edges = new_edges
        if stitches:
            ds.meta["stitched_edges"] = stitches
        # 已经补连过的数据集再跑一次：连通块只剩 1 个 → stitches 为空，此时保留原有记录
        elif not ds.meta.get("stitched_edges"):
            ds.meta["stitched_edges"] = []
        after = analyze_network(ds, RoadGraph.from_dataset(ds))
        if stitches and not args.dry_run:
            save_dataset(ds, path)
        results.append({
            "community": cid, "name": community["name"],
            "edges": f"{len(new_edges)} (+{len(stitches)})",
            "components": f"{before['components']} → {after['components']}",
            "isolated_points": f"{len(before['isolated_points'])} → {len(after['isolated_points'])}",
            "isolated_households": f"{before['isolated_households']} → {after['isolated_households']}",
            "saved": bool(stitches) and not args.dry_run,
            "stitch_samples": [f"{s['from']}↔{s['to']} {s['length_m']}m"
                               for s in stitches if "from" in s][:4],
            "stitch_notes": [s["note"] for s in stitches if "note" in s],
        })
    print(json.dumps({"ok": True, "max_m": args.max_m, "results": results},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
