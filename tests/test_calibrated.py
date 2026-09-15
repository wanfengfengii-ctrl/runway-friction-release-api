"""仪器漂移校准评估测试：等权相邻违序合并、样本校准、422 整份拒绝。

级联合并样例（CASCADE_BODY）：真值沿读数升序为 0.50 / 0.40 / 0.20 / 0.10，
每个新块都触发向前合并，最终四锚点同属一块（拟合值恰为 0.30，证明块成员
不丢失）；随后 0.45 / 0.35 合并为第二块（拟合值恰为 0.40），用于验证拟合值
落在既有判级边界上时沿用现有边界等级。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.friction import Rating, adjacent_violator_blocks
from app.main import app
from app.schemas import CalibratedFrictionInput
from app.service import assess_calibrated

client = TestClient(app)

ENDPOINT = "/api/v1/friction/calibrated-assess"

# 级联合并样例：块 0 = 读数 0.10–0.40（拟合值 0.30），块 1 = 读数 0.50/0.60（拟合值 0.40）
CASCADE_ANCHORS = [
    {"reading": 0.10, "true_value": 0.50},
    {"reading": 0.20, "true_value": 0.40},
    {"reading": 0.30, "true_value": 0.20},
    {"reading": 0.40, "true_value": 0.10},
    {"reading": 0.50, "true_value": 0.45},
    {"reading": 0.60, "true_value": 0.35},
]


def calibrated_body(**overrides):
    body = {
        "runway_id": "18L",
        # 逐字典复制，避免测试间通过共享的可变锚点字典互相污染
        "anchors": [dict(anchor) for anchor in CASCADE_ANCHORS],
        "front": [0.10, 0.50, 0.20],
        "middle": [0.50, 0.60, 0.50],
        "rear": [0.40, 0.40, 0.40],
    }
    body.update(overrides)
    return body


def post(body, **kwargs):
    return client.post(ENDPOINT, json=body, **kwargs)


# ---------- 等权相邻违序合并（领域规则） ----------


def test_pava_already_monotone_keeps_singleton_blocks():
    fitted, blocks = adjacent_violator_blocks([0.20, 0.30, 0.30, 0.50])
    assert fitted == [0.20, 0.30, 0.30, 0.50]
    # 单调不降（含相等）不构成违序：相邻等值不合并
    assert blocks == [0, 1, 2, 3]


def test_pava_single_violation_merges_with_equal_weight_mean():
    fitted, blocks = adjacent_violator_blocks([0.50, 0.30])
    assert fitted == [0.40, 0.40]
    assert blocks == [0, 0]


def test_pava_cascading_merges_keep_all_members():
    # 每个新块都触发向前合并：0.50 > 0.40 > 0.20 > 0.10 逐级并入同一块
    fitted, blocks = adjacent_violator_blocks([0.50, 0.40, 0.20, 0.10])
    assert blocks == [0, 0, 0, 0]  # 块成员不丢失：四个位置同属一块
    assert fitted == [0.30, 0.30, 0.30, 0.30]  # 等权算术均值 1.20/4，恰为 0.30


def test_pava_merged_block_merges_forward_again():
    # [0.50, 0.10] 先并成均值 0.30 的块，仍大于后继 0.20，须继续向前合并
    fitted, blocks = adjacent_violator_blocks([0.50, 0.10, 0.20])
    assert blocks == [0, 0, 0]
    assert fitted == pytest.approx([0.80 / 3] * 3)


def test_pava_later_blocks_do_not_disturb_earlier_solution():
    fitted, blocks = adjacent_violator_blocks([0.50, 0.40, 0.20, 0.10, 0.45, 0.35])
    assert blocks == [0, 0, 0, 0, 1, 1]
    assert fitted == [0.30, 0.30, 0.30, 0.30, 0.40, 0.40]


def test_pava_boundary_means_hit_exact_thresholds():
    # 块均值恰为 0.30 / 0.40：整数分运算保证精确落在既有判级边界上
    fitted, blocks = adjacent_violator_blocks([0.32, 0.28, 0.42, 0.38])
    assert blocks == [0, 0, 1, 1]
    assert fitted == [0.30, 0.30, 0.40, 0.40]


# ---------- 校准评估组装（服务层） ----------


def _payload(anchors, front, middle, rear, runway_id="18L", sampling="three_point"):
    return CalibratedFrictionInput(
        runway_id=runway_id,
        sampling=sampling,
        anchors=anchors,
        front=front,
        middle=middle,
        rear=rear,
    )


def test_assess_calibrated_cascade_blocks_and_segments():
    result = assess_calibrated(_payload(
        CASCADE_ANCHORS,
        [0.10, 0.50, 0.20],
        [0.50, 0.60, 0.50],
        [0.40, 0.40, 0.40],
    ))
    # 逐锚点：按读数升序返回，最终块归属与拟合值
    assert [a.reading for a in result.anchors] == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    assert [a.block for a in result.anchors] == [0, 0, 0, 0, 1, 1]
    assert [a.fitted_value for a in result.anchors] == [0.30] * 4 + [0.40] * 2
    assert [a.true_value for a in result.anchors] == [0.50, 0.40, 0.20, 0.10, 0.45, 0.35]

    # 逐段：原值与校正值并列，校正值进入中位数与判级
    front, middle, rear = result.segments
    assert front.raw_values == [0.10, 0.50, 0.20]
    assert front.calibrated_values == [0.30, 0.40, 0.30]
    assert front.median == 0.30 and front.rating is Rating.WATCH
    assert middle.calibrated_values == [0.40, 0.40, 0.40]
    assert middle.median == 0.40 and middle.rating is Rating.NORMAL
    assert rear.calibrated_values == [0.30, 0.30, 0.30]
    assert rear.median == 0.30 and rear.rating is Rating.WATCH
    assert result.overall_rating is Rating.WATCH
    assert result.worst_segments == ["front", "rear"]


def test_assess_calibrated_unsorted_anchors_are_sorted_in_response():
    shuffled = [CASCADE_ANCHORS[i] for i in (3, 0, 5, 1, 4, 2)]
    result = assess_calibrated(_payload(
        shuffled, [0.10, 0.10, 0.10], [0.60, 0.60, 0.60], [0.50, 0.50, 0.50],
    ))
    assert [a.reading for a in result.anchors] == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    assert [a.block for a in result.anchors] == [0, 0, 0, 0, 1, 1]


def test_assess_calibrated_monotone_true_values_keep_raw_readings():
    anchors = [
        {"reading": 0.30, "true_value": 0.31},
        {"reading": 0.40, "true_value": 0.42},
        {"reading": 0.50, "true_value": 0.53},
        {"reading": 0.60, "true_value": 0.64},
    ]
    result = assess_calibrated(_payload(
        anchors, [0.30, 0.40, 0.50], [0.40, 0.50, 0.60], [0.60, 0.60, 0.60],
    ))
    assert [a.block for a in result.anchors] == [0, 1, 2, 3]
    assert [a.fitted_value for a in result.anchors] == [0.31, 0.42, 0.53, 0.64]
    assert result.segments[0].calibrated_values == [0.31, 0.42, 0.53]
    assert result.segments[0].median == 0.42
    assert result.overall_rating is Rating.NORMAL


def test_assess_calibrated_five_point_trims_calibrated_values():
    anchors = [
        {"reading": 0.10, "true_value": 0.50},
        {"reading": 0.20, "true_value": 0.40},
        {"reading": 0.30, "true_value": 0.20},
        {"reading": 0.40, "true_value": 0.10},
        {"reading": 0.90, "true_value": 0.90},
    ]
    result = assess_calibrated(_payload(
        anchors,
        [0.10, 0.20, 0.30, 0.40, 0.90],
        [0.90] * 5,
        [0.90] * 5,
        sampling="five_point",
    ))
    front = result.segments[0]
    # 前四锚点并成一块（拟合值 0.30），0.90 自成一块
    assert front.raw_values == [0.10, 0.20, 0.30, 0.40, 0.90]
    assert front.calibrated_values == [0.30, 0.30, 0.30, 0.30, 0.90]
    # 五点去极值作用于校正值
    assert front.excluded_values == [0.30, 0.90]
    assert front.used_values == [0.30, 0.30, 0.30]
    assert front.median == 0.30 and front.rating is Rating.WATCH
    assert result.overall_rating is Rating.WATCH
    assert result.worst_segments == ["front"]


# ---------- HTTP：成功路径 ----------


def test_calibrated_success_response_shape():
    response = post(calibrated_body())
    assert response.status_code == 200, response.text
    data = response.json()

    assert set(data) == {"runway_id", "anchors", "segments", "overall_rating", "worst_segments"}
    assert data["runway_id"] == "18L"

    # 逐锚点：最终块归属与拟合值；级联合并后块成员不丢失
    assert [a["reading"] for a in data["anchors"]] == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    assert [a["block"] for a in data["anchors"]] == [0, 0, 0, 0, 1, 1]
    assert [a["fitted_value"] for a in data["anchors"]] == [0.30] * 4 + [0.40] * 2
    assert sum(1 for a in data["anchors"] if a["block"] == 0) == 4
    assert sum(1 for a in data["anchors"] if a["block"] == 1) == 2

    # 逐段：原值与校正值并列
    front, middle, rear = data["segments"]
    assert front["raw_values"] == [0.10, 0.50, 0.20]
    assert front["calibrated_values"] == [0.30, 0.40, 0.30]
    assert front["median"] == 0.30 and front["rating"] == "关注"
    assert middle["calibrated_values"] == [0.40, 0.40, 0.40]
    assert middle["median"] == 0.40 and middle["rating"] == "正常"
    assert rear["calibrated_values"] == [0.30, 0.30, 0.30]
    assert data["overall_rating"] == "关注"
    assert data["worst_segments"] == ["front", "rear"]


def test_calibrated_boundary_fitted_values_keep_existing_grades():
    # 拟合值恰为 0.30 -> 关注；恰为 0.40 -> 正常（沿用现有边界等级）
    data = post(calibrated_body(
        front=[0.10, 0.10, 0.10],   # 校准后 0.30
        middle=[0.50, 0.50, 0.50],  # 校准后 0.40
        rear=[0.60, 0.60, 0.60],    # 校准后 0.40
    )).json()
    ratings = {s["segment"]: (s["median"], s["rating"]) for s in data["segments"]}
    assert ratings["front"] == (0.30, "关注")
    assert ratings["middle"] == (0.40, "正常")
    assert ratings["rear"] == (0.40, "正常")
    assert data["overall_rating"] == "关注"
    assert data["worst_segments"] == ["front"]


def test_calibrated_five_point_response_carries_trim_fields():
    body = calibrated_body(
        sampling="five_point",
        front=[0.10, 0.20, 0.30, 0.40, 0.50],
        middle=[0.50, 0.50, 0.50, 0.60, 0.60],
        rear=[0.60] * 5,
    )
    data = post(body).json()
    front = data["segments"][0]
    assert front["raw_values"] == [0.10, 0.20, 0.30, 0.40, 0.50]
    assert front["calibrated_values"] == [0.30, 0.30, 0.30, 0.30, 0.40]
    assert front["excluded_values"] == [0.30, 0.40]
    assert front["used_values"] == [0.30, 0.30, 0.30]
    assert front["median"] == 0.30 and front["rating"] == "关注"


def test_calibrated_three_point_response_omits_five_point_fields():
    data = post(calibrated_body()).json()
    for segment in data["segments"]:
        assert "excluded_values" not in segment
        assert "used_values" not in segment


# ---------- HTTP：422 整份拒绝 ----------


def _assert_422_without_assessment(response):
    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload and payload["detail"]
    assert "segments" not in payload
    assert "overall_rating" not in payload
    assert "anchors" not in payload
    return payload


def _locs(payload):
    return [tuple(err["loc"]) for err in payload["detail"]]


def test_anchor_count_out_of_bounds_is_rejected():
    too_few = calibrated_body(anchors=CASCADE_ANCHORS[:3])
    payload = _assert_422_without_assessment(post(too_few))
    assert any("anchors" in loc for loc in _locs(payload))

    too_many = calibrated_body(anchors=CASCADE_ANCHORS + [
        {"reading": 0.70, "true_value": 0.70},
        {"reading": 0.71, "true_value": 0.71},
        {"reading": 0.72, "true_value": 0.72},
        {"reading": 0.73, "true_value": 0.73},
        {"reading": 0.74, "true_value": 0.74},
        {"reading": 0.75, "true_value": 0.75},
        {"reading": 0.76, "true_value": 0.76},
    ])
    payload = _assert_422_without_assessment(post(too_many))
    assert any("anchors" in loc for loc in _locs(payload))


def test_anchor_count_boundaries_4_and_12_are_accepted():
    four = calibrated_body(
        anchors=CASCADE_ANCHORS[:4],
        front=[0.10, 0.20, 0.30],
        middle=[0.20, 0.30, 0.40],
        rear=[0.40, 0.40, 0.40],
    )
    assert post(four).status_code == 200

    twelve = calibrated_body(anchors=CASCADE_ANCHORS + [
        {"reading": 0.70, "true_value": 0.70},
        {"reading": 0.71, "true_value": 0.71},
        {"reading": 0.72, "true_value": 0.72},
        {"reading": 0.73, "true_value": 0.73},
        {"reading": 0.74, "true_value": 0.74},
        {"reading": 0.75, "true_value": 0.75},
    ])
    assert post(twelve).status_code == 200


def test_anchor_out_of_range_is_rejected():
    bad_reading = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 1.01, "true_value": 0.50},
    ])
    payload = _assert_422_without_assessment(post(bad_reading))
    assert any("anchors" in loc for loc in _locs(payload))

    bad_true = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 0.70, "true_value": -0.01},
    ])
    payload = _assert_422_without_assessment(post(bad_true))
    assert any("anchors" in loc for loc in _locs(payload))


def test_anchor_precision_overflow_is_rejected():
    body = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 0.70, "true_value": 0.505},
    ])
    payload = _assert_422_without_assessment(post(body))
    assert any("anchors" in loc for loc in _locs(payload))

    body = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 0.705, "true_value": 0.50},
    ])
    payload = _assert_422_without_assessment(post(body))
    assert any("anchors" in loc for loc in _locs(payload))


def test_anchor_non_numeric_and_boolean_are_rejected():
    body = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": "0.70", "true_value": 0.50},
    ])
    _assert_422_without_assessment(post(body))

    body = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 0.70, "true_value": True},
    ])
    _assert_422_without_assessment(post(body))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_anchor_non_finite_is_rejected(token):
    raw = (
        '{"runway_id":"18L",'
        '"anchors":[{"reading":0.10,"true_value":0.50},'
        '{"reading":0.20,"true_value":0.40},'
        '{"reading":0.30,"true_value":0.20},'
        f'{{"reading":0.40,"true_value":{token}}}],'
        '"front":[0.10,0.20,0.30],'
        '"middle":[0.20,0.30,0.40],'
        '"rear":[0.30,0.40,0.10]}'
    )
    response = client.post(ENDPOINT, content=raw, headers={"content-type": "application/json"})
    payload = _assert_422_without_assessment(response)
    assert any("anchors" in loc for loc in _locs(payload))


def test_duplicate_anchor_readings_are_rejected():
    body = calibrated_body(anchors=CASCADE_ANCHORS[:3] + [
        {"reading": 0.30, "true_value": 0.60},
    ])
    payload = _assert_422_without_assessment(post(body))
    assert any("anchors" in loc for loc in _locs(payload))


def test_segment_sample_without_matching_anchor_is_rejected():
    for segment in ("front", "middle", "rear"):
        body = calibrated_body(**{segment: [0.10, 0.20, 0.35]})  # 0.35 无对应锚点
        payload = _assert_422_without_assessment(post(body))
        assert any(segment in loc for loc in _locs(payload))


def test_segment_sample_still_needs_coefficient_constraints():
    # 段样本本身仍须满足有限数 / 0–1 / 两位精度（此类值也必然无对应锚点）
    payload = _assert_422_without_assessment(post(calibrated_body(front=[0.10, 0.20, 1.5])))
    assert any("front" in loc for loc in _locs(payload))
    payload = _assert_422_without_assessment(post(calibrated_body(middle=[0.10, 0.20, 0.123])))
    assert any("middle" in loc for loc in _locs(payload))


# 超出 float 可表示范围的整数：float() 转换会 OverflowError，必须 422 而非 500
HUGE_INT = 10 ** 400


def test_huge_integer_anchor_values_are_rejected():
    for field in ("reading", "true_value"):
        anchors = [dict(anchor) for anchor in CASCADE_ANCHORS]
        anchors[0][field] = HUGE_INT
        payload = _assert_422_without_assessment(post(calibrated_body(anchors=anchors)))
        assert any("anchors" in loc for loc in _locs(payload))


def test_huge_integer_segment_sample_is_rejected():
    for segment in ("front", "middle", "rear"):
        body = calibrated_body(**{segment: [0.10, 0.20, HUGE_INT]})
        payload = _assert_422_without_assessment(post(body))
        assert any(segment in loc for loc in _locs(payload))


def test_huge_integer_error_response_stays_valid_json():
    # 超大整数回显在错误详情 input 中，响应仍须为合法 JSON 且不泄露部分判定
    payload = _assert_422_without_assessment(
        post(calibrated_body(front=[0.10, 0.20, HUGE_INT]))
    )
    assert payload["detail"][0]["input"] == [0.10, 0.20, HUGE_INT]


def test_calibrated_segment_count_follows_sampling_mode():
    payload = _assert_422_without_assessment(post(calibrated_body(front=[0.10, 0.20])))
    assert any("front" in loc for loc in _locs(payload))

    body = calibrated_body(sampling="five_point", middle=[0.50] * 4)
    payload = _assert_422_without_assessment(post(body))
    assert any("middle" in loc for loc in _locs(payload))


def test_calibrated_missing_or_extra_fields_are_rejected():
    body = calibrated_body()
    del body["anchors"]
    _assert_422_without_assessment(post(body))

    body = calibrated_body()
    del body["rear"]
    _assert_422_without_assessment(post(body))

    body = calibrated_body()
    body["side"] = [0.10, 0.20, 0.30]
    _assert_422_without_assessment(post(body))

    body = calibrated_body()
    body["anchors"][0]["extra"] = 1
    _assert_422_without_assessment(post(body))


def test_calibrated_blank_runway_and_bad_sampling_are_rejected():
    payload = _assert_422_without_assessment(post(calibrated_body(runway_id="   ")))
    assert any("runway_id" in loc for loc in _locs(payload))

    payload = _assert_422_without_assessment(post(calibrated_body(sampling="seven_point")))
    assert any("sampling" in loc for loc in _locs(payload))


def test_calibrated_multiple_errors_all_reported_but_no_partial_result():
    body = calibrated_body(front=[0.10, 0.20, 0.35], rear=[0.10, 0.20])
    payload = _assert_422_without_assessment(post(body))
    locs = _locs(payload)
    assert any("front" in loc for loc in locs)
    assert any("rear" in loc for loc in locs)


# ---------- 原评估入口保持不变 ----------


def test_original_assess_endpoint_is_untouched():
    body = {
        "runway_id": "18L",
        "front": [0.52, 0.31, 0.60],
        "middle": [0.40, 0.45, 0.90],
        "rear": [0.70, 0.80, 0.29],
    }
    data = client.post("/api/v1/friction/assess", json=body).json()
    assert set(data) == {"runway_id", "segments", "overall_rating", "worst_segments"}
    for segment in data["segments"]:
        assert set(segment) == {"segment", "label", "raw_values", "median", "rating"}
    assert [s["median"] for s in data["segments"]] == [0.52, 0.45, 0.70]
    assert data["overall_rating"] == "正常"
