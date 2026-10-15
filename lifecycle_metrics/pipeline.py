"""数据集装配流水线：加载/构建 → 实测户数覆盖 → 人口总量校准 → 落盘。

为什么要有这一层：
1. 户数有三个可信度层级 —— 实测（CSV 覆盖）> 普查总量校准 > OSM 建筑估算。
   引擎只认户数，但报告必须说清这些数字是怎么来的，所以校准信息全部写进 meta。
2. `tools/build_demo.py`、`tools/serve.py`、`tools/add_community.py` 共用同一套装配逻辑，
   避免"命令行跑出来的和网页看到的不是同一份数据"。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import repo_path
from .geo import haversine_m
from .providers import get_provider
from .schema import Dataset

HOUSEHOLD_CSV_DIR = "data/households"
NAME_MATCH_LIMIT = 8      # 名称匹配时允许的最短公共前缀
COORD_MATCH_M = 200.0     # 坐标匹配半径


def _rel(path: Path) -> str:
    """尽量给出相对仓库的路径；文件在仓库外（如测试的临时目录）时原样返回。"""
    try:
        return str(Path(path).relative_to(repo_path(".")))
    except ValueError:
        return str(path)


def _restore_base_households(ds: Dataset) -> int:
    """把户数还原到"未校准/未覆盖"的原始值。

    必要性：数据集是带缓存复用的，若直接在已校准的户数上再乘一次系数，会复利缩放
    （实测踩过：77,745 → 再乘 0.87 = 67,543）。所以每次装配前先还原原始值。
    """
    restored = 0
    for r in ds.residential:
        tags = r.tags or {}
        base = tags.get("households_before_calibration") or tags.get("households_before")
        if base:
            r.households = int(base)
            restored += 1
    return restored


def _apply_household_overrides(ds: Dataset, community_id: str) -> Dict[str, Any]:
    """用实测户数 CSV 覆盖居住组团户数。

    CSV 格式（表头必须含 name,households；lon/lat 可选）：
        name,households,lon,lat
        曹杨一村,1820,121.4041,31.2417
    匹配规则：有经纬度则按最近组团（≤200 m）；否则按名称包含匹配；两者都失败则记入 unmatched。
    """
    path = repo_path(HOUSEHOLD_CSV_DIR) / f"{community_id}.csv"
    if not path.exists():
        return {"applied": False, "reason": f"未提供 {_rel(path)}"}

    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("name") and row.get("households"):
                rows.append(row)

    matched, unmatched = 0, []
    for row in rows:
        try:
            households = int(float(str(row["households"]).replace(",", "")))
        except ValueError:
            unmatched.append(row.get("name", "?"))
            continue
        target = None
        lon, lat = row.get("lon"), row.get("lat")
        if lon and lat:
            try:
                rlon, rlat = float(lon), float(lat)
                best = min(ds.residential,
                           key=lambda r: haversine_m(r.lon, r.lat, rlon, rlat), default=None)
                if best is not None and haversine_m(best.lon, best.lat, rlon, rlat) <= COORD_MATCH_M:
                    target = best
            except ValueError:
                target = None
        if target is None:
            name = str(row["name"]).strip()
            target = next((r for r in ds.residential
                           if name and (name in r.name or r.name in name)), None)
        if target is None:
            unmatched.append(str(row.get("name")))
            continue
        target.tags = {**(target.tags or {}), "households_source": "measured_csv"}
        target.tags.setdefault("households_before", target.households)
        target.households = max(1, households)
        matched += 1

    return {"applied": matched > 0, "source": _rel(path),
            "rows": len(rows), "matched": matched,
            "unmatched": unmatched[:20], "unmatched_count": len(unmatched)}


def _apply_population_calibration(ds: Dataset, cfg: Dict[str, Any],
                                  elements_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """按普查总量校准户数（结构沿用数据源估算，总量对齐权威口径）。

    两种给定方式：
    - `target_population`：直接给分析范围内的目标常住人口
    - `density_per_km2` + `extent_m`：用街道人口密度 × 分析范围面积推算（推荐，来源更硬）
    """
    cal = cfg.get("population_calibration")
    if not cal:
        return {"applied": False, "reason": "未配置 population_calibration"}

    hh_size = elements_cfg.get("avg_household_size", 2.6)
    target_pop = cal.get("target_population")
    basis = cal.get("basis", "")
    if not target_pop and cal.get("density_per_km2"):
        e = cfg["extent_m"]
        area_km2 = (2 * e["half_width"] / 1000.0) * (2 * e["half_height"] / 1000.0)
        target_pop = cal["density_per_km2"] * area_km2
        basis = basis or (f"{cal['density_per_km2']:.0f} 人/km² × 分析范围 {area_km2:.2f} km²")
    if not target_pop:
        return {"applied": False, "reason": "缺少 target_population / density_per_km2"}

    before = ds.total_households()
    if before <= 0:
        return {"applied": False, "reason": "数据集中没有居住组团"}

    before_pop = before * hh_size
    factor = float(target_pop) / before_pop
    for r in ds.residential:
        # 始终以"首次记录到的原始户数"为基准，重复装配也不会复利缩放
        base = int((r.tags or {}).get("households_before_calibration", r.households))
        r.tags = {**(r.tags or {}), "households_before_calibration": base,
                  "calibration_factor": round(factor, 4)}
        r.households = max(1, int(round(base * factor)))
    after = ds.total_households()
    return {"applied": True, "mode": "total_population",
            "target_population": round(float(target_pop)),
            "population_before": round(before_pop),
            "population_after": round(after * hh_size),
            "factor": round(factor, 4), "basis": basis,
            "sources": cal.get("sources", []), "caveat": cal.get("caveat", "")}


def assemble_dataset(community: Dict[str, Any], elements_cfg: Dict[str, Any],
                     rebuild: bool = False) -> Tuple[Dataset, Dict[str, Any]]:
    """加载或构建数据集，并施加"实测覆盖 → 总量校准"，返回 (dataset, 处理报告)。"""
    from .schema import load_dataset, save_dataset

    ds, source = None, ""
    dataset_path = repo_path(community["dataset"])
    if dataset_path.exists() and not rebuild:
        ds = load_dataset(dataset_path)
        source = f"缓存 {_rel(dataset_path)}"
    else:
        provider = get_provider(community.get("provider", "synthetic"))
        ds = provider(community)
        source = f"生成（provider={community.get('provider', 'synthetic')}）"

    restored = _restore_base_households(ds)     # 先还原原始户数，避免复利缩放
    overrides = _apply_household_overrides(ds, community["id"])
    # 有实测覆盖时不再做总量校准（实测优先，避免把实测数据又缩放回去）
    if overrides.get("applied"):
        calibration = {"applied": False,
                       "reason": "已使用实测户数 CSV，跳过总量校准（实测优先）"}
    else:
        calibration = _apply_population_calibration(ds, community, elements_cfg)

    ds.meta = {**(ds.meta or {}), "household_overrides": overrides,
               "population_calibration": calibration,
               "households_restored_to_base": restored,
               "dataset_built_from": source}
    save_dataset(ds, dataset_path)
    return ds, {"source": source, "household_overrides": overrides,
                "population_calibration": calibration,
                "households_restored_to_base": restored}


def summarize_household_provenance(ds: Dataset) -> str:
    """给报告/界面用的一句话口径说明。"""
    meta = ds.meta or {}
    ov = meta.get("household_overrides") or {}
    cal = meta.get("population_calibration") or {}
    parts = []
    if ov.get("applied"):
        parts.append(f"实测户数覆盖 {ov.get('matched')}/{ov.get('rows')} 条（{ov.get('source')}）")
    elif cal.get("applied"):
        parts.append(f"总量校准 ×{cal.get('factor')}（{cal.get('basis')}）")
    else:
        parts.append("使用数据源原始估算值")
    src = meta.get("residential_source") or meta.get("source")
    parts.append(f"空间结构来自 {src}")
    return "；".join(parts)
