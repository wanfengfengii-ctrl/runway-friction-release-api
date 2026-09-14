"""FastAPI 入口：雨后跑道摩阻评估 JSON API。"""

from __future__ import annotations

import json
import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.schemas import FrictionAssessment, FrictionInput
from app.service import assess

DESCRIPTION = """雨后跑道摩阻评估 API。

- 一次提交包含跑道编号及前、中、后三段，默认每段 3 个、至多两位小数的摩阻系数（0.00–1.00）。
- 可选 `"sampling": "five_point"` 五点稳健采样：每段提交 5 个系数，排序后各剔除
  一个最低值与最高值，取剩余三值的中位数，并随段结果返回剔除值与参与判定的三值。
- 服务取每段中位数判级：≥0.40 正常，0.30–<0.40 关注，<0.30 关闭。
- 整跑道采用三段中的最差等级。
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


def _parse_strict_json(raw: bytes) -> Any:
    """解析 JSON 并拒绝重复键；任何解析问题均转为 422。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestValidationError(
            [{"loc": ["body"], "msg": "请求体不是合法的 UTF-8 文本", "type": "value_error.encoding"}],
            body=raw,
        ) from exc
    try:
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
    """将错误详情中的 NaN / Infinity 输入转为字符串，保证响应体是合法严格 JSON。"""

    def clean(node: Any) -> Any:
        if isinstance(node, float) and not math.isfinite(node):
            return "NaN" if math.isnan(node) else ("Infinity" if node > 0 else "-Infinity")
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
