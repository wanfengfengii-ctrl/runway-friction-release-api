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
CALIBRATED_ENDPOINT = f"{BASE_URL}/api/v1/friction/calibrated-assess"

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" —— {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def request_json(raw: str | bytes, expect_status: int, endpoint: str = ENDPOINT):
    body = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    req = urllib.request.Request(
        endpoint,
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
        ("超大整数样本", json.dumps({"runway_id": "18", "front": [10 ** 400, 0.5, 0.5],
                                     "middle": [0.5] * 3, "rear": [0.5] * 3})),
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

    # 6. 五点稳健采样：抵抗单个异常高/低值，剔除值与参与判定三值随段返回
    body = {
        "runway_id": "18L",
        "sampling": "five_point",
        "front": [0.42, 0.43, 0.44, 0.45, 0.99],   # 单个异常高值
        "middle": [0.01, 0.42, 0.43, 0.44, 0.45],  # 单个异常低值
        "rear": [0.60, 0.65, 0.70, 0.75, 0.80],
    }
    status, data = request_json(json.dumps(body), 200)
    check("五点模式返回 200", status == 200, str(status))
    if status == 200:
        by_name = {s["segment"]: s for s in data["segments"]}
        front, middle = by_name["front"], by_name["middle"]
        check("五点模式抵抗单个异常高值",
              front["median"] == 0.44 and front["rating"] == "正常", str(front))
        check("  └ 高值剔除与参与值正确",
              front["excluded_values"] == [0.42, 0.99]
              and front["used_values"] == [0.43, 0.44, 0.45]
              and front["raw_values"] == body["front"], str(front))
        check("五点模式抵抗单个异常低值",
              middle["median"] == 0.43 and middle["rating"] == "正常", str(middle))
        check("  └ 低值剔除与参与值正确",
              middle["excluded_values"] == [0.01, 0.45]
              and middle["used_values"] == [0.42, 0.43, 0.44], str(middle))

    # 7. 五点模式临界落级：0.40→正常，0.30→关注，0.29→关闭，最差段接管
    body = {
        "runway_id": "36R",
        "sampling": "five_point",
        "front": [0.10, 0.39, 0.40, 0.50, 0.90],
        "middle": [0.10, 0.20, 0.30, 0.80, 0.90],
        "rear": [0.10, 0.20, 0.29, 0.80, 0.90],
    }
    status, data = request_json(json.dumps(body), 200)
    check("五点临界请求返回 200", status == 200, str(status))
    if status == 200:
        ratings = {s["segment"]: (s["median"], s["rating"]) for s in data["segments"]}
        check("五点中位数 0.40 落级正常", ratings["front"] == (0.40, "正常"), str(ratings["front"]))
        check("五点中位数 0.30 落级关注", ratings["middle"] == (0.30, "关注"), str(ratings["middle"]))
        check("五点中位数 0.29 落级关闭", ratings["rear"] == (0.29, "关闭"), str(ratings["rear"]))
        check("五点最差段(后段)接管结论",
              data["overall_rating"] == "关闭" and data["worst_segments"] == ["rear"],
              str(data.get("worst_segments")))

    # 8. 多个相同极值只按位置各剔除一个
    body = {
        "runway_id": "09",
        "sampling": "five_point",
        "front": [0.10, 0.10, 0.35, 0.50, 0.90],   # 两个相同最低值
        "middle": [0.10, 0.30, 0.35, 0.90, 0.90],  # 两个相同最高值
        "rear": [0.50, 0.50, 0.50, 0.50, 0.50],
    }
    status, data = request_json(json.dumps(body), 200)
    check("相同极值请求返回 200", status == 200, str(status))
    if status == 200:
        by_name = {s["segment"]: s for s in data["segments"]}
        front, middle = by_name["front"], by_name["middle"]
        check("相同最低值只剔除一个",
              front["excluded_values"] == [0.10, 0.90]
              and front["used_values"] == [0.10, 0.35, 0.50]
              and front["median"] == 0.35, str(front))
        check("相同最高值只剔除一个",
              middle["excluded_values"] == [0.10, 0.90]
              and middle["used_values"] == [0.30, 0.35, 0.90]
              and middle["median"] == 0.35, str(middle))

    # 9. 三值旧请求返回原结构（不含五点字段），判级结果不变
    body = {
        "runway_id": "18L",
        "front": [0.52, 0.31, 0.60],
        "middle": [0.40, 0.45, 0.90],
        "rear": [0.70, 0.80, 0.29],
    }
    status, data = request_json(json.dumps(body), 200)
    check("旧请求返回 200", status == 200, str(status))
    if status == 200:
        check("旧请求段结构保持原样（无五点字段）",
              all(set(s) == {"segment", "label", "raw_values", "median", "rating"}
                  for s in data["segments"]),
              str(data["segments"][0]))
        check("旧请求判级结果不变",
              data["overall_rating"] == "正常"
              and [s["median"] for s in data["segments"]] == [0.52, 0.45, 0.70],
              str(data.get("overall_rating")))

    # 10. 五点模式 422 整份拒绝：段值数不符、越界、精度、非有限数、非法采样方式
    five_rejected: list[tuple[str, str | bytes, str]] = [
        ("五点模式混入四值段", json.dumps({"runway_id": "18", "sampling": "five_point",
                                           "front": [0.5] * 5, "middle": [0.5] * 4,
                                           "rear": [0.5] * 5}), "middle"),
        ("五点模式混入三值段", json.dumps({"runway_id": "18", "sampling": "five_point",
                                           "front": [0.5] * 5, "middle": [0.5] * 5,
                                           "rear": [0.5] * 3}), "rear"),
        ("五点模式越界 1.01", json.dumps({"runway_id": "18", "sampling": "five_point",
                                          "front": [1.01, 0.5, 0.5, 0.5, 0.5],
                                          "middle": [0.5] * 5, "rear": [0.5] * 5}), "front"),
        ("五点模式精度超限 0.123", json.dumps({"runway_id": "18", "sampling": "five_point",
                                               "front": [0.5] * 5,
                                               "middle": [0.123, 0.5, 0.5, 0.5, 0.5],
                                               "rear": [0.5] * 5}), "middle"),
        ("五点模式非有限值 NaN",
         b'{"runway_id":"18","sampling":"five_point","front":[0.5,0.5,0.5,0.5,NaN],'
         b'"middle":[0.5,0.5,0.5,0.5,0.5],"rear":[0.5,0.5,0.5,0.5,0.5]}', "front"),
        ("非法采样方式", json.dumps({"runway_id": "18", "sampling": "seven_point",
                                     "front": [0.5] * 3, "middle": [0.5] * 3,
                                     "rear": [0.5] * 3}), "sampling"),
    ]
    for name, raw, segment in five_rejected:
        status, data = request_json(raw, 422)
        ok = status == 422 and isinstance(data.get("detail"), list) and bool(data["detail"])
        check(f"422 拒绝：{name}", ok, f"status={status}, body={str(data)[:160]}")
        if ok:
            locs = [err.get("loc", []) for err in data["detail"]]
            check(f"  └ 错误位置指向 {segment}",
                  any(segment in loc for loc in locs), str(locs))
            check(f"  └ 无部分判定：{name}",
                  "segments" not in data and "overall_rating" not in data)

    # 11. 仪器漂移校准：级联合并样例（真值 0.50/0.40/0.20/0.10 逐级并入一块，
    #     拟合值恰为 0.30；0.45/0.35 并成第二块，拟合值恰为 0.40）
    cascade_anchors = [
        {"reading": 0.10, "true_value": 0.50},
        {"reading": 0.20, "true_value": 0.40},
        {"reading": 0.30, "true_value": 0.20},
        {"reading": 0.40, "true_value": 0.10},
        {"reading": 0.50, "true_value": 0.45},
        {"reading": 0.60, "true_value": 0.35},
    ]
    body = {
        "runway_id": "18L",
        "anchors": cascade_anchors,
        "front": [0.10, 0.50, 0.20],
        "middle": [0.50, 0.60, 0.50],
        "rear": [0.40, 0.40, 0.40],
    }
    status, data = request_json(json.dumps(body), 200, CALIBRATED_ENDPOINT)
    check("校准评估返回 200", status == 200, str(status))
    if status == 200:
        anchors = data["anchors"]
        check("锚点按读数升序返回",
              [a["reading"] for a in anchors] == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60],
              str(anchors))
        check("级联合并后块归属正确",
              [a["block"] for a in anchors] == [0, 0, 0, 0, 1, 1],
              str([a["block"] for a in anchors]))
        check("块成员不丢失（块0含4个、块1含2个锚点）",
              sum(1 for a in anchors if a["block"] == 0) == 4
              and sum(1 for a in anchors if a["block"] == 1) == 2, str(anchors))
        check("块拟合值为成员真值算术均值（恰为 0.30 / 0.40）",
              [a["fitted_value"] for a in anchors] == [0.30] * 4 + [0.40] * 2,
              str([a["fitted_value"] for a in anchors]))
        by_name = {s["segment"]: s for s in data["segments"]}
        front, middle = by_name["front"], by_name["middle"]
        check("段结果并列原值与校正值",
              front["raw_values"] == [0.10, 0.50, 0.20]
              and front["calibrated_values"] == [0.30, 0.40, 0.30], str(front))
        check("拟合值 0.30 落级关注（沿用现有边界）",
              front["median"] == 0.30 and front["rating"] == "关注", str(front))
        check("拟合值 0.40 落级正常（沿用现有边界）",
              middle["median"] == 0.40 and middle["rating"] == "正常", str(middle))
        check("校准后最差段接管结论",
              data["overall_rating"] == "关注"
              and data["worst_segments"] == ["front", "rear"],
              str(data.get("worst_segments")))

    # 12. 校准评估 422 整份拒绝：锚点数量、重复读数、样本引用、精度、非有限数
    calibrated_rejected: list[tuple[str, str | bytes, str]] = [
        ("锚点不足 4 个", json.dumps({"runway_id": "18", "anchors": cascade_anchors[:3],
                                      "front": [0.10, 0.20, 0.30], "middle": [0.10, 0.20, 0.30],
                                      "rear": [0.10, 0.20, 0.30]}), "anchors"),
        ("锚点读数重复", json.dumps({"runway_id": "18", "anchors": cascade_anchors[:3] + [
            {"reading": 0.30, "true_value": 0.60}],
            "front": [0.10, 0.20, 0.30], "middle": [0.10, 0.20, 0.30],
            "rear": [0.10, 0.20, 0.30]}), "anchors"),
        ("锚点真值精度超限", json.dumps({"runway_id": "18", "anchors": cascade_anchors[:3] + [
            {"reading": 0.70, "true_value": 0.505}],
            "front": [0.10, 0.20, 0.30], "middle": [0.10, 0.20, 0.30],
            "rear": [0.10, 0.20, 0.30]}), "anchors"),
        ("锚点读数越界", json.dumps({"runway_id": "18", "anchors": cascade_anchors[:3] + [
            {"reading": 1.01, "true_value": 0.50}],
            "front": [0.10, 0.20, 0.30], "middle": [0.10, 0.20, 0.30],
            "rear": [0.10, 0.20, 0.30]}), "anchors"),
        ("段样本无对应锚点", json.dumps({"runway_id": "18", "anchors": cascade_anchors,
                                         "front": [0.10, 0.20, 0.35],
                                         "middle": [0.10, 0.20, 0.30],
                                         "rear": [0.10, 0.20, 0.30]}), "front"),
        ("锚点超大整数", json.dumps({"runway_id": "18", "anchors": [
            {"reading": 0.10, "true_value": 10 ** 400}] + cascade_anchors[1:4],
            "front": [0.10, 0.20, 0.30], "middle": [0.10, 0.20, 0.30],
            "rear": [0.10, 0.20, 0.30]}), "anchors"),
        ("段样本超大整数", json.dumps({"runway_id": "18", "anchors": cascade_anchors,
                                       "front": [0.10, 0.20, 10 ** 400],
                                       "middle": [0.10, 0.20, 0.30],
                                       "rear": [0.10, 0.20, 0.30]}), "front"),
        ("锚点非有限值 NaN",
         b'{"runway_id":"18","anchors":[{"reading":0.10,"true_value":0.50},'
         b'{"reading":0.20,"true_value":0.40},{"reading":0.30,"true_value":0.20},'
         b'{"reading":0.40,"true_value":NaN}],'
         b'"front":[0.10,0.20,0.30],"middle":[0.10,0.20,0.30],"rear":[0.10,0.20,0.30]}',
         "anchors"),
    ]
    for name, raw, field in calibrated_rejected:
        status, data = request_json(raw, 422, CALIBRATED_ENDPOINT)
        ok = status == 422 and isinstance(data.get("detail"), list) and bool(data["detail"])
        check(f"422 拒绝：{name}", ok, f"status={status}, body={str(data)[:160]}")
        if ok:
            locs = [err.get("loc", []) for err in data["detail"]]
            check(f"  └ 错误位置指向 {field}",
                  any(field in loc for loc in locs), str(locs))
            check(f"  └ 无部分判定：{name}",
                  "segments" not in data and "overall_rating" not in data)

    print()
    if _failures:
        print(f"验收失败：{len(_failures)} 项 —— {_failures}")
        return 1
    print("全部验收通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
