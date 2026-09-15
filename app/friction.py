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


def adjacent_violator_blocks(values: list[float]) -> tuple[list[float], list[int]]:
    """等权相邻违序合并（Pool Adjacent Violators Algorithm）。

    前置条件：``values`` 为按仪器读数升序排列的锚点真值，且均为至多两位小数
    （由请求模型保证）。每个样本起初自成一块；凡相邻两块出现下降（前块拟合值
    严格大于后块拟合值）即合并为一块，合并后若与前块仍构成下降则继续向前合并，
    直至所有块单调不降。块拟合值取全部成员真值的算术均值（等权）。

    返回 ``(各位置拟合值, 各位置最终块编号)``：同一块内所有成员的拟合值相同；
    块编号按读数升序从 0 起编，每个输入位置恰好归属一个块，块成员不丢失。

    内部以「分」（整数）做精确求和与比较，避免浮点噪声影响违序判定与临界均值
    （如块均值恰为 0.30 / 0.40 时仍精确落在既有判级边界上）。
    """
    cents = [int(round(value * 100)) for value in values]
    # 每个块记录成员下标与真值分总和；块拟合值 = 总和 / 成员数（等权算术均值）
    members: list[list[int]] = []
    sums: list[int] = []
    for index, cent in enumerate(cents):
        members.append([index])
        sums.append(cent)
        # 下降块反复向前合并：均值比较交叉相乘为整数运算，结果精确
        while (
            len(members) >= 2
            and sums[-2] * len(members[-1]) > sums[-1] * len(members[-2])
        ):
            members[-2] = members[-2] + members[-1]
            sums[-2] = sums[-2] + sums[-1]
            del members[-1]
            del sums[-1]

    fitted: list[float] = [0.0] * len(cents)
    block_ids: list[int] = [0] * len(cents)
    for block_id, (block_members, block_sum) in enumerate(zip(members, sums)):
        mean = block_sum / (len(block_members) * 100)  # 分 -> 原始单位
        for index in block_members:
            fitted[index] = mean
            block_ids[index] = block_id
    return fitted, block_ids
