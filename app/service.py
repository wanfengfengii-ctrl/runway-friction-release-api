"""评估结果组装：纯领域编排，不触碰 HTTP。"""

from __future__ import annotations

from app.friction import (
    SEGMENT_LABELS,
    SEGMENT_ORDER,
    grade_median,
    median_of_three,
    robust_median_of_five,
    worst_rating,
)
from app.schemas import (
    FrictionAssessment,
    FrictionInput,
    SamplingMode,
    SegmentResult,
)


def assess(data: FrictionInput) -> FrictionAssessment:
    """按所选采样方式计算各段中位数与段等级，并由最差段决定整跑道等级。"""
    five_point = data.sampling is SamplingMode.FIVE_POINT
    segment_results: list[SegmentResult] = []
    for name in SEGMENT_ORDER:
        raw = list(getattr(data, name))
        excluded_values: list[float] | None = None
        used_values: list[float] | None = None
        if five_point:
            # 五点稳健采样：各剔除一个最低值与最高值，取剩余三值的中位数
            median, excluded_values, used_values = robust_median_of_five(raw)
        else:
            median = median_of_three(raw)
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
