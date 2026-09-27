# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4980**（容器内 4710） |
| Postgres | **6380**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4980

演示账号（对应下方角色矩阵）：

- `admin` / `123456`（**主管**：超级用户，含删除权）
- `worker` / `123456`（**值守工**：可建可改，不可删）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6380，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6380
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 角色矩阵

| 操作 | 值守工（worker） | 主管（admin / staff） |
|------|:---:|:---:|
| 登录、查看看板 / 抽屉 / 来脂批 | ✅ | ✅ |
| 来脂批：登记 / 修改 | ✅ | ✅ |
| 灶台：相位切换 | ✅ | ✅ |
| 值守：开灶 / 收灶 | ✅ | ✅ |
| 探针：登记 | ✅ | ✅ |
| 删除：来脂批 / 灶台 / 值守 / 探针 | ❌ 一律中文挡下 | ✅ 受删除守卫约束 |

- **主管** = 超级用户或 `is_staff`；其余登录用户皆为**值守工**。
- 值守工点任何删除入口（卡片「删」、抽屉内值守 / 探针 / 灶台删除）都会被后端用中文提示挡下；前端只藏按钮不算数，判定全部在后端。
- 登录鉴权不变：所有页面与删除入口都要求先登录。

## 删除守卫（四类删除同一套实现）

四类删除（来脂批 / 灶台 / 值守 / 探针）**不各写各的**：URL 只有一个
`POST /delete/<kind>/<pk>/`，视图只有一个 `delete_entity_view`，判定全部收敛在
`apps/kiln/services/deletion.py` 的 `delete_entity`：**先验角色 → 再跑模型守卫 → 最后删除**。
前端按钮的置灰原因与后端拒绝文案出自同一组 `*_block_reason` 函数，前后端同一套。

| 对象 | 守卫规则 |
|------|----------|
| 来脂批 | 仍挂**未收灶值守** → 拒绝（提示须先收灶清空）；无未收灶值守时可删，已收灶的值守与探针随批一并清除 |
| 灶台 | 有**未收灶值守** → **不可删**（抽屉按钮同步置灰并给出同一中文原因）；收灶后可删，其值守与探针级联清除 |
| 值守 | 可删，名下探针级联清除 |
| 探针 | 可删 |

删除成功后局部刷新：来脂批流的「本流共 N 批」与卡片行数同源重渲染、始终对齐；
看板图例计数与灶台瓦片随 `floor-refresh` 一并更新。

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶 / 删除）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD；每张卡片带删除入口（仅主管可用）

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。

- `脂-松脂坳-2409A`、`脂-桐油坑-2409B`：挂**未收灶值守** —— 演示主管删除被拒
- `脂-松脂坳-2409C`：**无值守** —— 演示主管可直接删除

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、deletion、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  templates/partials/ # 共享 toast 片段
  static/css/         # 值守台 ops-console 样式
```
