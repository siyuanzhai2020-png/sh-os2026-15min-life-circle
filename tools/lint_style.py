"""代码规范自检（零依赖，进 verify 链）。

    python tools/lint_style.py            # 只报错
    python tools/lint_style.py --fix      # 能自动修的顺手修掉（行尾空格 / 缺末尾换行 / CRLF）

规则（都是这个项目实际需要、且能自动核验的）：

1. **行长 ≤ 110**（Python / JS / CSS / PS1 / HTML）。中文注释按"字符数"而非"显示宽度"算，
   和编辑器的列号一致。
2. **行尾不留空格**。
3. **文件以换行结尾**。
4. **换行符统一 LF**；例外：`tasks.ps1` 必须带 UTF-8 BOM，`.cmd` / `.bat` 必须**纯 ASCII**
   （Windows PowerShell 5.1 与 cmd.exe 的编码坑，见 README「脚本编码」与 .gitattributes）。
5. **BOM 只允许出现在 `tasks.ps1`**。
6. **不使用制表符缩进**。
7. **不使用 `TODO` / `FIXME` / `XXX` / `HACK`** —— 这个项目把"待办"写在 docs/ 里，
   不留悬空标记（免得读者不知道谁在什么时候该做）。

排除：`data/cache*`、`out/`、`dist/`、`__pycache__`、`.venv`、`.env*`（含密钥）。
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

MAX_LEN = 110
# 前端放宽到 120：JS 模板字符串里嵌的是整段 HTML，硬折会切开标签、可读性反而更差
MAX_LEN_WEB = 120
WEB_SUFFIX = {".js", ".css", ".html"}
CODE_SUFFIX = {".py", ".js", ".css", ".html", ".ps1", ".cmd", ".json", ".toml", ".txt", ".yml"}
# .json 是数据文件：里面的长串是**一个不可拆分的字符串值**，换行会改变内容，所以只对它们
# 检查换行符与行尾空白，不查行长。
NO_LEN_SUFFIX = {".json"}
# 本文件自己要写出这几个标记名才能检查它们
NO_MARKER_FILES = {"tools/lint_style.py"}

EXCLUDE_DIRS = ["data/cache*", "data/cache-diag", "out", "dist", ".git", "__pycache__",
                ".venv", "venv", "node_modules", ".idea", ".vscode"]
EXCLUDE_FILES = [".env", ".env.local", ".env.local.example", "*.pyc", "data/*.dataset.json"]
# 这些文件的行尾与 BOM 由历史原因固定，不参与"统一 LF / 无 BOM"检查
KEEP_BOM = {"tasks.ps1"}
KEEP_CRLF = {"启动演示.cmd", "跑检查.cmd"}
MARKERS = ("TODO", "FIXME", "XXX", "HACK")


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        if any(fnmatch.fnmatch(prefix, d) for d in EXCLUDE_DIRS):
            return True
    return any(fnmatch.fnmatch(rel, f) for f in EXCLUDE_FILES)


def targets() -> List[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if excluded(rel):
            continue
        if p.suffix.lower() not in CODE_SUFFIX and p.name not in (".gitattributes", ".gitignore"):
            continue
        out.append(p)
    return out


def check(path: Path) -> List[str]:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    raw = path.read_bytes()
    problems: List[str] = []

    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom and rel not in KEEP_BOM:
        problems.append(f"{rel}: 不该有 UTF-8 BOM")
    if not has_bom and rel in KEEP_BOM:
        problems.append(f"{rel}: 必须带 UTF-8 BOM（PowerShell 5.1 否则按 GBK 解析）")

    body = raw[3:] if has_bom else raw
    has_crlf = b"\r\n" in body
    if rel.lower().endswith((".cmd", ".bat")):
        # .cmd 的真正陷阱不是行尾，是**编码**：cmd.exe 按控制台 ANSI 码页读批处理，
        # UTF-8 中文会被拆成乱码并当成命令执行。必须纯 ASCII。
        try:
            body.decode("ascii")
        except UnicodeDecodeError:
            problems.append(f"{rel}: .cmd/.bat 必须纯 ASCII（cmd.exe 按 ANSI 码页读批处理）")
    elif rel in KEEP_CRLF:
        if not has_crlf:
            problems.append(f"{rel}: 必须是 CRLF")
    elif has_crlf:
        problems.append(f"{rel}: 出现 CRLF，应为 LF")

    text = body.decode("utf-8", "replace").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    elif text and not text.endswith("\n"):
        problems.append(f"{rel}: 末尾缺换行")

    limit = MAX_LEN_WEB if path.suffix.lower() in WEB_SUFFIX else MAX_LEN
    for i, line in enumerate(lines, 1):
        if len(line) > limit and path.suffix.lower() not in NO_LEN_SUFFIX:
            problems.append(f"{rel}:{i}: 行长 {len(line)} > {limit}")
        if line.rstrip() != line:
            problems.append(f"{rel}:{i}: 行尾有空白")
        if line.lstrip().startswith("\t"):
            problems.append(f"{rel}:{i}: 用制表符缩进")
        if rel not in NO_MARKER_FILES:
            for m in MARKERS:
                if m in line:
                    problems.append(f"{rel}:{i}: 出现 {m} 标记")
    return problems


def fix(path: Path) -> bool:
    """只修行尾空格、末尾换行、CRLF 这三类安全项；行长要靠人改。"""
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    if rel in KEEP_CRLF or rel in KEEP_BOM:
        return False
    text = body.decode("utf-8", "replace").replace("\r\n", "\n")
    lines = [l.rstrip() for l in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    new = ("\n".join(lines) + "\n").encode("utf-8")
    if new == body:
        return False
    path.write_bytes(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="代码规范自检")
    ap.add_argument("--fix", action="store_true", help="自动修行尾空格 / 末尾换行 / CRLF")
    args = ap.parse_args()

    files = targets()
    if args.fix:
        changed = [str(p.relative_to(ROOT)) for p in files if fix(p)]
        if changed:
            print("已自动修正：", ", ".join(changed))

    problems: List[str] = []
    for p in files:
        problems += check(p)

    by_file: Dict[str, int] = {}
    for p in problems:
        by_file[p.split(":")[0]] = by_file.get(p.split(":")[0], 0) + 1
    print(f"检查 {len(files)} 个文件，{len(problems)} 处问题")
    for p in problems[:80]:
        print("  ✘", p)
    if len(problems) > 80:
        print(f"  … 还有 {len(problems) - 80} 处")
    if not problems:
        print("代码规范自检通过 ✔")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
