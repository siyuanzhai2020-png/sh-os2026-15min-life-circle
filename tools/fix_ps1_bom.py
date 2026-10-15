"""给 tasks.ps1 补回 UTF-8 BOM（幂等）。

    python tools/fix_ps1_bom.py

为什么需要它：Windows PowerShell 5.1 在没有 BOM 时按系统 ANSI(GBK) 解析 .ps1，
文件里的中文注释会被拆成乱码并报 `Unexpected token '{'` —— 而绝大多数编辑器
（以及很多 diff/补丁工具）保存时会把 BOM 去掉。改完 tasks.ps1 跑一下本脚本即可。

`tools/lint_style.py` 也会检查这一点，漏了会在自检里报出来。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"
TARGETS = ["tasks.ps1"]


def main() -> int:
    changed = 0
    for name in TARGETS:
        p = ROOT / name
        if not p.exists():
            print(f"跳过（不存在）：{name}")
            continue
        raw = p.read_bytes()
        if raw.startswith(BOM):
            print(f"已有 BOM：{name}")
            continue
        p.write_bytes(BOM + raw.lstrip(BOM))
        print(f"已补 BOM：{name}")
        changed += 1
    print("完成" if not changed else f"修正 {changed} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
