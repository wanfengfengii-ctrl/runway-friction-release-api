# 雨后跑道摩阻评估 API

纯后端 JSON API：维护人员一次提交跑道编号与前、中、后三段各 3 个摩阻系数，
服务取每段中位数分段判级，并按三段中的最差等级给出整跑道结论。
雨后复测受局部积水或仪器瞬时跳变影响时，可改用**五点稳健采样**（每段 5 个系数，
剔除一个最低值与一个最高值后取剩余三值的中位数），从同一入口取得整跑道结论。
连续作业前发现同一测量仪存在**系统性漂移**时，可改走**校准评估入口**：随三段
原始读数一并提交 4–12 个校准锚点（仪器读数 + 真值），服务按读数升序执行等权
相邻违序合并，每个段样本以精确对应锚点最终所属块的拟合值校准后进入同一套判级。

- **运行时**：Python 3.12 · FastAPI · Pydantic v2
- **测试**：pytest（93 项，覆盖输入校验、分段判级、五点稳健采样、等权相邻违序合并、
  校准评估、结果组装与 422 整份拒绝）
- **部署**：Docker Compose（默认仅运行 API；宿主端口可用 `API_PORT` 覆盖）

## 判级规则

对每一段的测量值取中位数（五点模式先剔除极值，见下节）：

| 中位数区间 | 段等级 |
| --- | --- |
| ≥ 0.40 | 正常 |
| 0.30 ≤ 值 < 0.40 | 关注 |
| < 0.30 | 关闭 |

注意临界值归属：**0.40 属于正常，0.30 属于关注**。
整跑道等级取三段中最差者（正常 < 关注 < 关闭），响应中 `worst_segments`
明确指出是哪一段（或哪几段并列）接管了结论。

## 五点稳健采样（可选）

在请求中加入 `"sampling": "five_point"` 即可启用，三段须统一按五点提交
（任一段不是 5 个值即整份 422 拒绝）。每段处理规则：

1. 按数值排序；
2. 剔除一个最低值与一个最高值（多个相同极值只按位置各剔除一个）；
3. 取剩余三值的中位数，复用同一套阈值判级、最差段选择与段顺序。

五点模式下每个段结果额外返回 `excluded_values`（被剔除的最低/最高值）与
`used_values`（实际参与判定的三值），`raw_values` 仍回显原始五值；
未传 `sampling` 的三值请求响应结构保持不变（不含这两个字段）。

## 仪器漂移校准评估（`/api/v1/friction/calibrated-assess`）

连续作业前发现同一测量仪存在系统性漂移时，维护人员可随三段原始读数一并提交
4–12 个校准锚点。每个锚点由**仪器读数** `reading` 与**真值** `true_value`
组成（均为 0.00–1.00、至多两位小数；读数在同一请求内必须唯一）。校准算法为
**等权相邻违序合并**（Pool Adjacent Violators Algorithm）：

1. 锚点按仪器读数升序排列，每个锚点起初自成一块；
2. 凡相邻两块出现下降（前块拟合值严格大于后块拟合值）即合并为一块，
   下降块须反复向前合并，直至所有块单调不降；
3. 块拟合值取全部成员真值的算术均值（等权），合并不丢失任何块成员。

三段样本的个数规则与原入口一致（三点 3 个、五点 5 个，`sampling` 语义相同），
且**每个段样本必须精确等于某一锚点的仪器读数**；校准时以该锚点最终所属块的
拟合值替换原读数，随后进入与原入口相同的三点中位数 / 五点去极值、阈值判级与
最差段组装。块拟合值恰为 0.30 或 0.40 时沿用现有边界等级（0.30→关注、0.40→正常）。

响应在原评估结构基础上扩展：

- `anchors`：逐锚点的校准结果（按读数升序），含 `reading`、`true_value`、
  最终块编号 `block`（从 0 起编）与所属块拟合值 `fitted_value`；
- `segments`：每段并列原始读数 `raw_values` 与校正值 `calibrated_values`，
  以及基于校正值的 `median`、`rating`（五点模式另含 `excluded_values` /
  `used_values`，同样基于校正值）；
- `overall_rating` 与 `worst_segments` 语义不变。

