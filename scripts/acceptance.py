"""一次性验收脚本：对运行中的 API 执行端到端验收。

用法：
    BASE_URL=http://127.0.0.1:8000 python scripts/acceptance.py

全部通过时退出码为 0；任一失败则非零。不依赖第三方库。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/v1/friction/assess"

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" —— {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def request_json(raw: str | bytes, expect_status: int):
    body = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get(path: str):
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main() -> int:
    print(f"验收目标：{BASE_URL}\n")

    # 0. 健康检查
    try:
        status, data = get("/health")
        check("健康检查 200", status == 200 and data == {"status": "ok"}, str(data))
    except Exception as exc:  # noqa: BLE001 - 验收脚本需输出明确失败原因
        check("健康检查 200", False, repr(exc))
        print("\nAPI 不可达，终止验收。")
        return 1

    # 1. 合法请求：中位数、段等级、顺序
    body = {
        "runway_id": "18L",
        "front": [0.52, 0.31, 0.60],
        "middle": [0.40, 0.45, 0.90],
        "rear": [0.70, 0.80, 0.29],
    }
    status, data = request_json(json.dumps(body), 200)
    check("合法请求返回 200", status == 200, str(status))
    if status == 200:
        names = [s["segment"] for s in data["segments"]]
        check("段顺序为 front/middle/rear", names == ["front", "middle", "rear"], str(names))
        medians = {s["segment"]: s["median"] for s in data["segments"]}
        check("三段中位数正确", medians == {"front": 0.52, "middle": 0.45, "rear": 0.70}, str(medians))
        check("原始值按段回显", data["segments"][0]["raw_values"] == body["front"])
        check("整跑道等级=正常", data["overall_rating"] == "正常", data["overall_rating"])

    # 2. 临界值归属：0.40→正常，0.30→关注，0.29→关闭；最差段接管
    body = {
        "runway_id": "36R",
        "front": [0.39, 0.40, 0.50],
        "middle": [0.20, 0.30, 0.80],
        "rear": [0.10, 0.29, 0.90],
    }
    status, data = request_json(json.dumps(body), 200)
    check("临界请求返回 200", status == 200, str(status))
    if status == 200:
        ratings = {s["segment"]: (s["median"], s["rating"]) for s in data["segments"]}
        check("中位数 0.40 落级正常", ratings["front"] == (0.40, "正常"), str(ratings["front"]))
        check("中位数 0.30 落级关注", ratings["middle"] == (0.30, "关注"), str(ratings["middle"]))
        check("中位数 0.29 落级关闭", ratings["rear"] == (0.29, "关闭"), str(ratings["rear"]))
        check("最差段(后段)接管整跑道结论", data["overall_rating"] == "关闭", data["overall_rating"])
        check("worst_segments 指向后段", data["worst_segments"] == ["rear"], str(data["worst_segments"]))

    # 3. 仅关注段时，关注压过正常
    body = {
        "runway_id": "09",
        "front": [0.50, 0.60, 0.70],
        "middle": [0.30, 0.35, 0.39],
        "rear": [0.80, 0.90, 1.00],
    }
    status, data = request_json(json.dumps(body), 200)
    check("关注压过正常", status == 200 and data["overall_rating"] == "关注", str(data.get("overall_rating")))

    # 4. 422 整份拒绝矩阵
    rejected_cases: list[tuple[str, str | bytes]] = [
        ("段数量不符(2 个)", json.dumps({"runway_id": "18", "front": [0.5, 0.5],
                                          "middle": [0.5, 0.5, 0.5], "rear": [0.5, 0.5, 0.5]})),
        ("段数量不符(4 个)", json.dumps({"runway_id": "18", "front": [0.5] * 4,
                                          "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("超出上限 1.01", json.dumps({"runway_id": "18", "front": [1.01, 0.5, 0.5],
                                       "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("低于下限 -0.01", json.dumps({"runway_id": "18", "front": [-0.01, 0.5, 0.5],
                                        "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("精度超限 0.123", json.dumps({"runway_id": "18", "front": [0.123, 0.5, 0.5],
                                        "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("字符串非数值", json.dumps({"runway_id": "18", "front": ["0.5", 0.5, 0.5],
                                      "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("布尔值非数值", json.dumps({"runway_id": "18", "front": [True, 0.5, 0.5],
                                      "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("缺少段 middle", json.dumps({"runway_id": "18", "front": [0.5] * 3, "rear": [0.5] * 3})),
        ("多余段 side", json.dumps({"runway_id": "18", "front": [0.5] * 3,
                                     "middle": [0.5] * 3, "rear": [0.5] * 3, "side": [0.5] * 3})),
        ("跑道编号空白", json.dumps({"runway_id": "   ", "front": [0.5] * 3,
                                     "middle": [0.5] * 3, "rear": [0.5] * 3})),
        ("非法 JSON", b'{"runway_id":'),
        ("非有限值 NaN", b'{"runway_id":"18","front":[0.5,0.5,NaN],'
                         b'"middle":[0.5,0.5,0.5],"rear":[0.5,0.5,0.5]}'),
        ("非有限值 Infinity", b'{"runway_id":"18","front":[0.5,0.5,Infinity],'
                              b'"middle":[0.5,0.5,0.5],"rear":[0.5,0.5,0.5]}'),
        ("重复段名 middle", b'{"runway_id":"18","front":[0.5,0.5,0.5],'
                            b'"middle":[0.4,0.4,0.4],"middle":[0.1,0.1,0.1],'
                            b'"rear":[0.5,0.5,0.5]}'),
    ]
    for name, raw in rejected_cases:
        status, data = request_json(raw, 422)
        ok = status == 422 and isinstance(data.get("detail"), list) and bool(data["detail"])
        check(f"422 拒绝：{name}", ok, f"status={status}, body={str(data)[:160]}")
        if ok:
            check(f"  └ 无部分判定：{name}", "segments" not in data and "overall_rating" not in data)

    # 5. 边界 0.00 / 1.00 / 0.30 均合法可提交
    raw = json.dumps({"runway_id": "18",
                      "front": [0.00, 0.00, 0.00],
                      "middle": [1.00, 1.00, 1.00],
                      "rear": [0.30, 0.30, 0.30]})
    status, data = request_json(raw, 200)
    check("0.00/1.00/0.30 边界可提交", status == 200, str(status))

    print()
    if _failures:
        print(f"验收失败：{len(_failures)} 项 —— {_failures}")
        return 1
    print("全部验收通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
