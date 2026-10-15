"""检查文档里指向"被 .gitignore 排除的路径"的链接 —— 这些在 GitHub 上会是死链。

    python tools/check_doc_links.py

只查仓库内的相对链接（http/https 跳过）：把它们解析成真实路径，
不存在就报出来。开发时本地有 data/、out/、dist/，所以这类死链在本地看不出来，
一推到 GitHub 就暴露了。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# 文档里"示例性提到"的路径不算死链：出图产物、数据集、打包物
IGNORE_PREFIX = ("out/", "dist/", "data/", "./out/", "./dist/", "./data/")
SKIP_SCHEME = ("http://", "https://", "mailto:", "#")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def main() -> int:
    files = [f for f in tracked() if f.endswith((".md", ".html"))]
    problems = []
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        for m in LINK.finditer(text):
            raw = m.group(1).strip()
            if not raw or raw.startswith(SKIP_SCHEME):
                continue
            target = raw.split("#")[0].strip()
            if not target or target.startswith(IGNORE_PREFIX) or target.startswith("/"):
                continue
            base = (ROOT / f).parent
            path = (base / target).resolve()
            try:
                rel = path.relative_to(ROOT)
            except ValueError:
                problems.append(f"{f}: 链接跑到仓库外 —— {raw}")
                continue
            if not path.exists():
                problems.append(f"{f}: 死链 —— {raw}")
            elif str(rel).replace("\\", "/") not in tracked() and path.is_file():
                problems.append(f"{f}: 链接指向未跟踪的文件（GitHub 上不存在）—— {raw}")

    print(f"检查 {len(files)} 个文档，{len(problems)} 处问题")
    for p in problems:
        print("  ✘", p)
    if not problems:
        print("文档内链检查通过 ✔")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
