"""配置装载：elements.json（口径）与 communities.json（试点社区）的唯一读入口。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_elements(path: str | Path | None = None) -> Dict[str, Any]:
    return load_json(path or ROOT / "config" / "elements.json")


def load_communities(path: str | Path | None = None) -> Dict[str, Any]:
    return load_json(path or ROOT / "config" / "communities.json")


def load_personas(path: str | Path | None = None) -> Dict[str, Any]:
    """典型住户画像（规划助手的"一类人"）。缺失时返回空表，不影响体检功能。"""
    try:
        return load_json(path or ROOT / "config" / "personas.json")
    except FileNotFoundError:
        return {"personas": []}


def persona_by_key(personas: Dict[str, Any], key: str | None) -> Dict[str, Any] | None:
    if not key:
        return None
    for p in personas.get("personas", []):
        if p.get("key") == key:
            return p
    raise KeyError(f"personas.json 中没有画像 {key!r}")


def resolve_community(communities: Dict[str, Any], community_id: str | None = None) -> Dict[str, Any]:
    target = community_id or communities.get("active")
    for c in communities.get("communities", []):
        if c["id"] == target:
            return c
    raise KeyError(f"communities.json 中没有社区 {target!r}")


def required_elements(elements: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for e in elements["elements"] if e.get("required")]


def element_map(elements: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {e["key"]: e for e in elements["elements"]}


def repo_path(rel: str | Path) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def slugify_community(name: str, existing: List[str]) -> str:
    """社区名称 → 唯一 id。

    纯中文名没有可用的 ASCII 片段，用名称哈希保证稳定且可读（custom-1a2b3c），
    避免所有中文社区都挤成 custom-community-2/-3 这种无法辨认的 id。
    """
    import hashlib
    import re
    ascii_part = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if ascii_part:
        base = f"custom-{ascii_part}"[:40].strip("-")
    else:
        base = "custom-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    candidate, i = base, 2
    while candidate in existing:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def save_communities(cfg: Dict[str, Any]) -> Path:
    path = ROOT / "config" / "communities.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
