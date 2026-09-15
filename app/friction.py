"""摩阻测量的领域规则与判级逻辑（纯函数，不依赖 Web 层）。"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import Enum
from typing import NamedTuple


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


# ---------- 连续巡检剖面：平均摩阻最低的定长检查窗 ----------

# 剖面评估输出统一保留六位小数（四舍五入）
PROFILE_OUTPUT_QUANTUM = Decimal("0.000001")

# 窗口平均值判级沿用三点 / 五点同一套边界，Decimal 精确比较（判级用未舍入值）
_PROFILE_NORMAL_THRESHOLD = Decimal("0.40")
_PROFILE_WATCH_THRESHOLD = Decimal("0.30")

# 剖面计算的局部精度：远高于六位输出精度，避免中间除法（斜率、驻点）的舍入噪声
_PROFILE_PRECISION = 40


class LowestWindow(NamedTuple):
    """平均摩阻最低的定长检查窗（各字段均为未舍入的 Decimal）。"""

    start: Decimal
    end: Decimal
    start_value: Decimal
    end_value: Decimal
    area: Decimal
    average: Decimal


def grade_profile_average(average: Decimal) -> Rating:
    """按未舍入的窗口平均值判级：>= 0.40 正常，0.30–<0.40 关注，< 0.30 关闭。"""
    if average >= _PROFILE_NORMAL_THRESHOLD:
        return Rating.NORMAL
    if average >= _PROFILE_WATCH_THRESHOLD:
        return Rating.WATCH
    return Rating.CLOSED


def round_profile_output(value: Decimal) -> Decimal:
    """剖面评估输出数值统一保留六位小数（四舍五入）。"""
    return value.quantize(PROFILE_OUTPUT_QUANTUM, rounding=ROUND_HALF_UP)


def lowest_average_window(
    mileages: list[Decimal],
    coefficients: list[Decimal],
    window_length: Decimal,
) -> LowestWindow:
    """连续巡检剖面上平均摩阻最低的定长检查窗。

    前置条件（由请求模型保证）：里程严格递增、首点里程为 0、末点里程等于跑道
    长度、测点 6–50 个、窗长大于 0 且小于跑道长度。

    相邻测点间作线性插值，以分段梯形积分建立前缀面积 P。窗口平均
    A(s) = (P(s+w) − P(s)) / w 在起点域 [0, L−w] 上分段二次，分段点为测点里程
    x_i 及其减去窗长的位置 x_i − w；按这些位置切分起点域后，每个区间检查两端
    与满足窗口两端插值相等（f(s) == f(s+w)）的内部驻点，而非只枚举测点起点。
    全局以窗口平均值最小者为结果，并列时选择起点最小者。

    全程 Decimal 运算（局部精度 40 位），返回未舍入结果；判级与输出舍入由
    ``grade_profile_average`` / ``round_profile_output`` 分别完成。
    """
    with localcontext() as context:
        context.prec = _PROFILE_PRECISION
        xs = mileages
        ys = coefficients
        w = window_length
        count = len(xs)

        # 每段斜率与测点处的前缀梯形面积
        slopes = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(count - 1)]
        prefix = [Decimal(0)]
        for i in range(count - 1):
            prefix.append(prefix[-1] + (xs[i + 1] - xs[i]) * (ys[i] + ys[i + 1]) / 2)

        def segment_of(x: Decimal) -> int:
            # x 所在的插值段；测点上取右侧段（连续，取值不变），末端归入最后一段
            return min(max(bisect_right(xs, x) - 1, 0), count - 2)

        def value_at(x: Decimal) -> Decimal:
            i = segment_of(x)
            return ys[i] + slopes[i] * (x - xs[i])

        def area_to(x: Decimal) -> Decimal:
            i = segment_of(x)
            dx = x - xs[i]
            return prefix[i] + dx * ys[i] + slopes[i] * dx * dx / 2

        limit = xs[-1] - w  # 起点域右端
        # 切分点：测点里程 x_i 与其减去窗长的位置 x_i − w（落在起点域内的部分）
        cuts = {Decimal(0), limit}
        for x in xs:
            if 0 < x < limit:
                cuts.add(x)
            shifted = x - w
            if 0 < shifted < limit:
                cuts.add(shifted)
        ordered = sorted(cuts)

        candidates = list(ordered)
        for a, b in zip(ordered, ordered[1:]):
            # 开区间 (a, b) 内窗口两端各自落在固定的插值段上，取中点定位段号
            mid = (a + b) / 2
            i = segment_of(mid)
            j = segment_of(mid + w)
            left_slope = slopes[i]
            right_slope = slopes[j]
            if left_slope == right_slope:
                # 两端插值之差恒定：区间上平均值单调或不变，最值必在端点
                continue
            # 内部驻点：f(s) == f(s+w) 的唯一解
            stationary = (
                ys[j] - ys[i] + right_slope * (w - xs[j]) + left_slope * xs[i]
            ) / (left_slope - right_slope)
            if a < stationary < b:
                candidates.append(stationary)

        best_start = Decimal(0)
        best_area = Decimal(0)
        best_average: Decimal | None = None
        # 起点升序扫描，仅在严格更小时替换：并列时保留起点最小者
        for start in sorted(candidates):
            area = area_to(start + w) - area_to(start)
            average = area / w
            if best_average is None or average < best_average:
                best_start = start
                best_area = area
                best_average = average

        return LowestWindow(
            start=best_start,
            end=best_start + w,
            start_value=value_at(best_start),
            end_value=value_at(best_start + w),
            area=best_area,
            average=best_average,
        )
