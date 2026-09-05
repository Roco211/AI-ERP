# Forge ERP · Purchasing v0.7

五金商贸 ERP。基于已验收的 Bootstrap v0.4，Catalog 增加分类、品牌、单位、商品、价格、客户、供应商、供货关系、仓库资料和快捷选品。可选本地语义搜索使用 Ollama/BGE-M3（[安装说明](docs/local-embeddings.md)）；库存 v0.6 已加入期初、调整、调拨、盘点、库存流水与低库存查询。采购 v0.7 已加入订单、分批收货、退货、冲销与历史采购价。销售、资金和 AI 业务功能未开放。详见 [Catalog 使用与验收](docs/catalog-v0.5.md) 和 [Excel 导入基础设计](docs/import/catalog-import-design.md)。

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
make api                 # 终端 1
make web                 # 终端 2
make worker              # 终端 3，可选后台消费者
make beat                # 终端 4，可选 Outbox 定时轮询
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
pnpm test:e2e            # 启动 API 和生产 Web，登录、Catalog、库存、只读权限、移动端真实流程
uv run --project apps/api alembic -c apps/api/alembic.ini current
```

测试建立临时 TEST_* 租户并清理，只能针对开发/测试数据库运行，不能使用生产连接。测试不以 SQLite 替代 RLS。CI 使用相同 Compose 基础设施，从空卷迁移、seed、检查、生成契约和跑浏览器测试。

## 工程约定

- 库存：[施工规范](docs/inventory-v0.6.md)、[验收证据](docs/inventory-v0.6-acceptance.md)、[ADR 0012](docs/adr/0012-inventory-ledger-and-posting.md)。
- `AGENTS.md`：冻结栈、领域规则与施工边界。
- `docs/adr/`：Bootstrap、Catalog、本地搜索及库存决策。
- `docs/acceptance.md`：逐项验收与实际运行记录。
- `docs/architecture/migration-context.md`：完整迁移上下文；第 54–57 节由用户在当前任务补充，作为权威验收依据。

API 启动会拒绝高权限数据库账户。迁移/seed 使用独立的 `MIGRATION_DATABASE_URL`；生产 API 不应获得该变量。生产配置强制 Secure Cookie 和 HTTPS Origin。登录要求 `Idempotency-Key` 与精确 `Origin`；退出操作本身幂等。Session token 仅经 HttpOnly Cookie 返回，数据库只保存 SHA-256 hash。

业务写操作进入 Application Command；HTTP Router 不实现领域规则。`RuntimeContext` 只从认证会话构建；事务内设置租户，应用查询显式限定租户，数据库再次执行 RLS。用户、组织失效和权限变更在下一请求生效。

## 迁移与运行边界

初始版本 `0001_bootstrap` 创建 forge schema、身份/RBAC、会话、审计、Outbox、幂等表和 RLS。扩展与应用角色由首次启动的 PostgreSQL init 脚本配置。修改 init 脚本不会自动更新已有卷，已有部署应追加迁移/运维变更。已合并迁移只追加、不重写。

Outbox consumer 使用 `FOR UPDATE SKIP LOCKED`，身份/库存事件记录处理元数据；商品事件在启用本地模型后更新搜索向量。核心库存过账同步提交，不依赖 worker 或 Redis。消费为至少一次语义，未来外部副作用必须按 event ID 去重。未知事件保持待处理。

OpenTelemetry 接入点已启用；可选 `SENTRY_DSN` 配置错误收集，不发送请求体。当前未配置外部追踪收集端或告警。FastAPI 的业务日志为 JSON；uvicorn access log 在标准启动命令中禁用，避免 URL 参数进入日志。

停止服务使用 `make down`，保留数据库卷。不要以删除卷作为常规升级手段。全部验收通过且 GitHub CI 绿色后，建议创建 `purchasing-v0.7` 标签；本次交付使用独立评审分支，不自动合并依赖 PR。

## 库存使用与维护

访问 `/inventory`。先在 Catalog 建立商品、单位换算和仓库，再创建期初单；保存草稿不入账，必须确认过账。库存调整用于有原因的增减；调拨同时处理两个仓库；盘点保存基准并要求过账时仍有效。过账后的原单不可编辑。

- 成本按组织/仓库/商品独立计算；基本单位成本必填时须明确输入，`0` 是有效的明确零成本。
- 仅各受影响库存键的最后一笔操作可整单冲销。有后续流水，即使后来已冲销，较早单据仍不能直接冲销。
- 盘点基准被入出库、占用或余额修复改变后，须刷新并重新核对实盘数。
- 每单最多 200 行，每页最多 100 条。低库存按组织汇总可用量，商品最低库存为 0 时关闭提醒。
- 查看数量使用 `inventory.read`；成本另需 `product.cost.read`；期初/调整/调拨/盘点/冲销/对账分别授权。现有角色不会自动被批量升级；开发 ADMIN 通过 `make seed` 补齐权限。
- 浏览器提交结果不明时保留原请求和幂等键，选择“重试原提交”。不要在另一窗口重建同一业务来猜测结果。

迁移 head 为 `0007_inventory_projection`：0005 创建库存表、约束与权限，0006 固定流水翻页快照，0007 分离流水顺序与余额修订版本。升级使用 `make migrate`，保留原 Catalog 资料和库存事实；不要删除数据库卷。

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
- 退货参考金额与库存扣减成本分别记录，不代表应付、付款或退款。
- 冲销沿用严格末笔整单规则；存在后续库存变动时不能直接冲销。
