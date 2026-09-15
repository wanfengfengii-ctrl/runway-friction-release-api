"""连续巡检剖面评估测试：内部驻点最优窗、并列取最早、临界均值判级、422 整份拒绝。

V_PROFILE：关于里程 200 对称的 V 形剖面（中间点为共线插值点），窗长 180 时
最低平均窗落在内部驻点 110（两端插值均为 0.47），严格优于全部断点候选
（测点里程与测点里程减窗长），证明不能只枚举测点起点。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.friction import Rating, grade_profile_average, lowest_average_window
from app.main import app
from app.schemas import ProfileFrictionInput
from app.service import assess_profile

client = TestClient(app)

ENDPOINT = "/api/v1/friction/profile-assess"

# V 形剖面：9 个测点（6–50 范围内），跑道长 400，窗长 180
V_MILEAGES = [0, 50, 100, 150, 200, 250, 300, 350, 400]
V_COEFFICIENTS = [0.80, 0.65, 0.50, 0.35, 0.20, 0.35, 0.50, 0.65, 0.80]
V_WINDOW = 180

# 对称三角波剖面：周期 200，窗长等于周期 -> 所有窗口平均值并列
WAVE_COEFFICIENTS = [0.50, 0.40, 0.30, 0.40, 0.50, 0.40, 0.30, 0.40, 0.50]


def profile_body(**overrides):
    body = {
        "runway_id": "18L",
        "runway_length": 400,
        "window_length": V_WINDOW,
        "points": [
            {"mileage": mileage, "coefficient": coefficient}
            for mileage, coefficient in zip(V_MILEAGES, V_COEFFICIENTS)
        ],
    }
    body.update(overrides)
    return body


def post(body, **kwargs):
    return client.post(ENDPOINT, json=body, **kwargs)


def _reference_window_average(points, window, start):
    """独立参考实现：分段线性插值 + 分段梯形面积（float，仅供对照）。"""

    def value_at(x):
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        raise AssertionError("参考积分越界")

    end = start + window
    grid = [start] + [x for x, _ in points if start < x < end] + [end]
    area = sum((b - a) * (value_at(a) + value_at(b)) / 2 for a, b in zip(grid, grid[1:]))
    return area / window


def _breakpoint_starts(points, runway_length, window):
    """断点候选起点：0、跑道长度减窗长、落在起点域内的测点里程及其减窗长位置。"""
    limit = runway_length - window
    cuts = {0.0, float(limit)}
    for mileage, _ in points:
        if 0 < mileage < limit:
            cuts.add(float(mileage))
        shifted = mileage - window
        if 0 < shifted < limit:
            cuts.add(float(shifted))
    return sorted(cuts)


# ---------- 领域规则：最低平均窗 ----------


def test_lowest_window_falls_on_interior_stationary_point():
    mileages = [Decimal(m) for m in V_MILEAGES]
    coefficients = [Decimal(str(c)) for c in V_COEFFICIENTS]
    window = lowest_average_window(mileages, coefficients, Decimal(V_WINDOW))

    # 最低窗落在内部驻点：不是测点里程，也不是测点里程减窗长
    points = list(zip(V_MILEAGES, V_COEFFICIENTS))
    breakpoints = _breakpoint_starts(points, 400, V_WINDOW)
    assert float(window.start) not in breakpoints

    assert window.start == Decimal(110)
    assert window.end == Decimal(290)
    assert window.start_value == Decimal("0.47")
    assert window.end_value == Decimal("0.47")  # 驻点两端插值相等
    assert window.area == Decimal("60.3")
    assert window.average == Decimal("0.335")

    # 严格优于所有断点候选（只枚举测点起点会错过该窗）
    best_breakpoint = min(_reference_window_average(points, V_WINDOW, s) for s in breakpoints)
    assert float(window.average) < best_breakpoint


def test_lowest_window_tie_picks_earliest_start():
    mileages = [Decimal(m) for m in V_MILEAGES]
    coefficients = [Decimal(str(c)) for c in WAVE_COEFFICIENTS]
    window = lowest_average_window(mileages, coefficients, Decimal(200))

    # 窗长等于三角波周期：每个起点平均值都是 0.40，并列时取起点最小者
    points = list(zip(V_MILEAGES, WAVE_COEFFICIENTS))
    for start in (0, 25, 75, 125, 200):
        assert _reference_window_average(points, 200, start) == pytest.approx(0.40)
    assert window.start == Decimal(0)
    assert window.end == Decimal(200)
    assert window.average == Decimal("0.4")


def test_lowest_window_flat_profile_starts_at_zero():
    mileages = [Decimal(m) for m in (0, 60, 120, 180, 240, 300)]
    coefficients = [Decimal("0.50")] * 6
    window = lowest_average_window(mileages, coefficients, Decimal(100))
    assert window.start == Decimal(0)
    assert window.average == Decimal("0.5")


def test_lowest_window_monotone_profiles_hit_domain_bounds():
    mileages = [Decimal(m) for m in (0, 100, 200, 300, 400, 500)]
    descending = [Decimal(str(c)) for c in (0.90, 0.80, 0.70, 0.60, 0.50, 0.40)]
    window = lowest_average_window(mileages, descending, Decimal(100))
    assert window.start == Decimal(400)  # 摩阻递减：最低窗贴跑道末端
    assert window.average == Decimal("0.45")

    ascending = list(reversed(descending))
    window = lowest_average_window(mileages, ascending, Decimal(100))
    assert window.start == Decimal(0)  # 摩阻递增：最低窗贴跑道起点
    assert window.average == Decimal("0.45")


def test_grade_profile_average_uses_unrounded_value():
    assert grade_profile_average(Decimal("0.40")) is Rating.NORMAL
    assert grade_profile_average(Decimal("0.30")) is Rating.WATCH
    # 未舍入值低于 0.30：即使六位小数输出会舍入到 0.300000，仍判关闭
    assert grade_profile_average(Decimal("0.2999998333333333333333333333")) is Rating.CLOSED
    assert grade_profile_average(Decimal("0.29")) is Rating.CLOSED


# ---------- 服务层：结果组装与输出舍入 ----------


def _payload(points, runway_length, window_length, runway_id="18L"):
    return ProfileFrictionInput(
        runway_id=runway_id,
        runway_length=runway_length,
        window_length=window_length,
        points=points,
    )


def test_assess_profile_assembles_window_fields():
    result = assess_profile(_payload(
        [{"mileage": m, "coefficient": c} for m, c in zip(V_MILEAGES, V_COEFFICIENTS)],
        400,
        V_WINDOW,
    ))
    assert result.runway_id == "18L"
    assert result.start_mileage == 110.0
    assert result.end_mileage == 290.0
    assert result.start_friction == 0.47
    assert result.end_friction == 0.47
    assert result.area == 60.3
    assert result.average == 0.335
    assert result.rating is Rating.WATCH


def test_assess_profile_rounds_output_but_grades_unrounded():
    # 微小凹陷：未舍入平均值 0.2999998333… < 0.30，六位小数输出舍入为 0.300000
    points = [
        {"mileage": 0, "coefficient": 0.30},
        {"mileage": 1, "coefficient": 0.30},
        {"mileage": 1.00005, "coefficient": 0.29},
        {"mileage": 1.0001, "coefficient": 0.30},
        {"mileage": 2, "coefficient": 0.30},
        {"mileage": 3, "coefficient": 0.30},
        {"mileage": 3.0001, "coefficient": 0.30},
    ]
    result = assess_profile(_payload(points, 3.0001, 3))
    assert result.average == 0.3  # 输出保留六位小数：0.300000
    assert result.area == 0.9  # 0.8999995 舍入为 0.900000
    assert result.rating is Rating.CLOSED  # 判级按未舍入值


# ---------- HTTP：成功路径 ----------


def test_profile_success_response_shape():
    response = post(profile_body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {
        "runway_id",
        "start_mileage",
        "end_mileage",
        "start_friction",
        "end_friction",
        "area",
        "average",
        "rating",
    }
    assert data["runway_id"] == "18L"


def test_profile_lowest_window_beats_all_breakpoint_candidates():
    data = post(profile_body()).json()
    assert data["start_mileage"] == 110
    assert data["end_mileage"] == 290
    assert data["start_friction"] == 0.47
    assert data["end_friction"] == 0.47
    assert data["area"] == 60.3
    assert data["average"] == 0.335
    assert data["rating"] == "关注"

    # 结果优于所有断点候选：起点不落在任何断点上，且平均值严格更低
    points = list(zip(V_MILEAGES, V_COEFFICIENTS))
    breakpoints = _breakpoint_starts(points, 400, V_WINDOW)
    assert data["start_mileage"] not in breakpoints
    assert data["average"] < min(
        _reference_window_average(points, V_WINDOW, s) for s in breakpoints
    )


def test_profile_symmetric_profile_tie_picks_earliest_window():
    body = profile_body(
        window_length=200,
        points=[
            {"mileage": mileage, "coefficient": coefficient}
            for mileage, coefficient in zip(V_MILEAGES, WAVE_COEFFICIENTS)
        ],
    )
    data = post(body).json()
    assert data["start_mileage"] == 0
    assert data["end_mileage"] == 200
    assert data["average"] == 0.4
    assert data["rating"] == "正常"


def test_profile_critical_average_grading():
    def flat_body(coefficient):
        return profile_body(
            window_length=100,
            runway_length=300,
            points=[
                {"mileage": mileage, "coefficient": coefficient}
                for mileage in (0, 60, 120, 180, 240, 300)
            ],
        )

    normal = post(flat_body(0.40)).json()
    assert normal["average"] == 0.4 and normal["rating"] == "正常"

    watch = post(flat_body(0.30)).json()
    assert watch["average"] == 0.3 and watch["rating"] == "关注"

    closed = post(flat_body(0.29)).json()
    assert closed["average"] == 0.29 and closed["rating"] == "关闭"


def test_profile_unrounded_average_decides_rating():
    # 未舍入平均值 0.2999998333…：输出舍入到 0.300000，判级仍为关闭
    body = profile_body(
        runway_length=3.0001,
        window_length=3,
        points=[
            {"mileage": 0, "coefficient": 0.30},
            {"mileage": 1, "coefficient": 0.30},
            {"mileage": 1.00005, "coefficient": 0.29},
            {"mileage": 1.0001, "coefficient": 0.30},
            {"mileage": 2, "coefficient": 0.30},
            {"mileage": 3, "coefficient": 0.30},
            {"mileage": 3.0001, "coefficient": 0.30},
        ],
    )
    data = post(body).json()
    assert data["average"] == 0.3
    assert data["rating"] == "关闭"


def test_existing_assess_endpoints_are_unchanged():
    # 既有三点 / 五点入口响应不变
    data = client.post("/api/v1/friction/assess", json={
        "runway_id": "18L",
        "front": [0.52, 0.31, 0.60],
        "middle": [0.40, 0.45, 0.90],
        "rear": [0.70, 0.80, 0.29],
    }).json()
    assert set(data) == {"runway_id", "segments", "overall_rating", "worst_segments"}
    assert [s["median"] for s in data["segments"]] == [0.52, 0.45, 0.70]
    assert data["overall_rating"] == "正常"

    # 既有校准评估入口响应不变（级联合并样例）
    data = client.post("/api/v1/friction/calibrated-assess", json={
        "runway_id": "18L",
        "anchors": [
            {"reading": 0.10, "true_value": 0.50},
            {"reading": 0.20, "true_value": 0.40},
            {"reading": 0.30, "true_value": 0.20},
            {"reading": 0.40, "true_value": 0.10},
            {"reading": 0.50, "true_value": 0.45},
            {"reading": 0.60, "true_value": 0.35},
        ],
        "front": [0.10, 0.50, 0.20],
        "middle": [0.50, 0.60, 0.50],
        "rear": [0.40, 0.40, 0.40],
    }).json()
    assert [a["block"] for a in data["anchors"]] == [0, 0, 0, 0, 1, 1]
    assert data["overall_rating"] == "关注"
    assert data["worst_segments"] == ["front", "rear"]


# ---------- 422 整份拒绝 ----------


def _assert_422_without_assessment(response):
    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload and payload["detail"]
    assert "average" not in payload
    assert "rating" not in payload
    return payload


def _locs(payload):
    return [tuple(err["loc"]) for err in payload["detail"]]


def test_point_count_out_of_bounds_is_rejected():
    # 5 个测点（里程本身合法、覆盖跑道全程，仅数量不足）
    too_few = profile_body(points=[
        {"mileage": mileage, "coefficient": 0.50}
        for mileage in (0, 100, 200, 300, 400)
    ])
    payload = _assert_422_without_assessment(post(too_few))
    assert any("points" in loc for loc in _locs(payload))

    # 51 个测点（里程合法，仅数量超限）
    too_many_points = [
        {"mileage": i * 8, "coefficient": 0.50} for i in range(50)
    ] + [{"mileage": 400, "coefficient": 0.50}]
    payload = _assert_422_without_assessment(post(profile_body(points=too_many_points)))
    assert any("points" in loc for loc in _locs(payload))


def test_point_count_boundaries_6_and_50_are_accepted():
    six = profile_body(points=[
        {"mileage": mileage, "coefficient": 0.50}
        for mileage in (0, 80, 160, 240, 320, 400)
    ])
    assert post(six).status_code == 200

    fifty = profile_body(points=[
        {"mileage": i * 8, "coefficient": 0.50} for i in range(49)
    ] + [{"mileage": 400, "coefficient": 0.50}])
    assert len(fifty["points"]) == 50
    assert post(fifty).status_code == 200


def test_duplicate_or_regressing_mileages_are_rejected():
    points = profile_body()["points"]
    points[3] = {"mileage": 100, "coefficient": 0.50}  # 与前一测点重复
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))

    points = profile_body()["points"]
    points[4], points[5] = points[5], points[4]  # 里程倒退
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))


def test_out_of_range_mileage_is_rejected():
    points = profile_body()["points"]
    points[0] = {"mileage": -0.5, "coefficient": 0.80}
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("mileage" in loc for loc in _locs(payload))

    points = profile_body()["points"]
    points[-1] = {"mileage": 400.01, "coefficient": 0.80}  # 超出跑道长度
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))


def test_mileages_must_cover_zero_and_runway_end():
    points = profile_body()["points"]
    points[0] = {"mileage": 0.5, "coefficient": 0.80}  # 未覆盖零点
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))

    points = profile_body()["points"]
    points[-1] = {"mileage": 399, "coefficient": 0.80}  # 未覆盖跑道终点
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))


def test_window_length_bounds_are_enforced():
    for bad_window in (0, -1, 400, 400.01):
        payload = _assert_422_without_assessment(post(profile_body(window_length=bad_window)))
        assert any("window_length" in loc for loc in _locs(payload))


def test_runway_length_must_be_positive():
    for bad_length in (0, -400):
        payload = _assert_422_without_assessment(post(profile_body(runway_length=bad_length)))
        assert any("runway_length" in loc for loc in _locs(payload))


def test_coefficient_range_and_precision_are_enforced():
    for bad in (1.01, -0.01, 0.123):
        points = profile_body()["points"]
        points[2] = {"mileage": 100, "coefficient": bad}
        payload = _assert_422_without_assessment(post(profile_body(points=points)))
        assert any("coefficient" in loc for loc in _locs(payload))


def test_non_numeric_and_boolean_values_are_rejected():
    points = profile_body()["points"]
    points[0] = {"mileage": 0, "coefficient": "0.80"}
    _assert_422_without_assessment(post(profile_body(points=points)))

    points = profile_body()["points"]
    points[0] = {"mileage": 0, "coefficient": True}
    _assert_422_without_assessment(post(profile_body(points=points)))

    points = profile_body()["points"]
    points[0] = {"mileage": "0", "coefficient": 0.80}
    _assert_422_without_assessment(post(profile_body(points=points)))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_rejected(token):
    for field in ("coefficient", "mileage"):
        raw_points = ",".join(
            f'{{"mileage":{m},"coefficient":{c}}}'
            for m, c in zip(V_MILEAGES, V_COEFFICIENTS)
        )
        raw = (
            '{"runway_id":"18L","runway_length":400,"window_length":180,'
            f'"points":[{raw_points}]}}'
        ).replace(
            '{"mileage":100,"coefficient":0.5}',
            '{"mileage":100,"coefficient":%s}' % token
            if field == "coefficient"
            else '{"mileage":%s,"coefficient":0.5}' % token,
            1,
        )
        response = client.post(ENDPOINT, content=raw, headers={"content-type": "application/json"})
        payload = _assert_422_without_assessment(response)
        assert any("points" in loc for loc in _locs(payload))


def test_non_finite_window_and_runway_length_are_rejected():
    for field in ("window_length", "runway_length"):
        raw = (
            '{"runway_id":"18L","runway_length":400,"window_length":180,"points":[]}'
        ).replace(f'"{field}":180' if field == "window_length" else f'"{field}":400',
                  f'"{field}":Infinity')
        response = client.post(ENDPOINT, content=raw, headers={"content-type": "application/json"})
        _assert_422_without_assessment(response)


def test_huge_integer_values_are_rejected():
    points = profile_body()["points"]
    points[2] = {"mileage": 100, "coefficient": 10 ** 400}
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("coefficient" in loc for loc in _locs(payload))

    points = profile_body()["points"]
    points[2] = {"mileage": 10 ** 400, "coefficient": 0.50}
    payload = _assert_422_without_assessment(post(profile_body(points=points)))
    assert any("points" in loc for loc in _locs(payload))


def test_missing_and_extra_keys_are_rejected():
    body = profile_body()
    del body["points"]
    _assert_422_without_assessment(post(body))

    body = profile_body()
    body["runway_name"] = "18L"
    _assert_422_without_assessment(post(body))

    points = profile_body()["points"]
    points[0] = {"mileage": 0, "coefficient": 0.80, "device": "CFT"}
    _assert_422_without_assessment(post(profile_body(points=points)))

    points = profile_body()["points"]
    del points[0]["coefficient"]
    _assert_422_without_assessment(post(profile_body(points=points)))


def test_duplicate_json_key_is_rejected():
    raw = (
        '{"runway_id":"18L","runway_length":400,"window_length":180,'
        '"window_length":200,"points":[]}'
    )
    response = client.post(ENDPOINT, content=raw, headers={"content-type": "application/json"})
    payload = _assert_422_without_assessment(response)
    assert payload["detail"][0]["loc"][-1] == "window_length"


def test_blank_runway_id_is_rejected():
    _assert_422_without_assessment(post(profile_body(runway_id="   ")))
    _assert_422_without_assessment(post(profile_body(runway_id="")))


def test_points_must_be_a_list():
    _assert_422_without_assessment(post(profile_body(points={"mileage": 0})))
