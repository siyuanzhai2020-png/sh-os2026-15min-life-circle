"""把当前演示打包成可以直接发给别人的 ZIP。

    python tools/make_package.py [--out dist] [--name lifecycle-demo]

打包原则：
1. **绝不包含密钥**：.env.local / .env 一律排除，改为附一份 .env.local.example；
2. **离线可用**：附上数据集与缓存（含已缓存的百度静态底图 PNG），收件人无需网络、
   无需 AK 就能看到真实地图与完整体检结论；
3. **可复现**：源码、测试、文档、报告全部带上，`python -m unittest` 可直接跑；
4. **干净**：排除 __pycache__、调试截图、探针输出等开发期产物。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent

# Windows 管道/重定向的默认编码是 GBK，而报告口径里含 "km²"（U+00B2）—— GBK 编码不了它。
# 不修的话，包已经打完却会在最后一行 print 时以非零码结束，看起来像"打包失败"。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", "dist", ".idea", ".vscode"}
EXCLUDE_NAMES = {".env", ".env.local", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}
# out/ 顶层是我的验证截图（十几 MB），只保留各社区目录 —— 但 compare.* 是对比页，要留下
EXCLUDE_TOP_LEVEL_OUT_FILES = True
OUT_TOP_LEVEL_KEEP = {"compare.html", "compare.json"}
# out/ 下划线开头的都是开发期脚本与截图（_shots/、_probe_*.py、_check_*.py），不进包
EXCLUDE_OUT_UNDERSCORE = True

# out/ 下的纯开发期子目录：演示取景截图（12 张 ≈ 5 MB），收件人用不上
EXCLUDE_OUT_SUBDIRS = {"tour"}
# 仓库根目录的开发便捷脚本不进包：启动演示.cmd 与包内自动生成的那份同名（会打架），
# 跑检查.cmd 依赖仓库布局，收件人用不上；包里已有自己的启动脚本与快速上手文档。
EXCLUDE_ROOT_NAMES = {"启动演示.cmd", "跑检查.cmd"}
# 缓存里只带演示必需的（离线底图 PNG 等）：OSM 原始抓取缓存只用于重建数据，体积十几 MB，
# 不值得进包（离线演示读的是 data/*.dataset.json，不碰它）。百度 POI 缓存不大，保留。
CACHE_EXCLUDE_PREFIX = ("probe-", "osm-")
CACHE_EXCLUDE_NAMES = {"_probe.png"}

LAUNCHER_CMD = r"""@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo   Community Life-Circle Check-up Demo  (Shanghai OS 2026)
echo   Quick start: open "请先读我.html"  ^(Chinese, offline^)
echo ================================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python not found. Install Python 3.9+ and tick "Add python.exe to PATH".
  echo     https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, requests, PIL, numpy" >nul 2>nul
if errorlevel 1 (
  echo [*] Installing dependencies ^(needs internet, about 1 minute^)...
  python -m pip install -r requirements.txt
  if errorlevel 1 ( echo [X] pip install failed. & pause & exit /b 1 )
)

if not exist "out\caoyang-real\report.html" (
  echo [*] Building reports from bundled data ^(offline, no AK needed^)...
  python tools\build_demo.py caoyang-real
  python tools\build_demo.py custom-078ed4
)

echo [*] Server: http://127.0.0.1:8000   (close this window to stop)
start "" http://127.0.0.1:8000
python tools\serve.py --port 8000 --warm
pause
"""

LAUNCHER_SH = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "Community Life-Circle Check-up Demo"
python3 -c "import fastapi, uvicorn, requests, PIL, numpy" 2>/dev/null || \\
  python3 -m pip install -r requirements.txt
[ -f out/caoyang-real/report.html ] || python3 tools/build_demo.py caoyang-real
echo "Server: http://127.0.0.1:8000"
python3 tools/serve.py --port 8000 --warm
"""

ENV_EXAMPLE = """# 把本文件复制为 .env.local 并填入你自己的百度地图 AK（浏览器端 + 服务端两类）。
# 演示包默认不含任何密钥：已有的数据集与静态底图都已随包附带，离线即可完整演示；
# 只有「＋ 自选社区」与「百度地图 JSAPI GL 底图」需要外网 + 你自己的 AK。
# 详见 docs/baidu-ak.md
BAIDU_WEB_AK=
BAIDU_SERVER_AK=
# 若服务端 AK 开启了 SN 校验，再填 SK
# BAIDU_SK=
"""


