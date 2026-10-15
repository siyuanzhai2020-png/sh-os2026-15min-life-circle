"""一键演示的逐步取景与几何自检（CDP 驱动无头 Edge）。

为什么不用 `msedge --screenshot`：那个走 `--virtual-time-budget`，而演示里全是
requestAnimationFrame 驱动的等待，虚拟时钟推进不可控 —— 实测同一份页面在
budget=11s / 16s 下截出来是同一帧，且预览第 4 步之后会卡在第 1 步。
CDP 走真实时钟，可以"摆好某一步 → 等稳 → 截图 / 量尺寸"，是唯一可靠的逐步核验方式。

用法：
    python tools/tour_shots.py 2 3 6 11          # 逐步骤截图 → out/tour/stepNN.png
    python tools/tour_shots.py --check           # 逐步自检：高亮框 / 指向线 / 卡片是否越界
    python tools/tour_shots.py --eval "<js>"     # 摆好第 1 步后求值一段 JS（调试用）
    python tools/tour_shots.py --url http://127.0.0.1:8000/?community=caoyang-real 2

依赖：本机装 Edge 或 Chrome；Python 的 websockets。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

ROOT = Path(__file__).resolve().parent.parent

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# 逐步核验：高亮框 / 指向线 / 指向卡是否都真的落在页面上，几何是否越界
PROBE_JS = r"""
(() => {
  const d = window.__demo, st = d.steps()[d.current() - 1] || {};
  const ring = document.getElementById('demoRing');
  const line = document.getElementById('daLine');
  const card = document.getElementById('demoCard');
  const rr = ring.getBoundingClientRect(), cr = card.getBoundingClientRect();
  const W = innerWidth, H = innerHeight;
  const cardOK = !card.hidden && cr.width > 0
    && cr.left >= -1 && cr.top >= -1 && cr.right <= W + 1 && cr.bottom <= H + 1;
  const ringOK = !!st.target && !ring.hidden && rr.width > 0;
  const noTarget = !st.target;
  return JSON.stringify({
    key: st.key || '?', target: st.target || '(居中卡)',
    ring: !ring.hidden && rr.width > 0,
    arrow_pts: (line.getAttribute('points') || '').split(' ').filter(Boolean).length,
    side: d.side(),
    hold: d.currentHold(),
    card: [cr.left, cr.top, cr.width, cr.height].map(Math.round).join(','),
    card_in_view: cardOK,
    ok: cardOK && (noTarget || (ringOK && (line.getAttribute('points') || '').length > 0)),
  });
})()
"""


def find_browser() -> Optional[str]:
    return next((p for p in BROWSERS if Path(p).exists()), None)


# 超时时把页面状态一起报出来 —— 只报"超时"没法定位，得知道它卡在哪个等待上
DIAG_JS = r"""
(() => {
  const r = state.route || {};
  return JSON.stringify({
    i: demo.i + 1, on: demo.on, paused: demo.paused, replaying: demo.replaying,
    raf: demo.raf, lastKey: demo.lastKey,
    spotEl: demo.spotEl ? (demo.spotEl.id || demo.spotEl.className) : '-',
    spotEl2: demo.spotEl2 ? (demo.spotEl2.id || demo.spotEl2.className) : '-',
    playing: !!r.playing, routePersona: (r.data && r.data.persona && r.data.persona.key) || '-',
    loading: !!state.loading, opt: !!(state.opt && state.opt.sites),
  });
})()
"""


# 目标元素为什么量不到矩形：把它和滚动容器的几何一起报出来
TARGET_JS = r"""
(() => {
  const d = window.__demo;
  const el = d.stepEl ? d.stepEl() : null;
  const panel = document.querySelector('.panel');
  const r = el ? el.getBoundingClientRect() : null;
  const pr = panel.getBoundingClientRect();
  return JSON.stringify({
    sel: d.stepTarget ? d.stepTarget() : '?',
    rect: r ? [r.left, r.top, r.width, r.height].map(Math.round) : null,
    panel: [Math.round(pr.top), Math.round(pr.height), panel.scrollTop,
            panel.scrollHeight, panel.clientHeight].map(Math.round),
    win: [window.innerWidth, window.innerHeight, demoBarH()],
  });
})()
"""


def http_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8"))


async def wait_target(port: int, marker: str, timeout: float = 25.0) -> Dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            for t in http_json(f"http://127.0.0.1:{port}/json/list"):
                # 只认**本次**启动的那个页面：端口上很可能还挂着上一次没清干净的僵尸浏览器
                # （脚本被强杀时 finally 不会执行）。连到旧页面上会得到一堆灵异现象 ——
                # 后台标签页被 Chrome 冻结后 JS 完全停摆，Runtime.evaluate 于是永远不返回。
                if (t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                        and marker in t.get("url", "")):
                    return t
        except (urllib.error.URLError, OSError, ValueError):
            pass
        await asyncio.sleep(0.3)
    raise RuntimeError("没等到可调试的页面（浏览器没起来？调试端口被上一次的僵尸实例占着？）")


def free_port() -> int:
    """要一个空闲端口 —— 固定端口会撞上僵尸实例。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def kill_stale(port: int) -> int:
    """收掉占着这个调试端口的僵尸 headless 实例（只杀命令行里带 --headless 的）。"""
    if sys.platform != "win32":
        return 0
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -match 'headless' -and "
          f"$_.CommandLine -match 'remote-debugging-port={port}' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "
          "$_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20)
        return len([x for x in out.stdout.split() if x.strip().isdigit()])
    except Exception:
        return 0


