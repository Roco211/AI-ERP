# Forge ERP · Operations v0.10

五金商贸 ERP。Bootstrap、Catalog、库存、采购和销售已验收发布。资金 v0.9 提供应收应付、收付款、退款及期初衔接；运营 v0.10 增加正式 Excel 导入、可解释补货建议和可追溯经营概览，保留现有单位换算、库存流水、状态及成本规则。可选本地语义搜索使用 Ollama/BGE-M3（[安装说明](docs/local-embeddings.md)）。AI 业务工具和完整会计仍在范围之外。

本次两个评审分支按资金→运营依赖顺序交付，不自动合并或发布。详见[资金交付](docs/funds-v0.9-delivery.md)、[运营规范](docs/operations-v0.10.md)、[运营验收](docs/operations-v0.10-acceptance.md)和[运营交付](docs/operations-v0.10-delivery.md)。

## 本地启动

需要 Docker Compose、uv、Node.js 24、pnpm 11.7.0。uv 自动获取 Python 3.14。

```sh
make env                 # 仅首次：生成本地随机凭据，不覆盖已有 .env
make install
make infra               # PostgreSQL 18 + vector + pg_trgm，Redis
make migrate
make seed                # DEMO / ADMIN；账户来自 .env 的 SEED_ADMIN_*
make seed-catalog        # 可选：五金示例商品及关联资料
make seed-inventory      # 可选：独立 INV-DEMO 商品/仓库，通过期初单入账
make seed-sales          # 可选：独立销售演示订单/出库/退货，重复保留既有事实
make api                 # 终端 1
make web                 # 终端 2
make worker              # 终端 3，执行已确认导入及后台事件
make beat                # 终端 4，定时恢复导入、清理到期正文及处理 Outbox
```

打开 http://localhost:3100，输入 DEMO 和 `.env` 中配置的邮箱、密码。不要公开 `.env`。重复 seed 保留已有密码；修改环境变量不会重置现有账户。

端口：Web 3100、API 8100、PostgreSQL 55438、Redis 56379。与已有 8000/55432 服务隔离。浏览器始终访问 `/api/v1`；后端地址只在 Next 服务端 rewrite 中使用。更改端口时同时调整 `.env`、Next 与 Playwright 配置。

## 验证

```sh
make lint                # Ruff / 格式 / Pyright / ESLint / TypeScript
make test                # 真实 PostgreSQL 集成和 RLS 测试 + Vitest
make contract            # FastAPI OpenAPI → TypeScript
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm --filter @forge/web exec playwright install --with-deps chromium
bash scripts/test_browser_with_worker.sh  # 独立 worker/beat + API/生产 Web 的完整浏览器测试
uv run --project apps/api alembic -c apps/api/alembic.ini current
```

测试建立临时 TEST_* 租户并清理，只能针对开发/测试数据库运行，不能使用生产连接。测试不以 SQLite 替代 RLS。CI 使用相同 Compose 基础设施，从空卷迁移、seed、检查、生成契约和跑浏览器测试。

## 工程约定

- 库存：[施工规范](docs/inventory-v0.6.md)、[验收证据](docs/inventory-v0.6-acceptance.md)、[ADR 0012](docs/adr/0012-inventory-ledger-and-posting.md)。
- `AGENTS.md`：冻结栈、领域规则与施工边界。
- `docs/adr/`：Bootstrap、Catalog、本地搜索及库存决策。
- `docs/acceptance.md`：逐项验收与实际运行记录。
- `docs/architecture/migration-context.md`：完整迁移上下文；第 54–57 节由用户在当前任务补充，作为权威验收依据。

API 启动会拒绝高权限数据库账户。迁移和基础身份 seed 使用独立的 `MIGRATION_DATABASE_URL`；销售演示仅用该连接读取现有操作者身份和权限，业务写入使用受 RLS 约束的 `forge_app`。生产 API 不应获得迁移变量。生产配置强制 Secure Cookie 和 HTTPS Origin。登录要求 `Idempotency-Key` 与精确 `Origin`；退出操作本身幂等。Session token 仅经 HttpOnly Cookie 返回，数据库只保存 SHA-256 hash。

业务写操作进入 Application Command；HTTP Router 不实现领域规则。`RuntimeContext` 只从认证会话构建；事务内设置租户，应用查询显式限定租户，数据库再次执行 RLS。用户、组织失效和权限变更在下一请求生效。

## 迁移与运行边界

初始版本 `0001_bootstrap` 创建 forge schema、身份/RBAC、会话、审计、Outbox、幂等表和 RLS。扩展与应用角色由首次启动的 PostgreSQL init 脚本配置。修改 init 脚本不会自动更新已有卷，已有部署应追加迁移/运维变更。已合并迁移只追加、不重写。

