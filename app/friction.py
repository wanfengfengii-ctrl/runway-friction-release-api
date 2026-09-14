"""摩阻测量的领域规则与判级逻辑（纯函数，不依赖 Web 层）。"""

from __future__ import annotations

import statistics
from enum import Enum


class Rating(str, Enum):
    """段 / 整跑道摩阻等级。"""

    NORMAL = "正常"
    WATCH = "关注"
    CLOSED = "关闭"


# 等级严重度，数字越大越差
_RANK: dict[Rating, int] = {
    Rating.NORMAL: 0,
    Rating.WATCH: 1,
    Rating.CLOSED: 2,
}

# 临界阈值（均含左、不含右的边界在 grade_median 中体现）
WATCH_THRESHOLD = 0.30
NORMAL_THRESHOLD = 0.40

SEGMENT_ORDER = ("front", "middle", "rear")
SEGMENT_LABELS = {
    "front": "前段",
    "middle": "中段",
    "rear": "后段",
}


def grade_median(median: float) -> Rating:
    """按中位数判级。

    - 中位数 >= 0.40：正常
    - 0.30 <= 中位数 < 0.40：关注
    - 中位数 < 0.30：关闭
    """
    if median >= NORMAL_THRESHOLD:
        return Rating.NORMAL
    if median >= WATCH_THRESHOLD:
        return Rating.WATCH
    return Rating.CLOSED


def median_of_three(values: list[float]) -> float:
    """取三个测量值的中位数（结果必为其中某个原始值）。"""
    return statistics.median(values)


def robust_median_of_five(values: list[float]) -> tuple[float, list[float], list[float]]:
    """五点稳健采样：排序后各剔除一个最低值与最高值，取剩余三值的中位数。

    返回 ``(中位数, 剔除值, 实际参与判定的三值)``。多个相同极值只按位置各剔除
    一个，因此剔除值恒为排序后的首、尾元素，参与判定的三值即排序后的第 2–4 个。
    前置条件：``values`` 恰好 5 个元素（由请求模型保证）。
    """
    ordered = sorted(values)
    excluded = [ordered[0], ordered[-1]]
    used = ordered[1:-1]
    return statistics.median(used), excluded, used


def worst_rating(ratings: list[Rating]) -> Rating:
    """若干段等级中最差的一级：正常 < 关注 < 关闭。"""
    return max(ratings, key=lambda r: _RANK[r])
