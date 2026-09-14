"""评估结果组装：纯领域编排，不触碰 HTTP。"""

from __future__ import annotations

from app.friction import (
    SEGMENT_LABELS,
    SEGMENT_ORDER,
    grade_median,
    median_of_three,
    worst_rating,
)
from app.schemas import FrictionAssessment, FrictionInput, SegmentResult


def assess(data: FrictionInput) -> FrictionAssessment:
    """计算三段中位数、段等级，并由最差段决定整跑道等级。"""
    segment_results: list[SegmentResult] = []
    for name in SEGMENT_ORDER:
        raw = getattr(data, name)
        median = median_of_three(raw)
        segment_results.append(
            SegmentResult(
                segment=name,
                label=SEGMENT_LABELS[name],
                raw_values=list(raw),
                median=median,
                rating=grade_median(median),
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
