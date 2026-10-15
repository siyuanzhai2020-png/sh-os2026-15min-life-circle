"""读取 .env.local / .env（标准库实现，避免引入 python-dotenv）。

规则：只填充**尚未设置**的环境变量，不覆盖已有的 shell 变量；
密钥永远不写进代码或文档，仓库中 .env.local 已被 .gitignore 排除。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = (".env.local", ".env")


def parse_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_env_file(root: Path | str | None = None, override: bool = False) -> Dict[str, str]:
    base = Path(root) if root else ROOT
    loaded: Dict[str, str] = {}
    for name in CANDIDATES:
        for key, value in parse_env_file(base / name).items():
            loaded[key] = value
            if override or key not in os.environ or not os.environ[key]:
                os.environ[key] = value
    return loaded


def baidu_credentials() -> Dict[str, str | None]:
    """返回 {'ak': 服务端 AK, 'sk': SK, 'web_ak': 浏览器端 AK}。"""
    load_env_file()
    return {
        "ak": os.environ.get("BAIDU_SERVER_AK") or os.environ.get("BAIDU_AK"),
        "sk": os.environ.get("BAIDU_SK"),
        "web_ak": os.environ.get("BAIDU_WEB_AK"),
    }
