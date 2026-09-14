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
        runway_id = value.strip()
        if not runway_id:
            raise ValueError("跑道编号不得为空白")
        return runway_id

    @field_validator("front", "middle", "rear", mode="before")
    @classmethod
    def _validate_segment_values(cls, value: Any, info: Any) -> list[float]:
        # before 模式：在 Pydantic 强转（如 True -> 1.0）之前拿到原始输入。
        # sampling 声明在段字段之前，此处已可读到其校验结果；若 sampling 本身
        # 非法（不在 info.data 中）则回退默认方式，此时 sampling 的报错已足以
        # 让整份请求以 422 拒绝。
        mode = info.data.get("sampling", SamplingMode.THREE_POINT)
        expected = VALUES_PER_SEGMENT[mode]
        if not isinstance(value, list):
            raise ValueError(f"每段必须是包含 {expected} 个测量值的数组")
        if len(value) != expected:
            raise ValueError(f"每段必须恰好包含 {expected} 个测量值")

        checked: list[float] = []
        for item in value:
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
            checked.append(coefficient)
        return checked

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
