"""校验百度地图 AK 是否可用：地点检索 / 地理编码 / 步行路径规划。

    set BAIDU_SERVER_AK=xxx & set BAIDU_SK=yyy & python tools/check_baidu_ak.py
    # 或： python tools/check_baidu_ak.py --ak xxx --sk yyy --lon 121.44 --lat 31.24

逐项打印 status / 结果数量 / 耗时，并解释常见错误码含义。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifecycle_metrics.providers.baidu import _place_search, _sn  # noqa: E402

STATUS_MEANING = {
    0: "成功",
    1: "服务器内部错误",
    2: "请求参数非法",
    3: "权限校验失败（该 AK 未开通此服务，或白名单/SN 配置不符）",
    4: "配额校验失败（当日配额用尽或未申请该服务）",
    5: "AK 不存在或已失效",
    101: "AK 参数错误",
    102: "AK 被禁用",
    240: "APP 服务被禁用",
    302: "配额不足或该服务未开通",
    401: "当前 AK 类型不支持该服务（如把浏览器端 AK 用于 Web 服务 API）",
}


def call(url: str, params: dict, timeout: int = 20) -> dict:
    import requests
    t0 = time.time()
    try:
        resp = requests.get(url, params=params, timeout=timeout,
                            headers={"User-Agent": "lifecycle-metrics/0.1"})
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "http_error": f"{type(exc).__name__}: {exc}",
                "hint": "本机无法访问 api.map.baidu.com：检查网络/代理，或先在有网环境验证 AK",
                "ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    status = body.get("status")
    return {"ok": status == 0, "status": status,
            "meaning": STATUS_MEANING.get(status, "未知状态码"),
            "results": len(body.get("results", []) or []),
            "message": body.get("message"), "ms": ms,
            "sample": (body.get("results") or [{}])[0].get("name") if body.get("results") else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验百度地图 AK 可用性")
    ap.add_argument("--ak", default=os.environ.get("BAIDU_SERVER_AK") or os.environ.get("BAIDU_AK"))
    ap.add_argument("--sk", default=os.environ.get("BAIDU_SK"))
    ap.add_argument("--lon", type=float, default=121.44)
    ap.add_argument("--lat", type=float, default=31.24)
    ap.add_argument("--with-sk", action="store_true", help="强制带 SN 校验（AK 开启了 SN 时必须）")
    args = ap.parse_args(argv)

    if not args.ak:
        print(json.dumps({"ok": False,
                          "error": "未提供 AK：设置环境变量 BAIDU_SERVER_AK 或用 --ak 传入"},
                         ensure_ascii=False, indent=1))
        return 2

    out: dict = {"ak_suffix": args.ak[-6:], "sk_provided": bool(args.sk), "checks": {}}

    def with_optional_sn(path: str, params: dict) -> dict:
        """先不带 SN 调一次；若失败且提供了 SK，再带 SN 重试，并记录哪种方式可用。"""
        base = call("https://api.map.baidu.com" + path, dict(params))
        if base.get("ok") or not args.sk:
            base["sn_used"] = False
            return base
        signed = dict(params)
        signed["sn"] = _sn(path, params, args.sk)
        retry = call("https://api.map.baidu.com" + path, signed)
        retry["sn_used"] = True
        retry["first_attempt"] = {"status": base.get("status"), "meaning": base.get("meaning")}
        return retry

    # 1) 地点检索（周边检索）
    out["checks"]["place_search"] = with_optional_sn("/place/v2/search", {
        "query": "菜市场", "location": f"{args.lat},{args.lon}", "radius": 1500,
        "output": "json", "scope": "2", "page_num": 0, "page_size": 20, "ak": args.ak})

    # 2) 地理编码
    out["checks"]["geocoding"] = with_optional_sn("/geocoding/v3/", {
        "address": "上海市普陀区曹杨新村", "output": "json", "ak": args.ak})

    # 3) 步行路径规划（用于校验自算路网）
    out["checks"]["walking_route"] = with_optional_sn("/directionlite/v1/walking", {
        "origin": f"{args.lat},{args.lon}",
        "destination": f"{args.lat + 0.005},{args.lon + 0.005}",
        "output": "json", "ak": args.ak})

    out["all_ok"] = all(c.get("ok") for c in out["checks"].values())
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