def quickstart_zh(stats: dict) -> str:
    return f"""# 社区生活圈智能体检与规划助手 · 演示包

> 2026 上海开源软件应用创新大赛 ·【开源 AI 工具赛道】百度地图赛题
> 打包时间：{stats['built_at']}　|　包内文件 {stats['files']} 个，约 {stats['size_mb']} MB

## 30 秒启动

**方式一（推荐）**：双击 `请先读我.html` 看本说明 → 双击 `启动演示.cmd` → 浏览器自动打开
http://127.0.0.1:8000

**方式二（不启动服务）**：直接双击 `out/caoyang-real/report.html`，这是一份自带图表的完整体检报告，离线可看；
也可以双击 `out/compare.html` 看六个社区的横向对比。

**方式三（命令行）**：

```bash
pip install -r requirements.txt
python tools/serve.py --port 8000 --warm      # 或：python tools/build_demo.py caoyang-real
```

> 首次运行需要 Python 3.9+。若依赖已装好，启动只要几秒。

## 这是什么

一句话：**把"15 分钟生活圈"从口号变成可量化、可复现、能出处方的体检报告。**

- 输入：社区范围（真实数据用百度 POI + OpenStreetMap 步行路网；离线演示用内置合成社区）
- 处理：自建步行路网最短路 → 算可达性 → 六维指标 → 问题清单 → 选址优化（处方）
- 输出：交互式地图 + 体检报告（HTML/Markdown）+ 结构化 JSON（每个数字可复核）

## 建议的 5 分钟浏览路线

1. **左侧地图**：真实百度底图 + 语义图标（房子=居住组团、菜篮=菜市场、店铺=便利店、M=地铁…）。
   **滚轮缩放、拖拽平移**；鼠标悬停能看到设施名称与组团缺口。
2. **右上角综合得分**：{stats['score_caoyang']}（良好）。下面"六维指标"看短板在哪一维。
3. **右侧「要素体检」表**：红字=达标率不足的要素，一眼看出缺什么。
4. **右下「规划建议（处方）」**：给定新增设施数量，系统推荐具体点位与覆盖增益——
   这是本项目区别于"地图套壳"的关键。
5. **顶部「查看完整报告」**：含问题清单、空间分布、**误差分析**（自算路网 vs 百度步行路径规划
   的抽样对照）与**坐标对齐核验图**。
6. **顶部「＋ 自选社区」**：输入任意地址（如"上海市普陀区XX街道办事处"）就能拉真实数据出结论
   （需外网；约 20–60 秒）。
7. **左上社区下拉框**：可在三个社区之间切换对比——曹杨新村街道（72.8 良好）、
   长风新村街道（68.4 一般）、离线合成社区（69.9）。

## 关于网络与密钥

- 包内**不含任何 AK/密钥**（`.env.local.example` 只是模板，要用请自己填）。
- **真实底图已离线缓存**（`data/cache/basemap-*.png`），所以不联网也能看到真地图。
- 六个社区的数据集都已随包附带，**不联网也能完整跑通体检与处方**。
  包内不含 OSM 原始抓取缓存（`osm-*.json`，十几 MB，只有重建数据时才用得到）。
- 只有这两件事需要外网：①「＋ 自选社区」；②「百度地图 JSAPI GL 底图」标签页（需自己的浏览器端 AK）。
  如果浏览器被代理/扩展拦了外网（诊断页 `/diag.html` 会告诉你），用默认的「体检底图」模式即可。

## 数据口径（重要，别只看分数）

| 项 | 说明 |
| --- | --- |
| 空间结构 | 百度地点检索（POI）+ OpenStreetMap（步行路网、建筑轮廓） |
| 户数 | 三级择优：实测 CSV > 普查总量校准 > OSM 建筑估算。两个真实社区都用**七普街道人口密度**做了总量校准（曹杨新村：40,989 → 117,556 人，×2.87） |
| 坐标 | 数据集统一 WGS-84；百度数据由 BD-09 换算（上海地区两者相差约 1.16 km，不换算会整体错位） |
| 底图 | 百度 zoom 分辨率不是 Web Mercator：实测 `262150·cos(lat)/2^zoom`（差 1.675 倍），已按实测标定 |
| 可达性 | 自建路网最短路 + 离路吸附惩罚；**已用百度步行路径规划抽样标定**：距离比 1.033、15 分钟判定一致率 96.7% |

**已知局限**（报告里也写了）：未建模过街等待/坡度/无障碍；设施容量为相对模型；
人口总量为密度推算值；要素清单与阈值需与 GB 50180-2018 及上海导则原文逐条核对。

## 目录结构

```
请先读我.html          快速上手（建议第一个打开）
启动演示.cmd            一键启动（Windows）
out/<社区>/             体检报告（HTML/Markdown）、地图 PNG、诊断与处方 JSON、标定结果
  caoyang-real/         曹杨新村街道（真实数据）
  custom-078ed4/        长风新村街道（真实数据）
  caoyang-demo/         曹杨示范社区（离线合成，用于无网络演示与回归）
lifecycle_metrics/      引擎：可达性 + 六维指标 + 选址优化 + 坐标换算 + 数据装配
web/                    交互式前端（纯本地资源，无 CDN）+ 底图诊断页
tools/                  命令行入口：出报告 / 起服务 / 标定 / 核验 / 自选社区 / 打包
config/                 口径（elements.json）与社区清单（communities.json）
docs/                   指标口径、数据契约与坐标系、百度 AK 接入说明
tests/                  21 项单元测试（口径与算法回归）
data/                   数据集与原始响应缓存（离线可复现）
```

## 常见问题

- **双击没反应 / 提示找不到 Python**：装 Python 3.9+，勾选 "Add python.exe to PATH" 后重开。
- **端口 8000 被占用**：`python tools/serve.py --port 8010`。
- **地图空白**：切到「体检底图」模式（默认就是），它由本地服务提供底图，不依赖浏览器出网。
- **想自己新建社区**：见 `docs/baidu-ak.md`（需要你自己的百度 AK），或命令行
  `python tools/add_community.py --name "XX街道" --address "..." --population 98000 --area 2.14`。
- **杀毒软件报警**：启动脚本只是调用本地 Python 起一个 127.0.0.1 的服务，可放心允许。

---

*本演示的所有数字都可由 `out/<社区>/diagnose.json`、`optimize.json`、`walk_calibration.json`
与 `python -m unittest` 复核。*
"""