class CDP:
    """极简 CDP 客户端。

    每次调用都有超时兜底：页面里的 promise 一旦不落地（例如某一步在等一个永远不会来的
    动画帧），没有超时就是一个"脚本挂住不动、什么都不打印"的哑巴故障 —— 上一版正是这样，
    排查花了很久。超时时把发起调用的表达式一起报出来，一眼能看出卡在哪。
    """

    def __init__(self, ws: Any, timeout: float = 60.0):
        self.ws = ws
        self.n = 0
        self.timeout = timeout

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None,
                   timeout: Optional[float] = None) -> Dict[str, Any]:
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        limit = timeout or self.timeout
        t0 = time.time()
        while True:
            left = limit - (time.time() - t0)
            if left <= 0:
                raise TimeoutError(f"{method} 超过 {limit:.0f}s 没有返回")
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=left))
            except asyncio.TimeoutError:
                raise TimeoutError(f"{method} 超过 {limit:.0f}s 没有返回") from None
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})

    async def js(self, expr: str, await_promise: bool = False,
                 timeout: Optional[float] = None) -> Any:
        r = await self.call("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise, "returnByValue": True},
            timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS 异常：{r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")


async def run(args: argparse.Namespace) -> int:
    browser = find_browser()
    if not browser:
        print("找不到 Edge / Chrome，跳过（把可执行文件路径加进 BROWSERS 即可）")
        return 2
    port = args.port or free_port()
    killed = kill_stale(port)
    if killed:
        print(f"[i] 先收掉占着端口 {port} 的 {killed} 个僵尸浏览器进程", flush=True)
    marker = f"tour={os.getpid()}"
    url = args.url + ("&" if "?" in args.url else "?") + marker
    tmp = tempfile.mkdtemp(prefix="tour-shots-")
    proc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
         f"--window-size={args.width},{args.height}", f"--remote-debugging-port={port}",
         f"--user-data-dir={tmp}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = await wait_target(port, marker)
        async with websockets.connect(target["webSocketDebuggerUrl"],
                                      max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            # 等前端就绪：__demo 挂上 ≠ 数据到了。loadBundle 是异步的，
            # 只等 readyState 会拿到 state.bundle === null，演示第一步就炸。
            ok = False
            for _ in range(80):
                try:
                    if await cdp.js("typeof state !== 'undefined' && !!state.bundle "
                                    "&& !!window.__demo && document.readyState === 'complete'"):
                        ok = True
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.25)
            if not ok:
                print("前端没就绪（__demo / state.bundle 未就位）")
                return 1

            if args.eval:
                if args.steps:
                    await cdp.js(f"window.__demo.preview({args.steps[0]})", await_promise=True)
                    await asyncio.sleep(args.settle)
                print(await cdp.js(args.eval, await_promise=True))

            if args.check:
                # 逐步**实跑**一遍再量：只看 DOM 里元素是否存在没用 —— 规划模式下的卡片
                # 一开始是隐藏的，必须靠这一步自己的 run() 把它切出来，量到的才作数。
                bad = []
                t_all = time.time()
                for n in args.steps:
                    t0 = time.time()
                    # 在**页面里**再包一层超时：CDP 的 awaitPromise 一旦碰上永远不落地的
                    # promise，Python 侧只能干等；页面内的 race 保证这个调用一定会返回，
                    # 并且明确告诉我们是"没摆好"还是"只是慢"。
                    expr = ("(async()=>{const t0=performance.now();"
                            f"const r=await Promise.race([window.__demo.preview({n})"
                            ".then(()=>'done').catch(e=>'ERR '+e.message),"
                            f"new Promise(s=>setTimeout(()=>s('TIMEOUT'),{int(args.step_timeout * 1000)}))]);"
                            "return r+' '+(performance.now()-t0).toFixed(0)+'ms'})()")
                    try:
                        res = await cdp.js(expr, await_promise=True,
                                           timeout=args.step_timeout + 15)
                    except (TimeoutError, RuntimeError) as err:
                        print(f"step {n:>2} 摆位失败（{time.time() - t0:.1f}s）：{err}", flush=True)
                        bad.append(n)
                        continue
                    if not str(res).startswith("done"):
                        diag = await cdp.js(DIAG_JS)
                        print(f"step {n:>2} 摆位失败（{time.time() - t0:.1f}s）：{res}\n"
                              f"         {diag}", flush=True)
                        bad.append(n)
                        continue
                    await asyncio.sleep(args.settle)
                    probe = json.loads(await cdp.js(PROBE_JS))
                    flag = "" if probe["ok"] else "  ✘"
                    print(f"step {n:>2} {probe['key']:<10} target={probe['target']:<16} "
                          f"ring={'有' if probe['ring'] else '无'} arrow={probe['arrow_pts']} "
                          f"side={probe['side']:<6} 停留={probe['hold']:>2}s "
                          f"摆位={time.time() - t0:4.1f}s card={probe['card']}{flag}",
                          flush=True)
                    if not probe["ok"]:
                        bad.append(n)
                        print(f"         诊断: {await cdp.js(DIAG_JS)}", flush=True)
                        print(f"         目标矩形: {await cdp.js(TARGET_JS)}", flush=True)
                print(f"逐步自检：{'全部通过 ✔' if not bad else f'{len(bad)} 步有问题 ✘ {bad}'}"
                      f"（12 步共 {time.time() - t_all:.0f}s）", flush=True)
                return 0 if not bad else 1

            out_dir = ROOT / "out" / "tour"
            out_dir.mkdir(parents=True, exist_ok=True)
            for n in args.steps:
                await cdp.js(f"window.__demo.preview({n})", await_promise=True)
                await asyncio.sleep(args.settle)
                shot = await cdp.call("Page.captureScreenshot", {"format": "png"})
                png = out_dir / f"step{n:02d}.png"
                png.write_bytes(base64.b64decode(shot["data"]))
                info = await cdp.js("(document.getElementById('demoDbgBar')||{}).textContent || ''")
                print(f"step {n:>2}: {png.relative_to(ROOT)}  {png.stat().st_size} bytes")
                if info:
                    print(f"         {info}")
            return 0
    finally:
        # 必须连子进程一起收掉：Edge 会拉起一堆渲染进程，只 terminate 启动进程的话
        # 它们会继续活着占住调试端口 —— 下一次运行就会连到上一次那个僵尸浏览器上，
        # 表现为"脚本挂住不动"（踩过一次，堆了 16 个 headless 实例）。
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一键演示逐步取景（CDP 驱动无头 Edge）")
    ap.add_argument("steps", nargs="*", type=int, default=[], help="要截图的步骤号（1 起）")
    ap.add_argument("--url", default="http://127.0.0.1:8000/?community=caoyang-real&demodebug=1")
    ap.add_argument("--port", type=int, default=0,
                    help="调试端口；0 = 每次自动挑一个空闲端口（默认，避免撞上僵尸实例）")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=950)
    ap.add_argument("--settle", type=float, default=0.6, help="每步截图前额外等待秒数")
    ap.add_argument("--step-timeout", type=float, default=90.0,
                    help="单步摆位超时秒数（含前置步骤重放；第 ⑦ 步要算选址，留宽一点）")
    ap.add_argument("--check", action="store_true", help="只做逐步自检，不截图")
    ap.add_argument("--eval", default="", help="先摆好首个步骤，再求值这段 JS 并打印（调试用）")
    args = ap.parse_args(argv)
    given_steps = bool(args.steps)
    if not given_steps and not args.eval:   # 没给步骤也没 --eval 时，默认全部步骤
        args.steps = list(range(1, 13))
    if args.eval and not given_steps:       # 只求值时不截图，免得把 eval 的输出淹掉
        args.steps = []
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
