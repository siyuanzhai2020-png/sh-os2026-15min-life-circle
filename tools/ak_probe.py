"""带硬超时的连通性 + AK 探针：分步打印，任一步失败都不阻塞。

    python tools/ak_probe.py            # 只探网络
    python tools/ak_probe.py --live     # 网络通时顺带验证 AK（读 .env.local / 环境变量）

AK 只以掩码形式打印（后 6 位），不落盘到代码里。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST = "api.map.baidu.com"
DEADLINE = time.time() + 45          # 全局硬上限：45 秒内必须给出结论


def load_env_local(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def mask(v: str | None) -> str:
    if not v:
        return "(未设置)"
    return "…" + v[-6:] if len(v) > 6 else "…"


def run_with_timeout(fn, timeout: float, default=None):
    """在守护线程里跑 fn，超时返回 default —— 保证探针不会挂死。"""
    box = {"value": default}

    def wrapper():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["value"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"error": f"timeout>{timeout}s"}
    return box["value"]


def step_dns() -> dict:
    t0 = time.time()
    infos = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
    return {"ok": True, "ips": sorted({i[4][0] for i in infos}),
            "ms": int((time.time() - t0) * 1000)}


def step_tcp() -> dict:
    t0 = time.time()
    with socket.create_connection((HOST, 443), timeout=8):
        pass
    return {"ok": True, "ms": int((time.time() - t0) * 1000)}


def step_https() -> dict:
    import requests
    t0 = time.time()
    r = requests.get(f"https://{HOST}/", timeout=(5, 10))
    return {"ok": True, "status": r.status_code, "bytes": len(r.content),
            "ms": int((time.time() - t0) * 1000)}


def step_place_search(ak: str, sk: str | None, lat: float, lon: float) -> dict:
    from lifecycle_metrics.providers.baidu import _place_search
    t0 = time.time()
    data = _place_search(ak, "菜市场", (lat, lon), 1500, page_num=0, sk=sk,
                         cache_dir=ROOT / "data" / "cache")
    results = data.get("results", [])
    return {"ok": data.get("status") == 0, "status": data.get("status"),
            "message": data.get("message"), "count": len(results),
            "sample": [r.get("name") for r in results[:5]],
            "ms": int((time.time() - t0) * 1000)}


def step_walking(ak: str, sk: str | None, lat: float, lon: float) -> dict:
    import requests
    from lifecycle_metrics.providers.baidu import _sn
    path = "/directionlite/v1/walking"
    params = {"origin": f"{lat},{lon}", "destination": f"{lat + 0.005},{lon + 0.005}",
              "output": "json", "ak": ak}
    if sk:
        params["sn"] = _sn(path, params, sk)
    t0 = time.time()
    r = requests.get(f"https://{HOST}{path}", params=params, timeout=(5, 10))
    body = r.json()
    res = body.get("result") or {}
    routes = res.get("routes") or []
    return {"ok": body.get("status") == 0, "status": body.get("status"),
            "message": body.get("message"),
            "distance_m": routes[0].get("distance") if routes else None,
            "duration_s": routes[0].get("duration") if routes else None,
            "ms": int((time.time() - t0) * 1000)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="连通性与百度 AK 探针")
    ap.add_argument("--live", action="store_true", help="网络通时验证 AK")
    ap.add_argument("--lat", type=float, default=31.24)
    ap.add_argument("--lon", type=float, default=121.44)
    args = ap.parse_args(argv)

    env = load_env_local(ROOT / ".env.local")
    ak_server = os.environ.get("BAIDU_SERVER_AK") or env.get("BAIDU_SERVER_AK")
    ak_web = os.environ.get("BAIDU_WEB_AK") or env.get("BAIDU_WEB_AK")
    sk = os.environ.get("BAIDU_SK") or env.get("BAIDU_SK")

    out: dict = {"env_local": (ROOT / ".env.local").exists(),
                 "ak_web": mask(ak_web), "ak_server": mask(ak_server), "sk": mask(sk),
                 "steps": {}}

    def remaining(cap: float) -> float:
        return max(1.0, min(cap, DEADLINE - time.time()))

    for name, fn, cap in (("dns", step_dns, 10), ("tcp443", step_tcp, 10), ("https", step_https, 15)):
        out["steps"][name] = run_with_timeout(fn, remaining(cap))
        print(f"[{name}] {json.dumps(out['steps'][name], ensure_ascii=False)}", flush=True)
        if isinstance(out["steps"][name], dict) and out["steps"][name].get("ok"):
            continue
        out["verdict"] = f"{name} 失败：本机无法访问 {HOST}"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 2

    if args.live and ak_server:
        out["steps"]["place_search(server_ak)"] = run_with_timeout(
            lambda: step_place_search(ak_server, sk, args.lat, args.lon), remaining(20))
        print("[place_search] "
              + json.dumps(out["steps"]["place_search(server_ak)"], ensure_ascii=False),
              flush=True)
        res = out["steps"]["place_search(server_ak)"]
        if isinstance(res, dict) and not res.get("ok") and sk is None and ak_web:
            out["steps"]["place_search(web_ak_as_server)"] = run_with_timeout(
                lambda: step_place_search(ak_web, None, args.lat, args.lon), remaining(20))
        if isinstance(res, dict) and res.get("ok"):
            out["steps"]["walking_route"] = run_with_timeout(
                lambda: step_walking(ak_server, sk, args.lat, args.lon), remaining(20))

    out["verdict"] = "网络可达"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
