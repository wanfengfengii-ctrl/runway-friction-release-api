"""HTTP 层测试：成功响应组装与 422 整份拒绝。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def valid_body(**overrides):
    body = {
        "runway_id": "18L",
        "front": [0.52, 0.31, 0.60],
        "middle": [0.40, 0.45, 0.90],
        "rear": [0.70, 0.80, 0.29],
    }
    body.update(overrides)
    return body


def post(body, **kwargs):
    return client.post("/api/v1/friction/assess", json=body, **kwargs)


# ---------- 成功路径 ----------

def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_success_response_shape_and_order():
    response = post(valid_body())
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["runway_id"] == "18L"
    assert [s["segment"] for s in data["segments"]] == ["front", "middle", "rear"]

    front, middle, rear = data["segments"]
    assert front["raw_values"] == [0.52, 0.31, 0.60]
    assert front["median"] == 0.52
    assert front["rating"] == "正常"

    assert middle["raw_values"] == [0.40, 0.45, 0.90]
    assert middle["median"] == 0.45
    assert middle["rating"] == "正常"

    assert rear["raw_values"] == [0.70, 0.80, 0.29]
    assert rear["median"] == 0.70
    assert rear["rating"] == "正常"

    assert data["overall_rating"] == "正常"
    assert data["worst_segments"] == ["front", "middle", "rear"]


def test_worst_segment_takes_over_overall_rating():
    body = valid_body(
        front=[0.80, 0.70, 0.90],
        middle=[0.10, 0.29, 0.20],
        rear=[0.60, 0.50, 0.70],
    )
    data = post(body).json()
    assert data["overall_rating"] == "关闭"
    assert [s["rating"] for s in data["segments"]] == ["正常", "关闭", "正常"]
    assert data["worst_segments"] == ["middle"]


def test_critical_median_040_is_normal_030_is_watch():
    body = valid_body(
        front=[0.39, 0.40, 0.50],
        middle=[0.20, 0.30, 0.80],
        rear=[0.10, 0.29, 0.90],
    )
    data = post(body).json()
    ratings = {s["segment"]: (s["median"], s["rating"]) for s in data["segments"]}
    assert ratings["front"] == (0.40, "正常")
    assert ratings["middle"] == (0.30, "关注")
    assert ratings["rear"] == (0.29, "关闭")
    assert data["overall_rating"] == "关闭"
    assert data["worst_segments"] == ["rear"]


# ---------- 422 整份拒绝 ----------

def _assert_422_without_assessment(response):
    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload and payload["detail"]
    assert "segments" not in payload
    assert "overall_rating" not in payload


def test_wrong_value_count_is_rejected():
    _assert_422_without_assessment(post(valid_body(front=[0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(rear=[0.5, 0.5, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(middle=[])))


def test_out_of_range_is_rejected():
    _assert_422_without_assessment(post(valid_body(front=[-0.01, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(front=[1.01, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(front=[1.001, 0.5, 0.5])))


def test_precision_overflow_is_rejected():
    _assert_422_without_assessment(post(valid_body(front=[0.123, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(middle=[0.405, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(rear=[0.301, 0.5, 0.5])))


def test_non_numeric_and_boolean_values_are_rejected():
    _assert_422_without_assessment(post(valid_body(front=["0.5", 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(front=[None, 0.5, 0.5])))
    _assert_422_without_assessment(post(valid_body(front=[True, 0.5, 0.5])))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_rejected(token):
    raw = (
        '{"runway_id":"18L",'
        f'"front":[0.5,0.5,{token}],'
        '"middle":[0.5,0.5,0.5],'
        '"rear":[0.5,0.5,0.5]}'
    )
    response = client.post(
        "/api/v1/friction/assess",
        content=raw,
        headers={"content-type": "application/json"},
    )
    _assert_422_without_assessment(response)


def test_missing_segment_is_rejected():
    body = valid_body()
    del body["middle"]
    _assert_422_without_assessment(post(body))


def test_unknown_segment_is_rejected():
    body = valid_body()
    body["side"] = [0.5, 0.5, 0.5]
    _assert_422_without_assessment(post(body))


def test_duplicate_segment_key_is_rejected():
    raw = (
        '{"runway_id":"18L",'
        '"front":[0.5,0.5,0.5],'
        '"middle":[0.4,0.4,0.4],'
        '"middle":[0.1,0.1,0.1],'
        '"rear":[0.6,0.6,0.6]}'
    )
    response = client.post(
        "/api/v1/friction/assess",
        content=raw,
        headers={"content-type": "application/json"},
    )
    _assert_422_without_assessment(response)
    assert response.json()["detail"][0]["loc"][-1] == "middle"


def test_blank_runway_id_is_rejected():
    _assert_422_without_assessment(post(valid_body(runway_id="   ")))
    _assert_422_without_assessment(post(valid_body(runway_id="")))


def test_malformed_json_and_empty_body_are_rejected():
    headers = {"content-type": "application/json"}
    _assert_422_without_assessment(
        client.post("/api/v1/friction/assess", content=b"{not json", headers=headers)
    )
    _assert_422_without_assessment(
        client.post("/api/v1/friction/assess", content=b"", headers=headers)
    )


def test_multiple_errors_all_reported_but_no_partial_result():
    body = valid_body(front=[1.5, 0.2, 0.3], rear=[0.1, 0.2])
    response = post(body)
    _assert_422_without_assessment(response)
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert any("front" in loc for loc in locs)
    assert any("rear" in loc for loc in locs)


def test_boundary_values_000_and_100_are_accepted():
    body = valid_body(
        front=[0.00, 0.00, 0.00],
        middle=[1.00, 1.00, 1.00],
        rear=[0.30, 0.30, 0.30],
    )
    response = post(body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [s["rating"] for s in data["segments"]] == ["关闭", "正常", "关注"]
    assert data["overall_rating"] == "关闭"


# ---------- 五点稳健采样 ----------

def five_point_body(**overrides):
    body = {
        "runway_id": "18L",
        "sampling": "five_point",
        "front": [0.42, 0.43, 0.44, 0.45, 0.99],
        "middle": [0.01, 0.42, 0.43, 0.44, 0.45],
        "rear": [0.60, 0.65, 0.70, 0.75, 0.80],
    }
    body.update(overrides)
    return body


def test_five_point_success_response_shape():
    response = post(five_point_body())
    assert response.status_code == 200, response.text
    data = response.json()

    assert [s["segment"] for s in data["segments"]] == ["front", "middle", "rear"]
    front, middle, rear = data["segments"]

    # 单个异常高值被剔除：原始五值、剔除值、参与判定三值随段返回
    assert front["raw_values"] == [0.42, 0.43, 0.44, 0.45, 0.99]
    assert front["excluded_values"] == [0.42, 0.99]
    assert front["used_values"] == [0.43, 0.44, 0.45]
    assert front["median"] == 0.44
    assert front["rating"] == "正常"

    # 单个异常低值被剔除
    assert middle["excluded_values"] == [0.01, 0.45]
    assert middle["used_values"] == [0.42, 0.43, 0.44]
    assert middle["median"] == 0.43
    assert middle["rating"] == "正常"

    assert rear["median"] == 0.70
    assert data["overall_rating"] == "正常"
    assert data["worst_segments"] == ["front", "middle", "rear"]


def test_five_point_critical_boundaries():
    body = five_point_body(
        front=[0.10, 0.39, 0.40, 0.50, 0.90],
        middle=[0.10, 0.20, 0.30, 0.80, 0.90],
        rear=[0.10, 0.20, 0.29, 0.80, 0.90],
    )
    data = post(body).json()
    ratings = {s["segment"]: (s["median"], s["rating"]) for s in data["segments"]}
    assert ratings["front"] == (0.40, "正常")
    assert ratings["middle"] == (0.30, "关注")
    assert ratings["rear"] == (0.29, "关闭")
    assert data["overall_rating"] == "关闭"
    assert data["worst_segments"] == ["rear"]


def test_five_point_duplicate_extremes_removed_once_by_position():
    body = five_point_body(
        front=[0.10, 0.10, 0.35, 0.50, 0.90],
        middle=[0.10, 0.30, 0.35, 0.90, 0.90],
        rear=[0.50] * 5,
    )
    data = post(body).json()
    front, middle, _ = data["segments"]
    assert front["excluded_values"] == [0.10, 0.90]
    assert front["used_values"] == [0.10, 0.35, 0.50]
    assert front["median"] == 0.35
    assert middle["excluded_values"] == [0.10, 0.90]
    assert middle["used_values"] == [0.30, 0.35, 0.90]
    assert middle["median"] == 0.35


def test_legacy_three_value_response_has_original_shape():
    data = post(valid_body()).json()
    assert set(data) == {"runway_id", "segments", "overall_rating", "worst_segments"}
    for segment in data["segments"]:
        assert set(segment) == {"segment", "label", "raw_values", "median", "rating"}


def test_five_point_mixed_four_value_segment_is_rejected():
    response = post(five_point_body(middle=[0.50, 0.50, 0.50, 0.50]))
    _assert_422_without_assessment(response)
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert any("middle" in loc for loc in locs)


def test_five_point_three_value_segment_is_rejected():
    response = post(five_point_body(rear=[0.50, 0.50, 0.50]))
    _assert_422_without_assessment(response)
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert any("rear" in loc for loc in locs)


def test_five_point_invalid_values_are_rejected_with_segment_location():
    cases = [
        ("front", [1.01, 0.5, 0.5, 0.5, 0.5]),    # 越界
        ("middle", [0.123, 0.5, 0.5, 0.5, 0.5]),  # 精度超限
        ("rear", [0.5, 0.5, 0.5, 0.5, True]),     # 布尔非数值
    ]
    for segment, bad in cases:
        response = post(five_point_body(**{segment: bad}))
        _assert_422_without_assessment(response)
        locs = [tuple(err["loc"]) for err in response.json()["detail"]]
        assert any(segment in loc for loc in locs)


def test_five_point_non_finite_is_rejected():
    raw = (
        '{"runway_id":"18L","sampling":"five_point",'
        '"front":[0.5,0.5,0.5,0.5,NaN],'
        '"middle":[0.5,0.5,0.5,0.5,0.5],'
        '"rear":[0.5,0.5,0.5,0.5,0.5]}'
    )
    response = client.post(
        "/api/v1/friction/assess",
        content=raw,
        headers={"content-type": "application/json"},
    )
    _assert_422_without_assessment(response)
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert any("front" in loc for loc in locs)


def test_unknown_sampling_mode_is_rejected():
    response = post(valid_body(sampling="seven_point"))
    _assert_422_without_assessment(response)
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert any("sampling" in loc for loc in locs)
