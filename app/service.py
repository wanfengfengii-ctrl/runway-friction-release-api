"""评估结果组装：纯领域编排，不触碰 HTTP。"""

from __future__ import annotations

from decimal import Decimal

from app.friction import (
    SEGMENT_LABELS,
    SEGMENT_ORDER,
    adjacent_violator_blocks,
    grade_median,
    grade_profile_average,
    lowest_average_window,
    median_of_three,
    robust_median_of_five,
    round_profile_output,
    worst_rating,
)
from app.schemas import (
    CalibratedAnchorResult,
    CalibratedFrictionAssessment,
    CalibratedFrictionInput,
    CalibratedSegmentResult,
    FrictionAssessment,
    FrictionInput,
    ProfileFrictionAssessment,
    ProfileFrictionInput,
    SamplingMode,
    SegmentResult,
)


def _median_and_trim(
    raw: list[float], five_point: bool
) -> tuple[float, list[float] | None, list[float] | None]:
    """按采样方式求中位数；五点模式另返回剔除值与参与判定的三值。"""
    if five_point:
        # 五点稳健采样：各剔除一个最低值与最高值，取剩余三值的中位数
        median, excluded_values, used_values = robust_median_of_five(raw)
        return median, excluded_values, used_values
    return median_of_three(raw), None, None


def assess(data: FrictionInput) -> FrictionAssessment:
    """按所选采样方式计算各段中位数与段等级，并由最差段决定整跑道等级。"""
    five_point = data.sampling is SamplingMode.FIVE_POINT
    segment_results: list[SegmentResult] = []
    for name in SEGMENT_ORDER:
        raw = list(getattr(data, name))
        median, excluded_values, used_values = _median_and_trim(raw, five_point)
        segment_results.append(
            SegmentResult(
                segment=name,
                label=SEGMENT_LABELS[name],
                raw_values=raw,
                median=median,
                rating=grade_median(median),
                excluded_values=excluded_values,
                used_values=used_values,
            )
        )

    overall = worst_rating([segment.rating for segment in segment_results])
    # 达到整跑道等级的段，即“接管结论”的最差段
    worst_segments = [segment.segment for segment in segment_results if segment.rating == overall]

    return FrictionAssessment(
        runway_id=data.runway_id,
        segments=segment_results,
        overall_rating=overall,
        worst_segments=worst_segments,
    )


def assess_calibrated(data: CalibratedFrictionInput) -> CalibratedFrictionAssessment:
    """仪器漂移校准评估：锚点等权相邻违序合并后，以块拟合值校准各段样本再判级。

    锚点按仪器读数升序执行等权相邻违序合并（下降块反复向前合并，块拟合值为
    全部成员真值的算术均值，直至所有块单调不降）；每个段样本以其精确对应的
    锚点最终所属块的拟合值替换，随后走与未校准评估相同的三点中位数 / 五点
    去极值、阈值判级与最差段组装。
    """
    anchors = sorted(data.anchors, key=lambda anchor: anchor.reading)
    fitted, block_ids = adjacent_violator_blocks([anchor.true_value for anchor in anchors])
    anchor_results = [
        CalibratedAnchorResult(
            reading=anchor.reading,
            true_value=anchor.true_value,
            block=block_id,
            fitted_value=fitted_value,
        )
        for anchor, fitted_value, block_id in zip(anchors, fitted, block_ids)
    ]
    # 读数在请求内唯一（由请求模型保证），样本 -> 拟合值的映射无歧义
    fitted_by_reading = {
        anchor.reading: fitted_value for anchor, fitted_value in zip(anchors, fitted)
    }

    five_point = data.sampling is SamplingMode.FIVE_POINT
    segment_results: list[CalibratedSegmentResult] = []
    for name in SEGMENT_ORDER:
        raw = list(getattr(data, name))
        calibrated = [fitted_by_reading[sample] for sample in raw]
        median, excluded_values, used_values = _median_and_trim(calibrated, five_point)
        segment_results.append(
            CalibratedSegmentResult(
                segment=name,
                label=SEGMENT_LABELS[name],
                raw_values=raw,
                calibrated_values=calibrated,
                median=median,
                rating=grade_median(median),
                excluded_values=excluded_values,
                used_values=used_values,
            )
        )

    overall = worst_rating([segment.rating for segment in segment_results])
    worst_segments = [segment.segment for segment in segment_results if segment.rating == overall]

    return CalibratedFrictionAssessment(
        runway_id=data.runway_id,
        anchors=anchor_results,
        segments=segment_results,
        overall_rating=overall,
        worst_segments=worst_segments,
    )


def _profile_output(value: Decimal) -> float:
    """剖面评估输出：保留六位小数（四舍五入）后转为 JSON 数字。"""
    return float(round_profile_output(value))


def assess_profile(data: ProfileFrictionInput) -> ProfileFrictionAssessment:
    """连续巡检剖面评估：在线性插值剖面上找平均摩阻最低的定长检查窗。

    相邻测点线性插值、分段梯形积分建立前缀面积，按测点里程及其减去窗长的
    位置切分窗口起点域，逐区间检查两端与内部驻点（窗口两端插值相等处），
    全局取窗口平均值最小者，并列取起点最小者。判级使用未舍入的平均值，
    响应数值统一保留六位小数。
    """
    mileages = [point.mileage for point in data.points]
    coefficients = [point.coefficient for point in data.points]
    window = lowest_average_window(mileages, coefficients, data.window_length)

    return ProfileFrictionAssessment(
        runway_id=data.runway_id,
        start_mileage=_profile_output(window.start),
        end_mileage=_profile_output(window.end),
        start_friction=_profile_output(window.start_value),
        end_friction=_profile_output(window.end_value),
        area=_profile_output(window.area),
        average=_profile_output(window.average),
        rating=grade_profile_average(window.average),
    )