锚点数量（4–12）、有限数、0.00–1.00 范围、两位精度、读数唯一性及段样本引用
错误均以 422 整份拒绝，错误位置 `loc` 指向对应请求字段（`anchors` 或相应段名）。

### 校准评估请求示例（级联合并）

```bash
curl -X POST http://localhost:8000/api/v1/friction/calibrated-assess \
  -H 'Content-Type: application/json' \
  -d '{
    "runway_id": "18L",
    "anchors": [
      {"reading": 0.10, "true_value": 0.50},
      {"reading": 0.20, "true_value": 0.40},
      {"reading": 0.30, "true_value": 0.20},
      {"reading": 0.40, "true_value": 0.10},
      {"reading": 0.50, "true_value": 0.45},
      {"reading": 0.60, "true_value": 0.35}
    ],
    "front":  [0.10, 0.50, 0.20],
    "middle": [0.50, 0.60, 0.50],
    "rear":   [0.40, 0.40, 0.40]
  }'
```

真值沿读数升序为 0.50 / 0.40 / 0.20 / 0.10，每个新块都触发向前合并，最终四个
锚点同属块 0（拟合值 1.20/4，恰为 0.30，块成员不丢失）；0.45 / 0.35 并成块 1
（拟合值恰为 0.40）。响应：

```json
{
  "runway_id": "18L",
  "anchors": [
    {"reading": 0.10, "true_value": 0.50, "block": 0, "fitted_value": 0.30},
    {"reading": 0.20, "true_value": 0.40, "block": 0, "fitted_value": 0.30},
    {"reading": 0.30, "true_value": 0.20, "block": 0, "fitted_value": 0.30},
    {"reading": 0.40, "true_value": 0.10, "block": 0, "fitted_value": 0.30},
    {"reading": 0.50, "true_value": 0.45, "block": 1, "fitted_value": 0.40},
    {"reading": 0.60, "true_value": 0.35, "block": 1, "fitted_value": 0.40}
  ],
  "segments": [
    {
      "segment": "front",
      "label": "前段",
      "raw_values": [0.10, 0.50, 0.20],
      "calibrated_values": [0.30, 0.40, 0.30],
      "median": 0.30,
      "rating": "关注"
    },
    {
      "segment": "middle",
      "label": "中段",
      "raw_values": [0.50, 0.60, 0.50],
      "calibrated_values": [0.40, 0.40, 0.40],
      "median": 0.40,
      "rating": "正常"
    },
    {
      "segment": "rear",
      "label": "后段",
      "raw_values": [0.40, 0.40, 0.40],
      "calibrated_values": [0.30, 0.30, 0.30],
      "median": 0.30,
      "rating": "关注"
    }
  ],
  "overall_rating": "关注",
  "worst_segments": ["front", "rear"]
}
```

## 输入约束（任一不满足均返回 422，不返回部分判定）

- 必须恰好包含 `front`（前）、`middle`（中）、`rear`（后）三段，不得缺少、重复或多出；
- 每段必须恰好 3 个数字；选择 `five_point` 时每段必须恰好 5 个数字；
- 系数仅允许 `0.00` 至 `1.00`，至多保留两位小数；
- 非有限数（`NaN` / `Infinity` / `-Infinity`）、字符串、布尔值均拒绝；
  超出浮点可表示范围的超大整数同样以 422 整份拒绝（按越界处理）；
- `sampling` 仅允许 `three_point`（默认，可省略）或 `five_point`；
- `runway_id` 为非空字符串（纯空白也拒绝）。

## 接口

### `POST /api/v1/friction/assess`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `runway_id` | string | 跑道编号，1–16 个字符，非空白 |
| `sampling` | string | 可选；`three_point`（默认）或 `five_point`（五点稳健采样） |
| `front` | number[] | 前段摩阻系数，默认 3 个、五点模式 5 个 |
| `middle` | number[] | 中段摩阻系数，默认 3 个、五点模式 5 个 |
| `rear` | number[] | 后段摩阻系数，默认 3 个、五点模式 5 个 |

响应 `200`：按前、中、后顺序给出每段的原始值 `raw_values`、中位数 `median`、
段等级 `rating`，以及唯一的整跑道等级 `overall_rating` 和最差段 `worst_segments`。
五点模式下每段另含 `excluded_values` 与 `used_values`。

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

