"""请求 / 响应 Pydantic 模型与输入校验。"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.friction import SEGMENT_ORDER, Rating

# 段名（英文字段名）必须恰好齐全
REQUIRED_SEGMENTS = tuple(SEGMENT_ORDER)

MIN_VALUE = 0.00
MAX_VALUE = 1.00


class SamplingMode(str, Enum):
    """每段采样方式：三点取中位数，或五点稳健采样（剔极值后取中位数）。"""

    THREE_POINT = "three_point"
    FIVE_POINT = "five_point"


# 各采样方式下每段要求的测量值个数
VALUES_PER_SEGMENT = {
    SamplingMode.THREE_POINT: 3,
    SamplingMode.FIVE_POINT: 5,
}

# 校准锚点数量约束
MIN_ANCHORS = 4
MAX_ANCHORS = 12


def _check_coefficient(item: Any) -> float:
    """单个摩阻系数校验：数字、有限、0.00–1.00、至多两位小数。"""
    # bool 是 int 的子类，须在数值判断前排除
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ValueError("摩阻系数必须是数字")
    coefficient = float(item)
    if not math.isfinite(coefficient):
        raise ValueError("摩阻系数必须为有限数")
    if not (MIN_VALUE <= coefficient <= MAX_VALUE):
        raise ValueError(
            f"摩阻系数必须介于 {MIN_VALUE:.2f} 与 {MAX_VALUE:.2f} 之间"
        )
    # 保留至多两位小数：round 后应与原值相等
    if round(coefficient, 2) != coefficient:
        raise ValueError("摩阻系数至多保留两位小数")
    return coefficient


def _check_segment_values(value: Any, mode: SamplingMode) -> list[float]:
    """按采样方式校验一段测量值：必须是恰好 expected 个合法系数的数组。"""
    expected = VALUES_PER_SEGMENT[mode]
    if not isinstance(value, list):
        raise ValueError(f"每段必须是包含 {expected} 个测量值的数组")
    if len(value) != expected:
        raise ValueError(f"每段必须恰好包含 {expected} 个测量值")
    return [_check_coefficient(item) for item in value]


def _check_runway_id(value: str) -> str:
    runway_id = value.strip()
    if not runway_id:
        raise ValueError("跑道编号不得为空白")
    return runway_id


class FrictionInput(BaseModel):
    """一次提交：跑道编号、采样方式与前 / 中 / 后三段摩阻系数。

    未传 ``sampling`` 时每段 3 个系数（三点取中位数）；选择五点稳健采样时
    每段必须恰好 5 个系数，三段统一按所选方式校验。
    """

    model_config = ConfigDict(extra="forbid")

    runway_id: str = Field(..., min_length=1, max_length=16, description="跑道编号")
    sampling: SamplingMode = Field(
        default=SamplingMode.THREE_POINT,
        description="采样方式：three_point（默认，每段 3 值）或 five_point（五点稳健采样，每段 5 值）",
    )
    front: list[float] = Field(..., description="前段摩阻系数（个数由采样方式决定）")
    middle: list[float] = Field(..., description="中段摩阻系数（个数由采样方式决定）")
    rear: list[float] = Field(..., description="后段摩阻系数（个数由采样方式决定）")

    @field_validator("runway_id")
    @classmethod
    def _runway_id_not_blank(cls, value: str) -> str:
        return _check_runway_id(value)

    @field_validator("front", "middle", "rear", mode="before")
    @classmethod
    def _validate_segment_values(cls, value: Any, info: Any) -> list[float]:
        # before 模式：在 Pydantic 强转（如 True -> 1.0）之前拿到原始输入。
        # sampling 声明在段字段之前，此处已可读到其校验结果；若 sampling 本身
        # 非法（不在 info.data 中）则回退默认方式，此时 sampling 的报错已足以
        # 让整份请求以 422 拒绝。
        mode = info.data.get("sampling", SamplingMode.THREE_POINT)
        return _check_segment_values(value, mode)

    @model_validator(mode="after")
    def _segments_present(self) -> "FrictionInput":
        # 字段本身为必填，extra="forbid" 拒绝多余段名；此处仅做显式语义守卫
        missing = [name for name in REQUIRED_SEGMENTS if not getattr(self, name)]
        if missing:
            raise ValueError(f"缺少段：{', '.join(missing)}")
        return self


class SegmentResult(BaseModel):
    segment: str
    label: str
    raw_values: list[float]
    median: float
    rating: Rating
    # 仅五点稳健采样模式填充（响应中 None 字段被省略，三点结果保持原结构）
    excluded_values: list[float] | None = Field(
        default=None, description="五点模式剔除的一个最低值与一个最高值（升序）"
    )
    used_values: list[float] | None = Field(
        default=None, description="五点模式实际参与判定的三个值（升序）"
    )


class FrictionAssessment(BaseModel):
    runway_id: str
    segments: list[SegmentResult]
    overall_rating: Rating
    worst_segments: list[str] = Field(
        ..., description="达到整跑道最差等级的段名（前/中/后中的一个或多个）"
    )


# ---------- 仪器漂移校准评估 ----------


class CalibrationAnchor(BaseModel):
    """一个校准锚点：仪器读数与对应真值（标准器复现值）。"""

    model_config = ConfigDict(extra="forbid")

    reading: float = Field(
        ..., description="仪器读数（0.00–1.00，至多两位小数，同一请求内唯一）"
    )
    true_value: float = Field(
        ..., description="真值（0.00–1.00，至多两位小数）"
    )

    @field_validator("reading", "true_value", mode="before")
    @classmethod
    def _validate_coefficient(cls, value: Any) -> float:
        return _check_coefficient(value)


class CalibratedFrictionInput(BaseModel):
    """仪器漂移校准评估请求：跑道编号、校准锚点与前 / 中 / 后三段原始读数。

    锚点 4–12 个，读数在请求内唯一；三段样本个数由采样方式决定（三点 3 个、
    五点 5 个），且每个样本必须精确等于某一锚点的仪器读数，校准后以该锚点
    最终所属块的拟合值进入判级。
    """

    model_config = ConfigDict(extra="forbid")

    runway_id: str = Field(..., min_length=1, max_length=16, description="跑道编号")
    sampling: SamplingMode = Field(
        default=SamplingMode.THREE_POINT,
        description="采样方式：three_point（默认，每段 3 值）或 five_point（五点稳健采样，每段 5 值）",
    )
    anchors: list[CalibrationAnchor] = Field(
        ...,
        min_length=MIN_ANCHORS,
        max_length=MAX_ANCHORS,
        description=f"校准锚点（{MIN_ANCHORS}–{MAX_ANCHORS} 个，读数唯一）",
    )
    front: list[float] = Field(..., description="前段原始仪器读数（个数由采样方式决定）")
    middle: list[float] = Field(..., description="中段原始仪器读数（个数由采样方式决定）")
    rear: list[float] = Field(..., description="后段原始仪器读数（个数由采样方式决定）")

    @field_validator("runway_id")
    @classmethod
    def _runway_id_not_blank(cls, value: str) -> str:
        return _check_runway_id(value)

    @field_validator("front", "middle", "rear", mode="before")
    @classmethod
    def _validate_segment_values(cls, value: Any, info: Any) -> list[float]:
        # 与 FrictionInput 相同的 before 模式校验：个数由采样方式决定
        mode = info.data.get("sampling", SamplingMode.THREE_POINT)
        return _check_segment_values(value, mode)

    @field_validator("anchors")
    @classmethod
    def _readings_unique(cls, anchors: list[CalibrationAnchor]) -> list[CalibrationAnchor]:
        readings = [anchor.reading for anchor in anchors]
        duplicated = sorted({reading for reading in readings if readings.count(reading) > 1})
        if duplicated:
            raise ValueError(
                "锚点仪器读数必须唯一，存在重复读数："
                + ", ".join(f"{reading:.2f}" for reading in duplicated)
            )
        return anchors

    @field_validator("front", "middle", "rear")
    @classmethod
    def _samples_reference_anchors(cls, value: list[float], info: Any) -> list[float]:
        # anchors 声明在段字段之前，此处已可读到其校验结果；若 anchors 本身非法
        # （不在 info.data 中）则跳过引用检查，锚点的报错已足以让整份请求以 422 拒绝。
        anchors = info.data.get("anchors")
        if anchors is None:
            return value
        known = {anchor.reading for anchor in anchors}
        unmatched = sorted({sample for sample in value if sample not in known})
        if unmatched:
            raise ValueError(
                "段样本必须精确对应一个锚点的仪器读数，未匹配的样本："
                + ", ".join(f"{sample:.2f}" for sample in unmatched)
            )
        return value


class CalibratedAnchorResult(BaseModel):
    """单个锚点的校准结果：最终块归属与拟合值（按读数升序返回）。"""

    reading: float = Field(..., description="仪器读数")
    true_value: float = Field(..., description="真值")
    block: int = Field(..., description="最终块编号（按读数升序从 0 起编）")
    fitted_value: float = Field(..., description="所属块拟合值（块内全部真值的算术均值）")


class CalibratedSegmentResult(BaseModel):
    segment: str
    label: str
    raw_values: list[float] = Field(..., description="原始仪器读数")
    calibrated_values: list[float] = Field(
        ..., description="校正值：各样本对应锚点最终所属块的拟合值"
    )
    median: float
    rating: Rating
    # 仅五点稳健采样模式填充（基于校正值；响应中 None 字段被省略）
    excluded_values: list[float] | None = Field(
        default=None, description="五点模式剔除的一个最低值与一个最高值（升序，校正值）"
    )
    used_values: list[float] | None = Field(
        default=None, description="五点模式实际参与判定的三个值（升序，校正值）"
    )


class CalibratedFrictionAssessment(BaseModel):
    runway_id: str
    anchors: list[CalibratedAnchorResult] = Field(
        ..., description="逐锚点校准结果（按仪器读数升序）"
    )
    segments: list[CalibratedSegmentResult]
    overall_rating: Rating
    worst_segments: list[str] = Field(
        ..., description="达到整跑道最差等级的段名（前/中/后中的一个或多个）"
    )
