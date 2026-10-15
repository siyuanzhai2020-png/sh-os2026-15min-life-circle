"""给已有数据集里的居住组团挂上真实小区名（**不重跑 Overpass/百度 POI 检索**）。

    python tools/name_clusters.py [community_id ...] [--dry-run]

背景：OSM 只给得出"居住组团 37"这种通用名（聚合后的兜底名），报告和"一天动线"里
没法直接说清是哪个小区。百度地点检索的「住宅区 / 小区」POI 里就有"金菊小区""元丰花园社区"
这类名字，用最近的 POI 给组团命名即可。

只改 `residential[].name` 与 `tags.named_from`，不动户数/路网/设施，
所以不会影响任何指标（跑完可直接对比 diagnose.json 的 summary 自证）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.config import load_communities, repo_path           # noqa: E402
from lifecycle_metrics.env import baidu_credentials, load_env_file         # noqa: E402
from lifecycle_metrics.providers.baidu import (_collect_baidu_residential,  # noqa: E402
                                               name_clusters_from_poi, search_area)
from lifecycle_metrics.schema import load_dataset, save_dataset            # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="居住组团实名（用百度住宅区 POI）")
    ap.add_argument("community_ids", nargs="*", help="缺省=配置里所有 baidu+osm 社区")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    args = ap.parse_args(argv)

    load_env_file()
    ak = baidu_credentials()["ak"]
    if not ak:
        print("缺少服务端 AK（.env.local 的 BAIDU_SERVER_AK），无法检索小区名", file=sys.stderr)
        return 2

    cfg = load_communities()
    wanted = set(args.community_ids)
    summary = []
    for community in cfg["communities"]:
        cid = community["id"]
        if wanted and cid not in wanted:
            continue
        if community.get("provider") != "baidu":
            continue                                   # 合成数据集没有百度 POI 可挂
        path = repo_path(community["dataset"])
        if not path.exists():
            print(f"[skip] {cid}: 数据集不存在 {path.name}")
            continue
        ds = load_dataset(path)
        generic = [r for r in ds.residential if r.name.startswith("居住组团")]
        location, radius_m = search_area(community)
        pois = _collect_baidu_residential(ak, location, radius_m, None, repo_path("data") / "cache")
        before = {r.id: r.name for r in ds.residential}
        named = name_clusters_from_poi(ds.residential, pois)
        renamed = [(r.id, before[r.id], r.name) for r in ds.residential if before[r.id] != r.name]
        ds.meta["residential_named_from_poi"] = named
        if not args.dry_run and named:
            save_dataset(ds, path)
        summary.append({"community": cid, "name": community["name"],
                        "clusters": len(ds.residential), "generic_before": len(generic),
                        "named": named, "poi_candidates": len(pois),
                        "saved": bool(named) and not args.dry_run,
                        "samples": [{"id": i, "from": a, "to": b} for i, a, b in renamed[:8]]})
    print(json.dumps({"ok": True, "results": summary}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
