"""push 前最终闸门：扫描**即将进仓库的内容**里有没有密钥。

    python tools/check_no_secrets.py

扫描对象按优先级取：
1. `git show :<path>` —— **暂存区**的 blob（有暂存内容时）。这是 push 前的正确目标：
   只有暂存区里的东西才会被 commit。
2. 暂存区为空时退回扫描 `HEAD` 的提交树 —— 这样它也能安全地放进 `verify` 链里跑
   （提交之后暂存区本来就是空的，不能因此判失败）。

同时检查通用密钥特征（百度 AK/SK、私钥、各类 token）。返回码非 0 表示发现疑似密钥，
此时**不要 push**。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 通用特征（不依赖本机 .env.local，换台机器也能查出问题）
PATTERNS = [
    (re.compile(rb"\b[0-9A-Fa-f]{24}\b"), "疑似百度 AK（24 位十六进制）"),
    (re.compile(rb"\b[0-9A-Za-z]{32}\b"), "疑似 32 位 key/sk"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥"),
    (re.compile(rb"sk-[A-Za-z0-9]{20,}"), "疑似 OpenAI 风格 token"),
    (re.compile(rb"ghp_[A-Za-z0-9]{20,}"), "GitHub personal token"),
]
# 出现这些词的文件才细看，降低误报（本仓库里 24 位十六进制最可能出现在别处）
SKIP_SUFFIX = (".png", ".jpg", ".zip", ".ico", ".bmp")
# 白名单：这些文件里出现 32/24 位串是正常的（哈希、示例值、GeoJSON id）
ALLOW = {
    ".gitignore", ".gitattributes", "LICENSE",
    "lifecycle_metrics/basemap.py",      # 里面有百度 zoom 常量与说明
}


def staged_files():
    # 用 -z 拿 NUL 分隔的原始字节：文件名是中文时，默认按系统 ANSI 解码会炸
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def head_files():
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                         cwd=ROOT, capture_output=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def blob(path: str, rev: str = ":") -> bytes:
    r = subprocess.run(["git", "show", f"{rev}{path}"], cwd=ROOT, capture_output=True)
    return r.stdout


def main() -> int:
    files = staged_files()
    rev, where = ":", "暂存区"
    if not files:
        # 已提交（或仓库还没有提交）时退回提交树：verify 链在 commit 之后跑，暂存区是空的
        files = head_files()
        rev, where = "HEAD:", "HEAD 提交树"
    if not files:
        print("没有可扫描的内容（仓库还没有任何提交）—— 跳过")
        return 0
    print(f"扫描{where} {len(files)} 个文件…")
    hits = []
    for f in files:
        if f.endswith(SKIP_SUFFIX) or f in ALLOW:
            continue
        data = blob(f, rev)
        for pat, label in PATTERNS:
            for m in pat.finditer(data):
                s = m.group(0)
                # 32 位纯十六进制多半是哈希；只报"看着像 key 的"（含大小写混合或很长）
                if label == "疑似 32 位 key/sk" and re.fullmatch(rb"[0-9a-f]{32}", s):
                    continue
                line = data[:m.start()].count(b"\n") + 1
                hits.append(f"  ✘ {f}:{line}  {label}  {s[:12].decode('utf-8', 'replace')}…")
    if hits:
        print("\n".join(hits[:40]))
        print(f"发现 {len(hits)} 处疑似密钥 —— **不要 push**，先确认并清理。")
        return 1
    print("未发现密钥特征 ✔（.env.local 等已被 .gitignore 排除，不在此列）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
