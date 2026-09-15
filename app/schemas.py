"""请求 / 响应 Pydantic 模型与输入校验。"""

from __future__ import annotations

import math
from decimal import Decimal
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
    try:
        coefficient = float(item)
    except OverflowError:
        # 超大整数超出浮点可表示范围，必然越界；须转为校验错误以 422 整份拒绝，
        # 否则 OverflowError（非 ValueError 子类）会穿透校验层变成 500
        raise ValueError(
            f"摩阻系数必须介于 {MIN_VALUE:.2f} 与 {MAX_VALUE:.2f} 之间"
        ) from None
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


# ---------- 连续巡检剖面评估 ----------

# 剖面测点数量约束
MIN_PROFILE_POINTS = 6
MAX_PROFILE_POINTS = 50


def _check_finite_decimal(item: Any, label: str) -> Decimal:
    """通用数值校验：数字、有限，统一转为 Decimal。

    剖面评估请求的 JSON 小数已按 Decimal 保真解析；直接构造模型时传入的
    int / float 也在此精确转换（float 经最短十进制表示还原）。
    """
    # bool 是 int 的子类，须在数值判断前排除
    if isinstance(item, bool) or not isinstance(item, (int, float, Decimal)):
        raise ValueError(f"{label}必须是数字")
    if isinstance(item, Decimal):
        value = item
    elif isinstance(item, int):
        value = Decimal(item)
    else:
        value = Decimal(str(item))
    if not value.is_finite():
        raise ValueError(f"{label}必须为有限数")
    return value


def _check_profile_coefficient(item: Any) -> Decimal:
    """测点摩阻系数校验：数字、有限、0.00–1.00、至多两位小数，返回 Decimal。"""
    coefficient = _check_finite_decimal(item, "摩阻系数")
    if not (Decimal("0.00") <= coefficient <= Decimal("1.00")):
        raise ValueError(
            f"摩阻系数必须介于 {MIN_VALUE:.2f} 与 {MAX_VALUE:.2f} 之间"
        )
    # 保留至多两位小数：舍到两位后应与原值相等（数值比较，0.100 视为 0.10）
    if coefficient != coefficient.quantize(Decimal("0.01")):
        raise ValueError("摩阻系数至多保留两位小数")
    return coefficient


class ProfilePoint(BaseModel):
    """一个里程—摩阻测点：里程自跑道起点起算，系数 0.00–1.00 至多两位小数。"""

    model_config = ConfigDict(extra="forbid")

    mileage: Decimal = Field(..., description="测点里程（≥ 0，与跑道长度同单位）")
    coefficient: Decimal = Field(..., description="摩阻系数（0.00–1.00，至多两位小数）")

    @field_validator("mileage", mode="before")
    @classmethod
    def _mileage_valid(cls, value: Any) -> Decimal:
        mileage = _check_finite_decimal(value, "测点里程")
        if mileage < 0:
            raise ValueError("测点里程不得为负")
        return mileage

    @field_validator("coefficient", mode="before")
    @classmethod
    def _coefficient_valid(cls, value: Any) -> Decimal:
        return _check_profile_coefficient(value)


class ProfileFrictionInput(BaseModel):
    """连续巡检剖面评估请求：跑道编号、跑道长度、固定检查窗长与里程—摩阻测点。

    测点 6–50 个，须按里程严格递增（不得重复或倒退），并恰好覆盖零点（首点
    里程为 0）与跑道终点（末点里程等于跑道长度）；窗长大于 0 且小于跑道长度。
    """

    model_config = ConfigDict(extra="forbid")

    runway_id: str = Field(..., min_length=1, max_length=16, description="跑道编号")
    runway_length: Decimal = Field(..., description="跑道长度（与测点里程同单位，大于 0）")
    window_length: Decimal = Field(..., description="固定检查窗长度（大于 0 且小于跑道长度）")
    points: list[ProfilePoint] = Field(
        ...,
        min_length=MIN_PROFILE_POINTS,
        max_length=MAX_PROFILE_POINTS,
        description=f"里程—摩阻测点（{MIN_PROFILE_POINTS}–{MAX_PROFILE_POINTS} 个，里程严格递增并覆盖跑道全程）",
    )

    @field_validator("runway_id")
    @classmethod
    def _runway_id_not_blank(cls, value: str) -> str:
        return _check_runway_id(value)

    @field_validator("runway_length", mode="before")
    @classmethod
    def _runway_length_valid(cls, value: Any) -> Decimal:
        length = _check_finite_decimal(value, "跑道长度")
        if length <= 0:
            raise ValueError("跑道长度必须大于 0")
        return length

    @field_validator("window_length", mode="before")
    @classmethod
    def _window_length_valid(cls, value: Any, info: Any) -> Decimal:
        window = _check_finite_decimal(value, "窗长")
        if window <= 0:
            raise ValueError("窗长必须大于 0")
        # runway_length 声明在前且已校验；若其本身非法（不在 info.data 中）则跳过
        # 交叉检查，跑道长度的报错已足以让整份请求以 422 拒绝
        runway_length = info.data.get("runway_length")
        if runway_length is not None and window >= runway_length:
            raise ValueError("窗长必须小于跑道长度")
        return window

    @field_validator("points")
    @classmethod
    def _points_valid(
        cls, points: list[ProfilePoint], info: Any
    ) -> list[ProfilePoint]:
        mileages = [point.mileage for point in points]
        for previous, current in zip(mileages, mileages[1:]):
            if current <= previous:
                raise ValueError("测点里程必须严格递增，存在重复或倒退里程")
        # runway_length 非法时跳过越界 / 覆盖检查（其报错已足以 422 整份拒绝）
        runway_length = info.data.get("runway_length")
        if runway_length is not None:
            out_of_range = sorted({m for m in mileages if m > runway_length})
            if out_of_range:
                raise ValueError(
                    "测点里程不得超出跑道长度，越界里程："
                    + ", ".join(str(m) for m in out_of_range)
                )
            if mileages[0] != 0:
                raise ValueError("首个测点里程必须为 0（测点须恰好覆盖零点）")
            if mileages[-1] != runway_length:
                raise ValueError("末个测点里程必须等于跑道长度（测点须恰好覆盖跑道终点）")
        return points


class ProfileFrictionAssessment(BaseModel):
    """连续巡检剖面评估结果：平均摩阻最低的定长检查窗（数值保留六位小数）。"""

    runway_id: str
    start_mileage: float = Field(..., description="窗口起点里程")
    end_mileage: float = Field(..., description="窗口止点里程")
    start_friction: float = Field(..., description="窗口起点插值摩阻")
    end_friction: float = Field(..., description="窗口止点插值摩阻")
    area: float = Field(..., description="窗口积分面积")
    average: float = Field(..., description="窗口平均摩阻（判级按未舍入值）")
    rating: Rating = Field(..., description="窗口平均值判级（沿用 0.40 / 0.30 边界）")
