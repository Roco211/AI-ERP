# Forge ERP · Catalog v0.5

五金商贸 ERP。基于已验收的 Bootstrap v0.4，Catalog 增加分类、品牌、单位、商品、价格、客户、供应商、供货关系、仓库资料和快捷选品。可选本地语义搜索使用 Ollama/BGE-M3（[安装说明](docs/local-embeddings.md)）；库存、销售、采购、资金和 AI 业务功能未开放。详见 [Catalog 使用与验收](docs/catalog-v0.5.md) 和 [Excel 导入基础设计](docs/import/catalog-import-design.md)。

## 本地启动

需要 Docker Compose、uv、Node.js 24、pnpm 11.7.0。uv 自动获取 Python 3.14。

```sh
make env                 # 仅首次：生成本地随机凭据，不覆盖已有 .env
make install
make infra               # PostgreSQL 18 + vector + pg_trgm，Redis
make migrate
make seed                # DEMO / ADMIN；账户来自 .env 的 SEED_ADMIN_*
make seed-catalog        # 可选：五金示例商品及关联资料
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
pnpm test:e2e            # 启动 API 和生产 Web，登录/导航/退出与移动端 smoke
uv run --project apps/api alembic -c apps/api/alembic.ini current
```

测试建立临时 TEST_* 租户并清理，只能针对开发/测试数据库运行，不能使用生产连接。测试不以 SQLite 替代 RLS。CI 使用相同 Compose 基础设施，从空卷迁移、seed、检查、生成契约和跑浏览器测试。

## 工程约定

- `AGENTS.md`：冻结栈、领域规则与施工边界。
- `docs/adr/`：8 份初始决策及需复核的实现细节。
- `docs/acceptance.md`：逐项验收与实际运行记录。
- `docs/architecture/migration-context.md`：完整迁移上下文；第 54–57 节由用户在当前任务补充，作为权威验收依据。

API 启动会拒绝高权限数据库账户。迁移/seed 使用独立的 `MIGRATION_DATABASE_URL`；生产 API 不应获得该变量。生产配置强制 Secure Cookie 和 HTTPS Origin。登录要求 `Idempotency-Key` 与精确 `Origin`；退出操作本身幂等。Session token 仅经 HttpOnly Cookie 返回，数据库只保存 SHA-256 hash。

业务写操作进入 Application Command；HTTP Router 不实现领域规则。`RuntimeContext` 只从认证会话构建；事务内设置租户，应用查询显式限定租户，数据库再次执行 RLS。用户、组织失效和权限变更在下一请求生效。

## 迁移与运行边界

初始版本 `0001_bootstrap` 创建 forge schema、身份/RBAC、会话、审计、Outbox、幂等表和 RLS。扩展与应用角色由首次启动的 PostgreSQL init 脚本配置。修改 init 脚本不会自动更新已有卷，已有部署应追加迁移/运维变更。已合并迁移只追加、不重写。

Outbox consumer 使用 `FOR UPDATE SKIP LOCKED`，Bootstrap 只记录身份事件处理日志；不提供业务事件处理器。消费为至少一次语义，未来外部副作用必须按 event ID 去重。未知事件保持待处理。

OpenTelemetry 接入点已启用；可选 `SENTRY_DSN` 配置错误收集，不发送请求体。当前未配置外部追踪收集端或告警。FastAPI 的业务日志为 JSON；uvicorn access log 在标准启动命令中禁用，避免 URL 参数进入日志。

停止服务使用 `make down`，保留数据库卷。不要以删除卷作为常规升级手段。全部验收通过且 GitHub CI 绿色后，建议创建 `bootstrap-v0.4` 标签。