def md_to_html(md: str, title: str) -> str:
    """极简 Markdown → HTML（标题/表格/代码块/列表/加粗/行内代码），不引依赖。

    只为让收件人双击就能读快速上手，不做完整 Markdown 实现。
    """
    import html as _html
    import re

    def inline(s: str) -> str:
        s = _html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    out: List[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(_html.escape(lines[i]))
                i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if line.strip() in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        next_is_rule = (i + 1 < len(lines)
                        and set(lines[i + 1].replace("|", "").strip()) <= set("-: "))
        if line.startswith("|") and next_is_rule:
            header = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in header)
            tb = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items: List[str] = []
            while i < len(lines):
                if re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                    items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                    i += 1
                elif items and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\|)", lines[i]):
                    items[-1] += " " + lines[i].strip()      # 列表项的续行并入上一项
                    i += 1
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if line.startswith(">"):
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        out.append(f"<p>{inline(line)}</p>")
        i += 1

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{_html.escape(title)}</title>
<style>
 body {{ max-width: 860px; margin: 32px auto; padding: 0 20px 80px; color:#1f2430; background:#fff;
        font: 15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
 h1 {{ font-size: 26px; border-bottom:3px solid #1f77b4; padding-bottom:10px; }}
 h2 {{ font-size: 20px; margin-top:32px; border-bottom:1px solid #e5e7eb; padding-bottom:6px; }}
 h3 {{ font-size: 16px; margin-top:22px; }}
 code {{ background:#f2f5f9; padding:1px 5px; border-radius:4px; font-size:13px; }}
 pre {{ background:#0f172a; color:#e2e8f0; padding:14px 16px; border-radius:10px; overflow:auto; }}
 pre code {{ background:none; color:inherit; }}
 table {{ border-collapse:collapse; width:100%; margin:14px 0; font-size:14px; }}
 th,td {{ border:1px solid #e5e7eb; padding:7px 9px; text-align:left; vertical-align:top; }}
 th {{ background:#f2f5f9; }}
 blockquote {{ margin:14px 0; padding:10px 16px; background:#f8fafc; border-left:4px solid #1f77b4;
               color:#475569; }}
 hr {{ border:0; border-top:1px solid #e5e7eb; margin:28px 0; }}
 a {{ color:#1f77b4; }}
</style></head><body>
{chr(10).join(out)}
</body></html>
"""


def iter_files(root: Path) -> Iterable[Tuple[Path, str]]:
    """产出 (绝对路径, zip 内相对路径)。"""
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIX:
            continue
        if len(rel_parts) == 1 and path.name in EXCLUDE_ROOT_NAMES:
            continue                                  # 仓库根目录的开发便捷脚本
        rel = "/".join(rel_parts)
        if rel_parts[0] == "out" and len(rel_parts) > 2 and rel_parts[1] in EXCLUDE_OUT_SUBDIRS:
            continue                                  # out/tour/ 演示取景截图（开发期产物）
        if EXCLUDE_OUT_UNDERSCORE and rel_parts[0] == "out" and any(
                p.startswith("_") for p in rel_parts[1:]):
            continue                                  # out/ 下的开发期脚本与截图
        if (rel_parts[0] == "out" and len(rel_parts) == 2 and EXCLUDE_TOP_LEVEL_OUT_FILES
                and path.name not in OUT_TOP_LEVEL_KEEP):
            continue                                  # out/ 顶层的开发期截图
        if rel_parts[:2] == ("data", "cache"):
            if path.name.startswith(CACHE_EXCLUDE_PREFIX) or path.name in CACHE_EXCLUDE_NAMES:
                continue
        if rel.startswith("dist/"):
            continue
        yield path, rel


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打包演示")
    ap.add_argument("--out", default="dist", help="输出目录")
    ap.add_argument("--name", default="15min-life-circle-demo", help="包名前缀")
    args = ap.parse_args(argv)

    built_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = dt.datetime.now().strftime("%Y%m%d")

    # 先算文件清单与体积，供 README 里的数字使用
    files = list(iter_files(ROOT))
    total = sum(p.stat().st_size for p, _ in files)
    diag_path = ROOT / "out" / "caoyang-real" / "diagnose.json"
    score = "72.8"
    if diag_path.exists():
        score = str(json.loads(diag_path.read_text(encoding="utf-8"))["summary"]["composite_score"])

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{args.name}-{stamp}.zip"

    quick = quickstart_zh({"built_at": built_at, "files": len(files) + 5,
                           "size_mb": round(total / 1e6, 1),
                           "score_caoyang": score})
    extra = {
        "请先读我.html": md_to_html(quick, "社区生活圈体检演示 · 快速上手"),
        "README-quickstart-zh.md": quick,
        "启动演示.cmd": LAUNCHER_CMD,
        "start-demo.sh": LAUNCHER_SH,
        ".env.local.example": ENV_EXAMPLE,
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        root_name = f"{args.name}-{stamp}"
        for path, rel in files:
            zf.write(path, f"{root_name}/{rel}")
        for name, text in extra.items():
            zf.writestr(f"{root_name}/{name}", text.encode("utf-8"))

    size_mb = zip_path.stat().st_size / 1e6
    print(json.dumps({
        "zip": str(zip_path.relative_to(ROOT)),
        "size_mb": round(size_mb, 2),
        "files": len(files) + len(extra),
        "uncompressed_mb": round(total / 1e6, 2),
        "excluded_secrets": [".env", ".env.local"],
        "included": {"datasets": len(list((ROOT / 'data').glob('*.dataset.json'))),
                     "cache_files": len(list((ROOT / 'data' / 'cache').glob('*'))),
                     "reports": sorted(p.name for p in (ROOT / 'out').iterdir()
                                       if p.is_dir() and not p.name.startswith('_'))},
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
