"""领域规则单元测试：中位数、分段判级、最差段接管。"""

import math

import pytest

from app.friction import (
    Rating,
    grade_median,
    median_of_three,
    worst_rating,
)


@pytest.mark.parametrize(
    ("median", "expected"),
    [
        (0.40, Rating.NORMAL),   # 上临界：>= 0.40 正常（含边界）
        (0.41, Rating.NORMAL),
        (1.00, Rating.NORMAL),
        (0.3999, Rating.WATCH),
        (0.39, Rating.WATCH),
        (0.30, Rating.WATCH),    # 下临界：0.30 属于关注而非关闭
        (0.3000001, Rating.WATCH),
        (0.2999, Rating.CLOSED),
        (0.29, Rating.CLOSED),
        (0.00, Rating.CLOSED),
    ],
)
def test_grade_median_boundaries(median, expected):
    assert grade_median(median) is expected


def test_median_is_middle_value_regardless_of_order():
    assert median_of_three([0.52, 0.31, 0.60]) == 0.52
    assert median_of_three([0.31, 0.60, 0.52]) == 0.52
    assert median_of_three([0.40, 0.40, 0.40]) == 0.40


def test_median_of_three_is_always_one_of_inputs():
    values = [0.33, 0.47, 0.29]
    assert median_of_three(values) in values


def test_worst_rating_orders_and_ties():
    assert worst_rating([Rating.NORMAL, Rating.NORMAL, Rating.NORMAL]) is Rating.NORMAL
    assert worst_rating([Rating.NORMAL, Rating.WATCH, Rating.NORMAL]) is Rating.WATCH
    assert worst_rating([Rating.NORMAL, Rating.CLOSED, Rating.WATCH]) is Rating.CLOSED
    assert worst_rating([Rating.WATCH, Rating.WATCH, Rating.NORMAL]) is Rating.WATCH


def test_thresholds_are_exact_for_critical_values():
    # 浮点层面验证临界归属，避免实现中误用 <= / < 写反
    assert math.isclose(0.40, 0.40)
    assert grade_median(0.40) is not Rating.WATCH
    assert grade_median(0.30) is not Rating.CLOSED