Outbox consumer 使用 `FOR UPDATE SKIP LOCKED`，身份/库存事件记录处理元数据；商品事件在启用本地模型后更新搜索向量。核心库存过账同步提交，不依赖 worker 或 Redis。消费为至少一次语义，未来外部副作用必须按 event ID 去重。未知事件保持待处理。

OpenTelemetry 接入点已启用；可选 `SENTRY_DSN` 配置错误收集，不发送请求体。当前未配置外部追踪收集端或告警。FastAPI 的业务日志为 JSON；uvicorn access log 在标准启动命令中禁用，避免 URL 参数进入日志。

停止服务使用 `make down`，保留数据库卷。不要以删除卷作为常规升级手段。`sales-v0.8` 已发布；资金和运营分别在 `funds/v0.9`、`operations/v0.10` 评审分支，不自动合并、打标签或发布。

## 库存使用与维护

访问 `/inventory`。先在 Catalog 建立商品、单位换算和仓库，再创建期初单；保存草稿不入账，必须确认过账。库存调整用于有原因的增减；调拨同时处理两个仓库；盘点保存基准并要求过账时仍有效。过账后的原单不可编辑。

- 成本按组织/仓库/商品独立计算；基本单位成本必填时须明确输入，`0` 是有效的明确零成本。
- 仅各受影响库存键的最后一笔操作可整单冲销。有后续流水，即使后来已冲销，较早单据仍不能直接冲销。
- 盘点基准被入出库、占用或余额修复改变后，须刷新并重新核对实盘数。
- 每单最多 200 行，每页最多 100 条。低库存按组织汇总可用量，商品最低库存为 0 时关闭提醒。
- 查看数量使用 `inventory.read`；成本另需 `product.cost.read`；期初/调整/调拨/盘点/冲销/对账分别授权。现有角色不会自动被批量升级；开发 ADMIN 通过 `make seed` 补齐权限。
- 浏览器提交结果不明时保留原请求和幂等键，选择“重试原提交”。不要在另一窗口重建同一业务来猜测结果。

库存 v0.6 的迁移止于 `0007_inventory_projection`：0005 创建库存表、约束与权限，0006 固定流水翻页快照，0007 分离流水顺序与余额修订版本。当前运营分支的迁移 head 为 `0015_reporting`（资金为 `0012_funds`）。升级使用 `make migrate`，保留原 Catalog 资料和库存事实；不要删除数据库卷。

对账工具要求现存、有 `inventory.reconcile` 和 `product.cost.read` 的操作者，以及明确的组织、仓库、商品范围。默认 dry-run，实际修复必须加 `--repair`；工具与正常过账使用相同库存键锁，修复只更新投影并留审计：

```sh
uv run --project apps/api python apps/api/scripts/reconcile_inventory.py \
  --organization DEMO --actor admin@example.test \
  --warehouse <仓库UUID> --product <商品UUID>
# 核对输出后，以相同参数加 --repair 执行修复。
```

演示 seed 不替换用户库存；重复执行保留已有期初单。浏览器验收会在开发 DEMO 中留下标记为 INV-E2E 的真实单据和流水，不能删除流水来清理历史。重启电脑后需重新启动基础设施、API、Web 及所需 worker/beat；当前启动方式不是系统自启动服务。

## 采购 v0.7

入口：`http://localhost:3100/purchase`，沿用现有 DEMO 账户。

- [施工规范](docs/purchasing-v0.7.md)、[验收清单](docs/purchasing-v0.7-acceptance.md)、[ADR 0013](docs/adr/0013-purchasing-receipts-and-returns.md)。
- 升级：`make migrate seed`；可选演示：`make seed-purchasing`。演示订单100件，已收60件，待收40件；重复运行保留已有单据。
- 确认采购订单不会增加库存，收货过账才增加库存；退货不重开订单待收量。
- 一张订单一个供应商、一个仓库；禁止超收。单价按所选采购单位输入，数量与金额由服务端精确核算。
- 退货商业冲减与库存扣减成本分别记录；资金启用后，商业冲减同步到应付来源，实际退款另行登记。
- 冲销沿用严格末笔整单规则；存在后续库存变动时不能直接冲销。


## 销售 v0.8

入口：`http://localhost:3100/sales`，沿用现有 DEMO 账户。详见[施工规范](docs/sales-v0.8.md)、[验收清单](docs/sales-v0.8-acceptance.md)、[ADR 0014](docs/adr/0014-sales-reservations-returns-and-margin.md)、[最终交付记录](docs/sales-v0.8-delivery.md)和[完整目录](docs/sales-v0.8-tree.txt)。

```sh
make migrate
make seed                # 为现有 DEMO 管理员同步新增销售权限，保留已有密码
make seed-sales          # 可选，仅 development；需要现有活跃 DEMO 管理员
```

