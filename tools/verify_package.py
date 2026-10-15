"""验证打包结果：解压到临时目录，用"陌生人环境"跑一遍。

    python tools/verify_package.py [dist/xxx.zip]

检查项：
1. 包内不含任何密钥文件；
2. 解压后单元测试全通过；
3. 起服务（换端口），核心接口可用；
4. **不用 AK、不联网**也能拿到静态底图（走缓存）与体检结论；
5. 报告文件齐全。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"

# Windows 管道/重定向的默认编码是 GBK，而报告口径里含 "km²"（U+00B2）—— GBK 编码不了它。
# 不修的话，自检已经跑完却会在最后一行 print 时以非零码结束，看起来像"自检失败"。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def wait_port(port: int, timeout: float = 40.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    import requests
    zips = sorted((ROOT / "dist").glob("*.zip"))
    zip_path = Path(argv[0]) if argv else (zips[-1] if zips else None)
    if not zip_path or not zip_path.exists():
        print("找不到 zip，请先运行 python tools/make_package.py")
        return 2

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="pkgverify-"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            roots = {n.split("/")[0] for n in names}
            zf.extractall(tmp)
        base = tmp / sorted(roots)[0]

        # 1) 密钥
        bad = [n for n in names if Path(n).name in (".env", ".env.local")]
        checks.append(("包内不含密钥文件", not bad, f"命中 {bad}" if bad else "已排除 .env/.env.local"))
        checks.append(("解压后有统一根目录", len(roots) == 1, str(sorted(roots))))

        # 2) 单元测试
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                           cwd=base, capture_output=True, text=True, timeout=300)
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        checks.append(("解压后单元测试通过", r.returncode == 0, tail[0]))

        # 3) 起服务（继承完整环境：Windows 上清空环境变量会导致 Python 起不来）
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("BAIDU_SERVER_AK", None)      # 刻意不注入任何 AK，验证离线可用
        env.pop("BAIDU_WEB_AK", None)
        env.pop("BAIDU_SK", None)
        proc = subprocess.Popen([sys.executable, "tools/serve.py", "--port", str(PORT)],
                                cwd=base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env)
        try:
            up = wait_port(PORT)
            checks.append(("服务可启动", up, BASE))
            if up:
                h = requests.get(f"{BASE}/api/health", timeout=30).json()
                checks.append(("GET /api/health", h.get("ok") is True,
                               json.dumps(h, ensure_ascii=False)[:120]))

                b = requests.get(f"{BASE}/api/bundle", timeout=180).json()
                summ = b["diag"]["summary"]
                checks.append(("GET /api/bundle（离线缓存数据集）",
                               summ["composite_score"] > 0,
                               f"{summ['residential_points']} 组团 / {summ['households']:,} 户 / "
                               f"{summ['population']:,} 人 / 得分 {summ['composite_score']}"))

                bm = requests.get(f"{BASE}/api/basemap.png", timeout=60)
                is_png = bm.content[:4] == b"\x89PNG"
                checks.append(("静态底图无需 AK（走离线缓存）",
                               bm.status_code == 200 and is_png,
                               f"{bm.status_code}, {len(bm.content)} bytes, PNG={is_png}"))

                meta = requests.get(f"{BASE}/api/basemap.json", timeout=30).json()
                checks.append(("底图地理参考（实测标定 mpp）",
                               abs(meta["mpp_m_per_px"] - 3.42) < 0.05,
                               f"mpp={meta['mpp_m_per_px']:.3f} m/px, span={meta['span_m']['width']:.0f} m"))

                rp = requests.get(f"{BASE}/api/report", timeout=60, allow_redirects=True)
                checks.append(("报告可通过服务访问", rp.status_code == 200 and "体检报告" in rp.text,
                               f"{rp.status_code}, {len(rp.content)} bytes"))

                cl = requests.get(f"{BASE}/api/communities", timeout=30).json()
                # 至少 3 个内置社区；打包时若带上自选社区会更多，所以只查下界
                checks.append((f"社区列表（≥3 个，实际 {len(cl['communities'])}）",
                               len(cl["communities"]) >= 3,
                               ", ".join(c["name"] for c in cl["communities"])))

                # 批次 4：住户画像 + 同心等时圈，必须离线可用（包内无 .env.local）
                pf = requests.get(f"{BASE}/api/config", timeout=30).json().get("personas") or []
                checks.append((f"住户画像离线可用（{len(pf)} 类）", len(pf) >= 5,
                               "、".join(p.get("name", "") for p in pf)))
                pid = (b["dataset"].get("residential") or [{}])[0].get("id")
                rp2 = requests.get(f"{BASE}/api/route",
                                   params={"point": pid, "persona": pf[0]["key"]},
                                   timeout=180).json()
                checks.append(("画像动线可生成（离线）",
                               rp2.get("persona", {}).get("key") == pf[0]["key"]
                               and rp2.get("assumptions", {}).get("walk_speed_mps")
                               == pf[0]["walk_speed_mps"],
                               f"{rp2['point']['name']} · {rp2['totals']['stops']} 站 · "
                               f"步速 {rp2['assumptions']['walk_speed_mps']} m/s"))
                iso2 = requests.get(f"{BASE}/api/isochrone", params={"point": pid},
                                    timeout=180).json()
                checks.append(("同心等时圈可生成（离线）",
                               len(iso2.get("bands") or []) == 3
                               and 0 < iso2.get("compactness", {}).get("ratio", 0) <= 1,
                               f"可达面积比 {iso2['compactness']['ratio']} · "
                               f"圈内 " + "/".join(str(x["elements_count"]) for x in iso2["bands"])))
                ipng = requests.get(f"{BASE}/api/isochrone.png", params={"point": pid}, timeout=180)
                checks.append(("风圈叠加图（GL 用）可生成",
                               ipng.status_code == 200 and ipng.content[:4] == b"\x89PNG",
                               f"{len(ipng.content)} bytes"))
                rpt = requests.get(f"{BASE}/api/report", timeout=120, allow_redirects=True)
                checks.append(("报告含风圈与住户画像章节",
                               "map_focus.png" in rpt.text and "同一栋楼" in rpt.text, ""))
                dh = requests.get(f"{BASE}/", timeout=60).text
                checks.append(("一键演示齐备（入口 / 菜单 / 极简控制条 / 聚光层 / 指向卡）",
                               all(k in dh for k in ("demoStart", "demoMenu", "demoBar",
                                                     "demoPause", "demoExit",
                                                     "demoSpot", "demoRing", "demoArrow",
                                                     "demoCard", "demoHoles", "daLine")), ""))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

        # 4) 报告文件
        for cid in ("caoyang-real", "custom-078ed4", "caoyang-demo"):
            p = base / "out" / cid / "report.html"
            checks.append((f"报告存在 {cid}", p.exists(), f"{p.stat().st_size} bytes" if p.exists() else "缺失"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in checks if not ok]
    print(json.dumps({
        "zip": str(zip_path.relative_to(ROOT)),
        "zip_size_mb": round(zip_path.stat().st_size / 1e6, 2),
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "passed": len(checks) - len(failed),
        "failed": failed,
    }, ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