#### 五点稳健采样示例

```bash
curl -X POST http://localhost:8000/api/v1/friction/assess \
  -H 'Content-Type: application/json' \
  -d '{
    "runway_id": "18L",
    "sampling": "five_point",
    "front":  [0.42, 0.43, 0.44, 0.45, 0.99],
    "middle": [0.10, 0.20, 0.30, 0.80, 0.90],
    "rear":   [0.60, 0.65, 0.70, 0.75, 0.80]
  }'
```

前段的单个异常高值 0.99 被剔除，不影响判级；响应中每段附带剔除值与参与判定的三值：

```json
{
  "runway_id": "18L",
  "segments": [
    {
      "segment": "front",
      "label": "前段",
      "raw_values": [0.42, 0.43, 0.44, 0.45, 0.99],
      "median": 0.44,
      "rating": "正常",
      "excluded_values": [0.42, 0.99],
      "used_values": [0.43, 0.44, 0.45]
    },
    {
      "segment": "middle",
      "label": "中段",
      "raw_values": [0.10, 0.20, 0.30, 0.80, 0.90],
      "median": 0.30,
      "rating": "关注",
      "excluded_values": [0.10, 0.90],
      "used_values": [0.20, 0.30, 0.80]
    },
    {
      "segment": "rear",
      "label": "后段",
      "raw_values": [0.60, 0.65, 0.70, 0.75, 0.80],
      "median": 0.70,
      "rating": "正常",
      "excluded_values": [0.60, 0.80],
      "used_values": [0.65, 0.70, 0.75]
    }
  ],
  "overall_rating": "关注",
  "worst_segments": ["middle"]
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

缺段、重复段名（JSON 中重复键）、数量不符（含五点模式下非 5 值的段）、越界、
非有限数等情形同以 422 整份拒绝，错误位置 `loc` 指向对应段。

### `POST /api/v1/friction/calibrated-assess`

仪器漂移校准评估入口，详见上文「仪器漂移校准评估」一节。请求体在原入口字段
（`runway_id`、可选 `sampling`、`front` / `middle` / `rear` 三段原始读数）
之上增加 `anchors`（4–12 个 `{reading, true_value}` 校准锚点，读数唯一）；
响应逐锚点返回最终块归属与拟合值，逐段并列原始读数与校正值，判级规则与
最差段组装同原入口。原评估入口的三点、五点结构与结果保持不变。

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
执行健康检查、合法判级、临界值归属、最差段接管、五点稳健采样（抗单个异常
高/低值、相同极值只剔一个、旧请求结构不变）、仪器漂移校准（级联合并、块成员
不丢失、拟合值边界落级、逐段原值与校正值）及完整 422 拒绝矩阵，
输出逐项 PASS/FAIL 并以退出码表示结论。在 Compose 中它是**一次性**服务
（`docker compose run --rm`，跑完即退出并自动删除容器）：

```bash
# 全新环境一条命令完成：构建镜像 → 启动 API 并等其健康 → 运行验收 → 退出清理
docker compose --profile verify run --build --rm verify
```

镜像只由 `api` 服务构建（`verify` 仅引用同一镜像并设 `pull_policy: never`），
避免两个服务并行构建时争抢同名镜像标签；`verify` 通过
`depends_on: service_healthy` 等 API 健康后才执行，容器内访问地址固定为
`http://api:8000`。对宿主机上已运行的实例也可直接执行：

```bash
BASE_URL=http://127.0.0.1:8000 python3 scripts/acceptance.py
```

## 目录结构

```
app/
  __init__.py
  friction.py    # 领域规则：三点/五点中位数、阈值判级、最差等级、等权相邻违序合并（纯函数）
  schemas.py     # Pydantic 请求/响应模型与输入校验（含校准锚点与样本引用校验）
  service.py     # 结果组装：段顺序、整体等级、最差段、校准评估编排
  main.py        # FastAPI 应用、严格 JSON 解析（含重复键检测）、422
scripts/
  acceptance.py  # 一次性端到端验收脚本（仅标准库）
tests/
  test_friction.py
  test_service.py
  test_api.py
  test_calibrated.py
Dockerfile
docker-compose.yml
requirements.txt
```
