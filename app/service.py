"""评估结果组装：纯领域编排，不触碰 HTTP。"""

from __future__ import annotations

from app.friction import (
    SEGMENT_LABELS,
    SEGMENT_ORDER,
    adjacent_violator_blocks,
    grade_median,
    median_of_three,
    robust_median_of_five,
    worst_rating,
)
from app.schemas import (
    CalibratedAnchorResult,
    CalibratedFrictionAssessment,
    CalibratedFrictionInput,
    CalibratedSegmentResult,
    FrictionAssessment,
    FrictionInput,
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
