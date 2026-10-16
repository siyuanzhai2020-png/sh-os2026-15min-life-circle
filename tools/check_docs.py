"""文档规范自检：章节编号连续性、交叉引用有效性、文件格式与措辞。

零依赖，可在仓库根目录直接运行：

    python tools/check_docs.py

检查项
1. 文件格式：UTF-8 无 BOM、LF 行尾、末尾有换行、无行尾空白；
2. 章节编号：顶层 `## N.` 从 1 连续递增且不重复，子节 `### N.M` 归属正确、序号连续；
3. 交叉引用：`§N` / `§N.M` 必须能在同一文件或同一行引用的文档中找到对应章节；
4. 措辞：不出现口语词与 emoji（对外文本统一口径见 docs/metrics.md §16）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"out", "dist", "data", ".git", "node_modules", "__pycache__"}

# 口语词与装饰符号（对外文本统一口径见 docs/metrics.md §16）
BANNED_WORDS = ["坑", "翻车", "巨无霸", "一眼看懂", "说白了", "顺带", "病灶", "蹭词", "踩过"]
# 只拦图形化 emoji；箭头（→ ← ↔）与数学符号属正常技术排版，不在范围内
EMOJI_RANGES = ((0x231A, 0x231B), (0x23E9, 0x23FA), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0x1F000, 0x1FAFF), (0xFE0F, 0xFE0F))

SEC_TOP = re.compile(r"^##\s+(\d+)\.\s*(\S.*)$")
SEC_SUB = re.compile(r"^###\s+(\d+)\.(\d+)\s*(\S.*)$")
REF = re.compile(r"§\s*(\d+)(?:\.(\d+))?")
LINK_MD = re.compile(r"\]\(([^)]+\.md)\)")
PATH_MD = re.compile(r"([A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.md)")


def md_files() -> list[Path]:
    found = []
    for p in sorted(ROOT.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        found.append(p)
    return found


def parse(text: str) -> tuple[list[int], set[tuple[int, int]]]:
    """Return top-level section numbers and (section, subsection) pairs."""
    tops: list[int] = []
    subs: set[tuple[int, int]] = set()
    current = 0
    for line in text.splitlines():
        m = SEC_SUB.match(line)
        if m:
            subs.add((int(m.group(1)), int(m.group(2))))
            continue
        m = SEC_TOP.match(line)
        if m:
            current = int(m.group(1))
            tops.append(current)
    return tops, subs


def emoji_hits(text: str) -> list[str]:
    hits = []
    for ch in text:
        code = ord(ch)
        if any(lo <= code <= hi for lo, hi in EMOJI_RANGES):
            hits.append(ch)
    return sorted(set(hits))


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    raw = path.read_bytes()
    problems: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{rel}: 不应带 UTF-8 BOM")
    if b"\r\n" in raw:
        problems.append(f"{rel}: 行尾应为 LF")
    if not raw.endswith(b"\n"):
        problems.append(f"{rel}: 文件应以换行结尾")
    for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
        if line != line.rstrip():
            problems.append(f"{rel}:{i}: 行尾空白")
    return problems


def check_structure(path: Path, all_tops: dict[str, set[int]],
                    all_subs: dict[str, set[tuple[int, int]]]) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    tops, subs = parse(text)
    expect = 1
    for n in tops:
        if n != expect:
            problems.append(f"{rel}: 顶层章节编号不连续或重复：期望 {expect}.，实际 {n}.")
            expect = max(expect, n)
        expect += 1
    for sec, sub in sorted(subs):
        if sec not in tops:
            problems.append(f"{rel}: 子节 {sec}.{sub} 没有对应的顶层章节 {sec}.")
        expect_sub = 1
        for s in sorted(s for c, s in subs if c == sec):
            if s != expect_sub:
                problems.append(f"{rel}: 第 {sec} 节子节编号不连续：期望 {sec}.{expect_sub}，实际 {sec}.{s}")
                expect_sub = max(expect_sub, s)
            expect_sub += 1

    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for word in BANNED_WORDS:
            if word in line:
                problems.append(f"{rel}:{i}: 措辞不合规（口语词「{word}」）：{line.strip()[:60]}")
        for ch in emoji_hits(line):
            problems.append(f"{rel}:{i}: 措辞不合规（emoji「{ch}」）：{line.strip()[:60]}")
        targets = {rel}
        for link in LINK_MD.findall(line):
            target = (path.parent / link).resolve()
            try:
                targets.add(target.relative_to(ROOT).as_posix())
            except ValueError:
                continue
        for plain in PATH_MD.findall(line):
            for base in (ROOT, path.parent):
                candidate = (base / plain).resolve()
                if candidate.exists():
                    targets.add(candidate.relative_to(ROOT).as_posix())
                    break
        for m in REF.finditer(line):
            sec = int(m.group(1))
            sub = m.group(2)
            ok = False
            for t in targets:
                if sub is None and sec in all_tops.get(t, set()):
                    ok = True
                if sub is not None and (sec, int(sub)) in all_subs.get(t, set()):
                    ok = True
            if not ok:
                ref = f"§{sec}.{sub}" if sub else f"§{sec}"
                problems.append(f"{rel}:{i}: 交叉引用 {ref} 无对应章节（本行引用：{', '.join(sorted(targets))}）")
    return problems


def main() -> int:
    files = md_files()
    all_tops: dict[str, set[int]] = {}
    all_subs: dict[str, set[tuple[int, int]]] = {}
    for p in files:
        tops, subs = parse(p.read_text(encoding="utf-8"))
        rel = p.relative_to(ROOT).as_posix()
        all_tops[rel] = set(tops)
        all_subs[rel] = subs

    problems: list[str] = []
    for p in files:
        problems += check_file(p)
        problems += check_structure(p, all_tops, all_subs)

    print(f"文档规范自检：{len(files)} 个 Markdown 文件")
    for p in files:
        tops = sorted(all_tops[p.relative_to(ROOT).as_posix()])
        span = f"{tops[0]}–{tops[-1]}" if tops else "无编号章节"
        print(f"  {p.relative_to(ROOT).as_posix():22s} 顶层章节 {len(tops):2d} 个（{span}）")
    if problems:
        print(f"\n发现 {len(problems)} 处问题：")
        for item in problems:
            print("  -", item)
        print("\n文档规范自检：失败")
        return 1
    print("\n文档规范自检：通过")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