演示使用独立的 `SALE-DEMO` / `SALE-DEMO-*` 编码商品和资料，订购 100、出库 60、退货 10；初始现有/占用/可用库存为 `150 / 40 / 110`，净销售 `750`、净成本 `550`、净毛利 `200`。已创建的演示可继续练习；重复运行保留当前单据和资料，不重置库存，也不接管已有同编码资料。不要把这些示例资料用于真实经营。

- 确认订单全量占用库存；出库仅消费本单占用；退货不恢复待发或占用，补发应另开订单。
- 每个出库行按冻结单价独立四舍五入至四位金额精度，分批出库的金额合计可能与订单金额有最末位差。库存全部出完时结清库存成本，原出库行全部退回时分别结清其销售金额与成本。
- 查看销售价格与成本/毛利分别授权。数量仓管可按冻结来源处理出库、退货，服务端执行最终权限校验。
- 提交结果不明时使用“重试原提交”。认证失效、显式退出、刷新、强制关闭或浏览器崩溃后，页面内待确认请求可能丢失；先核对服务端单据与库存事实，再决定是否新建或重复操作。遇到无法识别位置的旧浏览历史条目时，回退保护只保留待确认页面，不能恢复该条旧网址。

销售 v0.8 验收时完整后端 357 项、前端 44 项、真实浏览器 11 项测试均通过，46 项验收完成，已合并并发布 `sales-v0.8`。这些是历史版本证据；当前资金版本的验证结果见资金交付记录。

## 轻量资金 v0.9

升级使用 `make migrate seed`；保留原资料和密码，仅同步开发 ADMIN 权限。访问 `/funds`，先核对启用日期与期初，再明确启用。未启用时原采购/销售继续按既有规则操作。启用后，销售出库同步形成应收、采购入库同步形成应付；订单确认不产生资金或库存。

- 收付款可分次或同时核销多个同一往来对象的来源；金额全部由服务端核对。已付款后退货形成待退款，退款与欠款分别显示。
- 旧单先通过期初绑定衔接，历史已结金额与本期实际现金分开。零余额及历史待退款余额都需要明确核对，不自动推断旧账。
- 错误用冲销纠正，保留原记录；有后续依赖时先处理依赖。冲销不会绕过原库存的严格末笔规则。
- 不确定提交使用原请求重试；资金操作有永久回执，普通幂等缓存清理后仍不会重复写入。页面刷新或关闭后的未确认操作仍应先查服务端记录。
- 本阶段仅 CNY、四位金额精度，无预收预付、自动抹零、账龄、会计凭证或银行转账执行。业务时区默认 `Asia/Shanghai`，可通过 `BUSINESS_TIMEZONE` 配置。


## 运营 v0.10

- `/imports`：下载一种资料的 xlsx 模板，上传后查看原值、清洗结果和错误，明确确认后后台逐行执行。成功行永久记录来源；只重试失败行，版本冲突需要重新预览。支持 10 种现有 Catalog 资料，单批最多 10,000 行、64 列、10MB；正文 7 天后清理，已成功的业务资料保留。[模板、字段和恢复指南](docs/import/catalog-import-v0.10-guide.md)。
- `/replenishment`：按全组织可用库存、有效在途及过去 30 个完整业务日净销量计算，展示依据。复核供应商、仓库、单位和价格后只生成采购草稿；依据变化须重新复核。不会自动确认采购、入库或付款。
- `/dashboard`：期间销售、实际收付款与当前应收应付/库存分开展示；支持日期、每日趋势与原单来源。金额和实际成本按独立权限控制，当前有效事实发生冲销后历史窗口会重新计算。

升级使用 `make migrate seed` 并重启 API、Web、worker 和 beat；已有数据与密码保留，其他角色的新增权限需要明确配置。资金仍须在 `/funds` 明确启用并核对旧账，不会自动为 DEMO 开启。补货与概览使用 `BUSINESS_TIMEZONE`（默认 Asia/Shanghai）。导入通过同源 `/api`，服务端代理等待上限 120 秒；这不是万行性能承诺。

已确认导入的手动恢复和到期清理使用同一个受限 worker 入口（不会提升执行者权限）：

```sh
IMPORT_ORGANIZATION_ID='替换为目标组织UUID'
uv run --project apps/api python -m forge_erp.workers.catalog_import \
  --organization "$IMPORT_ORGANIZATION_ID" --limit 100
```

先将变量替换为目标组织的 UUID。`--limit` 是每组织本轮最多尝试行数；省略 `--organization` 会自动发现并实际处理各组织已确认任务及到期正文，不是仅列举或仅清理。失败行仍由创建者在界面明确重试。Redis 恢复后常规定时轮询继续；也可直接运行上面命令，不依赖 embedding。
