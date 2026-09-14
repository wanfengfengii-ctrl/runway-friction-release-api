"""结果组装测试：前中后顺序、原始值/中位数/等级、最差段接管。"""

import pytest

from app.friction import Rating
from app.schemas import FrictionInput
from app.service import assess


def _payload(front, middle, rear, runway_id="18L"):
    return FrictionInput(runway_id=runway_id, front=front, middle=middle, rear=rear)


def test_all_normal_segments_yield_normal():
    result = assess(_payload([0.50, 0.60, 0.55], [0.45, 0.40, 0.90], [0.70, 0.80, 0.60]))
    assert result.overall_rating is Rating.NORMAL
    assert [s.segment for s in result.segments] == ["front", "middle", "rear"]
    assert [s.label for s in result.segments] == ["前段", "中段", "后段"]
    assert result.segments[0].median == 0.55
    assert result.segments[1].median == 0.45
    assert result.segments[2].median == 0.70
    assert [s.rating for s in result.segments] == [Rating.NORMAL] * 3
    assert result.worst_segments == ["front", "middle", "rear"]


def test_single_closed_segment_takes_over_conclusion():
    # 前、后正常，中段关闭 -> 整跑道关闭，最差段仅中段
    result = assess(_payload([0.80, 0.70, 0.90], [0.20, 0.29, 0.10], [0.60, 0.50, 0.70]))
    assert [s.rating for s in result.segments] == [
        Rating.NORMAL,
        Rating.CLOSED,
        Rating.NORMAL,
    ]
    assert result.overall_rating is Rating.CLOSED
    assert result.worst_segments == ["middle"]


def test_watch_outranks_normal():
    result = assess(_payload([0.40, 0.41, 0.50], [0.30, 0.35, 0.39], [0.90, 1.00, 0.95]))
    assert result.overall_rating is Rating.WATCH
    assert result.worst_segments == ["middle"]


def test_multiple_worst_segments_are_all_listed():
    result = assess(_payload([0.10, 0.20, 0.25], [0.30, 0.31, 0.32], [0.05, 0.10, 0.20]))
    assert result.overall_rating is Rating.CLOSED
    assert result.worst_segments == ["front", "rear"]


def test_critical_medians_fall_on_correct_side():
    # 中位数恰好 0.40 -> 正常；恰好 0.30 -> 关注
    result = assess(_payload([0.39, 0.40, 0.50], [0.20, 0.30, 0.80], [0.10, 0.29, 0.90]))
    by_name = {s.segment: s for s in result.segments}
    assert by_name["front"].median == 0.40
    assert by_name["front"].rating is Rating.NORMAL
    assert by_name["middle"].median == 0.30
    assert by_name["middle"].rating is Rating.WATCH
    assert by_name["rear"].median == 0.29
    assert by_name["rear"].rating is Rating.CLOSED
    assert result.overall_rating is Rating.CLOSED


def test_raw_values_are_preserved_in_segment_order():
    raw = [0.62, 0.41, 0.58]
    result = assess(_payload(raw, [0.5, 0.6, 0.7], [0.8, 0.9, 1.0]))
    assert result.segments[0].raw_values == raw


def test_runway_id_is_echoed():
    assert assess(_payload([0.5] * 3, [0.5] * 3, [0.5] * 3, runway_id="36R")).runway_id == "36R"


def test_unsorted_inputs_still_grade_by_median():
    # 顺序打乱不影响中位数
    result = assess(_payload([0.10, 0.90, 0.40], [0.5] * 3, [0.5] * 3))
    assert result.segments[0].median == 0.40
    assert result.segments[0].rating is Rating.NORMAL
    assert result.overall_rating is Rating.NORMAL


def test_input_model_rejects_invalid_count():
    with pytest.raises(Exception):
        FrictionInput(runway_id="18", front=[0.5, 0.5], middle=[0.5] * 3, rear=[0.5] * 3)
