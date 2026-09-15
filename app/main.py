"""FastAPI 入口：雨后跑道摩阻评估 JSON API。"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.schemas import (
    CalibratedFrictionAssessment,
    CalibratedFrictionInput,
    FrictionAssessment,
    FrictionInput,
    ProfileFrictionAssessment,
    ProfileFrictionInput,
)
from app.service import assess, assess_calibrated, assess_profile

DESCRIPTION = """雨后跑道摩阻评估 API。

- 一次提交包含跑道编号及前、中、后三段，默认每段 3 个、至多两位小数的摩阻系数（0.00–1.00）。
- 可选 `"sampling": "five_point"` 五点稳健采样：每段提交 5 个系数，排序后各剔除
  一个最低值与最高值，取剩余三值的中位数，并随段结果返回剔除值与参与判定的三值。
- 服务取每段中位数判级：≥0.40 正常，0.30–<0.40 关注，<0.30 关闭。
- 整跑道采用三段中的最差等级。
- 仪器存在系统性漂移时，可改走 `/api/v1/friction/calibrated-assess`：随三段原始读数
  一并提交 4–12 个校准锚点（仪器读数 + 真值，读数唯一），服务按读数升序执行等权
  相邻违序合并（下降块反复向前合并，块拟合值取全部成员真值的算术均值，直至所有块
  单调不降），每个段样本以精确对应的锚点最终所属块的拟合值校准后进入同一套判级；
  响应逐锚点返回最终块归属与拟合值，逐段并列原始读数与校正值。
- 连续巡检车获得不等距测点后，可改走 `/api/v1/friction/profile-assess`：提交跑道
  编号、跑道长度、固定检查窗长与 6–50 个里程—摩阻测点（里程严格递增并恰好覆盖
  零点与跑道终点），服务在相邻测点间线性插值，以分段梯形积分建立前缀面积，在
  整条跑道上定位平均摩阻最低的定长连续窗口（逐区间检查端点与内部驻点，并列时
  取起点最小者），返回窗口起止里程、两端插值、积分面积与平均值；平均值按未舍入
  值沿用 0.40 / 0.30 边界判级，计算使用 Decimal，输出数值保留六位小数。
- 任何输入不合法均以 422 整份拒绝，不返回部分判定。
"""

app = FastAPI(
    title="雨后跑道摩阻评估 API",
    description=DESCRIPTION,
    version="1.0.0",
)


class _DuplicateKeyError(ValueError):
    """JSON 对象内出现重复键。"""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKeyError(key)
        obj[key] = value
    return obj


def _parse_strict_json(raw: bytes, *, parse_decimal: bool = False) -> Any:
    """解析 JSON 并拒绝重复键；任何解析问题均转为 422。

    parse_decimal=True 时 JSON 小数按 Decimal 保真解析（供剖面评估的精确
    十进制计算）；NaN / Infinity 等非有限常量仍按浮点解析，由模型校验拒绝。
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestValidationError(
            [{"loc": ["body"], "msg": "请求体不是合法的 UTF-8 文本", "type": "value_error.encoding"}],
            body=raw,
        ) from exc
    try:
        if parse_decimal:
            return json.loads(
                text,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_float=Decimal,
            )
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise RequestValidationError(
            [{"loc": ["body", exc.pos], "msg": f"JSON 解析失败：{exc.msg}", "type": "value_error.jsondecode"}],
            body=raw,
        ) from exc
    except _DuplicateKeyError as exc:
        key = exc.args[0]
        raise RequestValidationError(
            [{"loc": ["body", key], "msg": f"JSON 中存在重复键：{key}", "type": "value_error.duplicate_key"}],
            body=raw,
        ) from exc


def _sanitize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """清洗错误详情：非有限浮点与 Decimal 输入转为 JSON 可序列化形式，保证响应体合法。"""

    def clean(node: Any) -> Any:
        if isinstance(node, float) and not math.isfinite(node):
            return "NaN" if math.isnan(node) else ("Infinity" if node > 0 else "-Infinity")
        if isinstance(node, Decimal):
            # Decimal 不在 JSON 序列化范围内；有限值按 float 回显，超出浮点范围
            # 或非有限的值转为字符串，避免 422 响应本身变成非法 JSON
            as_float = float(node)
            if math.isfinite(as_float):
                return as_float
            return "NaN" if node.is_nan() else ("Infinity" if node > 0 else "-Infinity")
        if isinstance(node, dict):
            return {key: clean(value) for key, value in node.items()}
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return [clean(error) for error in errors]


@app.get("/health", tags=["系统"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/friction/assess",
    response_model=FrictionAssessment,
    response_model_exclude_none=True,
    tags=["摩阻评估"],
    summary="提交三段摩阻测量值并获取整跑道判级",
)
async def assess_friction(request: Request) -> FrictionAssessment:
    raw = await request.body()
    payload = _parse_strict_json(raw)
    try:
        data = FrictionInput.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(_sanitize_errors(exc.errors()), body=payload) from exc
    return assess(data)


@app.post(
    "/api/v1/friction/calibrated-assess",
    response_model=CalibratedFrictionAssessment,
    response_model_exclude_none=True,
    tags=["摩阻评估"],
    summary="提交校准锚点与三段原始读数，按等权相邻违序合并校准后判级",
)
async def assess_friction_calibrated(request: Request) -> CalibratedFrictionAssessment:
    raw = await request.body()
    payload = _parse_strict_json(raw)
    try:
        data = CalibratedFrictionInput.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(_sanitize_errors(exc.errors()), body=payload) from exc
    return assess_calibrated(data)


@app.post(
    "/api/v1/friction/profile-assess",
    response_model=ProfileFrictionAssessment,
    tags=["摩阻评估"],
    summary="提交连续巡检剖面，获取平均摩阻最低的定长检查窗",
)
async def assess_friction_profile(request: Request) -> ProfileFrictionAssessment:
    raw = await request.body()
    # 剖面计算全程 Decimal：JSON 小数按 Decimal 保真解析，不做二进制浮点近似
    payload = _parse_strict_json(raw, parse_decimal=True)
    try:
        data = ProfileFrictionInput.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(_sanitize_errors(exc.errors()), body=payload) from exc
    return assess_profile(data)
