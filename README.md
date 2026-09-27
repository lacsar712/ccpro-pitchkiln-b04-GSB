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
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（主管 · 超级用户，可删除）
- `worker` / `123456`（值守工 · 可建可改，不可删除）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

## 角色矩阵

| 角色 | 来脂批 | 灶台 | 值守 | 探针 |
|------|--------|------|------|------|
| 值守工（`worker`） | 建 / 改 | 建 / 改 | 建 / 改（开灶、收灶） | 建 / 改 |
| 主管（`admin`） | 建 / 改 / **删** | 建 / 改 / **删** | 建 / 改 / **删** | 建 / 改 / **删** |

- 角色判定：超级用户或「主管」组成员 = 主管，其余登录用户 = 值守工；种子自动建组并分配（`admin`→主管，`worker`→值守工），侧条有角色徽标。
- **删除权只给主管**：值守工点任何删除入口，后端统一中文挡下——「删除权限仅为主管开放，值守工不可删除。」不是只藏按钮，四个删除端点都在后端强制校验。
- 所有页面保持登录鉴权（`login_required`）。

## 删除规则（四类同一套）

四类删除（来脂批 / 灶台 / 值守 / 探针）不各写各的，统一走
`apps/kiln/services/deletion.py`：`review_delete`（先角色、后业务守卫）→
`perform_delete`（唯一执行入口），四个删除视图只是它的薄封装。

- **来脂批**：仍挂未收灶值守 → 拒删（「仍挂 N 条未收灶值守，清空未收灶后才可删除」）；清空未收灶后才可删。删除时已收灶的值守连同探针随批一并清除。
- **灶台**：仍有未收灶值守 → **不可删**，须先收灶。删除后其下值守与探针级联清除。该规则前后端同一套：模板用 `delete_block_reason` 禁用按钮并显示理由，后端用同一函数拦截。
- **值守**：探针随值守级联删除；若删的是未收灶值守，灶台自动回冷灶。
- **探针**：单条删除。
- 删除成功后整页重载，来脂批流的「共 N 张」计数与卡片行数由同一查询一次渲染，始终对齐。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号与角色组存在。样例地名仅用「松脂坳 / 桐油坑」系。
来脂批中 `2409A` / `2409B` 挂有未收灶值守、`2409C` 无值守，便于对照演示删除规则。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、deletion(统一删除)、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
