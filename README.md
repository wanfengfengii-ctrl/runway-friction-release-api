# 雨后跑道摩阻评估 API

纯后端 JSON API：维护人员一次提交跑道编号与前、中、后三段各 3 个摩阻系数，
服务取每段中位数分段判级，并按三段中的最差等级给出整跑道结论。

- **运行时**：Python 3.12 · FastAPI · Pydantic v2
- **测试**：pytest（41 项，覆盖输入校验、分段判级、结果组装与 422 整份拒绝）
- **部署**：Docker Compose（默认仅运行 API；宿主端口可用 `API_PORT` 覆盖）

## 判级规则

对每一段的 3 个测量值取中位数：

| 中位数区间 | 段等级 |
| --- | --- |
| ≥ 0.40 | 正常 |
| 0.30 ≤ 值 < 0.40 | 关注 |
| < 0.30 | 关闭 |

注意临界值归属：**0.40 属于正常，0.30 属于关注**。
整跑道等级取三段中最差者（正常 < 关注 < 关闭），响应中 `worst_segments`
明确指出是哪一段（或哪几段并列）接管了结论。

## 输入约束（任一不满足均返回 422，不返回部分判定）

- 必须恰好包含 `front`（前）、`middle`（中）、`rear`（后）三段，不得缺少、重复或多出；
- 每段必须恰好 3 个数字；
- 系数仅允许 `0.00` 至 `1.00`，至多保留两位小数；
- 非有限数（`NaN` / `Infinity` / `-Infinity`）、字符串、布尔值均拒绝；
- `runway_id` 为非空字符串（纯空白也拒绝）。

## 接口

### `POST /api/v1/friction/assess`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `runway_id` | string | 跑道编号，1–16 个字符，非空白 |
| `front` | number[3] | 前段 3 个摩阻系数 |
| `middle` | number[3] | 中段 3 个摩阻系数 |
| `rear` | number[3] | 后段 3 个摩阻系数 |

响应 `200`：按前、中、后顺序给出每段的原始值 `raw_values`、中位数 `median`、
段等级 `rating`，以及唯一的整跑道等级 `overall_rating` 和最差段 `worst_segments`。

#### 请求示例

```bash
curl -X POST http://localhost:8000/api/v1/friction/assess \
  -H 'Content-Type: application/json' \
  -d '{
    "runway_id": "18L",
    "front":  [0.39, 0.40, 0.50],
    "middle": [0.20, 0.30, 0.80],
    "rear":   [0.10, 0.29, 0.90]
  }'
```

上例展示临界值落级与最差段接管：前段中位数 0.40→正常，中段 0.30→关注，
后段 0.29→关闭；整跑道被后段接管为“关闭”。

#### 成功响应示例

```json
{
  "runway_id": "18L",
  "segments": [
    {
      "segment": "front",
      "label": "前段",
      "raw_values": [0.39, 0.40, 0.50],
      "median": 0.40,
      "rating": "正常"
    },
    {
      "segment": "middle",
      "label": "中段",
      "raw_values": [0.20, 0.30, 0.80],
      "median": 0.30,
      "rating": "关注"
    },
    {
      "segment": "rear",
      "label": "后段",
      "raw_values": [0.10, 0.29, 0.90],
      "median": 0.29,
      "rating": "关闭"
    }
  ],
  "overall_rating": "关闭",
  "worst_segments": ["rear"]
}
```

#### 422 拒绝示例

```bash
curl -i -X POST http://localhost:8000/api/v1/friction/assess \
  -H 'Content-Type: application/json' \
  -d '{"runway_id":"18L","front":[0.123,0.31,0.60],"middle":[0.40,0.45,0.90],"rear":[0.70,0.80,0.29]}'
```

```
HTTP/2 422
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["front"],
      "msg": "Value error, 摩阻系数至多保留两位小数",
      "input": [0.123, 0.31, 0.6],
      ...
    }
  ]
}
```

缺段、重复段名（JSON 中重复键）、数量不符、越界、非有限数等情形同以 422 整份拒绝。

### `GET /health`

返回 `{"status": "ok"}`，供容器健康检查与探活使用。

交互式文档（FastAPI 自动生成）：`http://localhost:8000/docs`。

## 本地开发（Python 3.12）

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
pytest
```

## Docker Compose

默认配置**只运行 API** 一个常驻服务（`verify` 位于 profile 中，不会随默认启动运行）：

```bash
docker compose up --build -d          # 宿主端口默认 8000
API_PORT=18000 docker compose up -d   # 用 API_PORT 覆盖宿主端口
curl http://localhost:${API_PORT:-8000}/health
docker compose down
```

### 一次性验收服务 `verify`

仓库内置零第三方依赖的验收脚本 `scripts/acceptance.py`，会对运行中的 API
执行健康检查、合法判级、临界值归属、最差段接管及完整 422 拒绝矩阵，
输出逐项 PASS/FAIL 并以退出码表示结论。在 Compose 中它是**一次性**服务
（`docker compose run --rm`，跑完即退出并自动删除容器）：

```bash
docker compose --profile verify run --rm verify
```

该服务通过 `depends_on: service_healthy` 等 API 健康后再执行，
内部访问地址固定为 `http://api:8000`。对宿主机上已运行的实例也可直接执行：

```bash
BASE_URL=http://127.0.0.1:8000 python3 scripts/acceptance.py
```

## 目录结构

```
app/
  __init__.py
  friction.py    # 领域规则：中位数、阈值判级、最差等级（纯函数）
  schemas.py     # Pydantic 请求/响应模型与输入校验
  service.py     # 结果组装：段顺序、整体等级、最差段
  main.py        # FastAPI 应用、严格 JSON 解析（含重复键检测）、422
scripts/
  acceptance.py  # 一次性端到端验收脚本（仅标准库）
tests/
  test_friction.py
  test_service.py
  test_api.py
Dockerfile
docker-compose.yml
requirements.txt
```
