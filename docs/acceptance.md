# Bootstrap v0.4 验收记录

权威依据：`docs/architecture/migration-context.md` 第 54、55、57 节，及用户当前施工指令。仅实施 Platform Foundation，未进入 Catalog。

## Implementation summary

建立 uv / pnpm monorepo、49 条核心施工规则、8 份初始 ADR、PostgreSQL/Redis Compose、Alembic 初始迁移、FastAPI 与 Next.js。身份链包括 Organization、User、多角色 RBAC、Argon2、opaque HttpOnly session、登录/退出/me、RuntimeContext、应用租户查询与 FORCE RLS。提供 Request ID、Problem Details、JSON logging、Audit / Transactional Outbox / Idempotency foundations。前端包括 shadcn Base UI 登录、TanStack Query provider、个人信息、导航壳和快捷导航。其余页面为明确占位。

## 原文逐项验收

| 第 55 节条目 | 证据 / 状态 |
|---|---|
| Repository structure frozen | AGENTS.md、README、docs/repository-tree.txt |
| uv.lock committed | 由 uv 生成并锁定，纳入初始 Git 提交 |
| pnpm-lock.yaml committed | 由 pnpm 生成并锁定，纳入初始 Git 提交 |
| PostgreSQL boots | 本地容器 healthy，PostgreSQL 18.6 |
| pgvector enabled | vector 0.8.6；同时 pg_trgm 1.6 |
| Redis boots | 本地容器 healthy；readyz、真实限流测试 |
| Clean DB runs alembic upgrade head | 新建专用空卷首次迁移成功，0001_bootstrap |
| FastAPI boots | Playwright 服务启动记录；启动校验 forge_app 权限 |
| Next.js boots | 生产构建及真实浏览器启动通过 |
| shadcn works | 官方 CLI 生成 Base UI button/input/label；Vitest 通过 |
| organization exists | 开发 seed DEMO；重复 seed 成功 |
| user exists | 环境变量账户，ADMIN 角色；无 Git 内固定密码 |
| login works | API 集成与真实浏览器登录通过 |
| logout works | API 集成测试通过，重复退出 204 |
| /auth/me works | API 集成测试通过；返回当前租户身份和权限 |
| secure session architecture works | HttpOnly/SameSite/Secure 测试；仅 SHA-256 hash；失效/撤销/停用测试 |
| RBAC works | 撤回 profile.read 后，后端返回 403 |
| Tenant Context works | 会话派生、伪造 header 不改变租户、提交/回滚后的连接复用测试 |
| PostgreSQL RLS works | 9 张租户表 ENABLE + FORCE + USING + WITH CHECK；forge_app 不可绕过 |
| Audit foundation works | session mutation 同事务审计；应用无 DELETE/UPDATE 权限；回滚测试 |
| Outbox foundation works | 同事务事件、SKIP LOCKED 处理、重复轮询测试；实际 Celery worker 连接 Redis 并接收任务 |
| Idempotency foundation exists | tenant/actor/operation/key 作用域、请求 fingerprint、并发 5 请求仅执行一次 |
| OpenAPI → TS works | FastAPI export → openapi-typescript → openapi-fetch；修复错误媒体类型 schema 回归 |
| structured logging works | HTTP JSON 日志包含 method/status/duration/request_id，不记录密码/Cookie/SQL 参数 |
| request ID works | X-Request-ID 传递、生成和非法值替换；Problem Details 关联一致 |
| backend tests pass | 20 passed；Ruff / format / Pyright 通过 |
| integration tests pass | 真实 PostgreSQL 18 + Redis，不使用 SQLite 替代 |
| tenancy tests pass | 跨租户读写、同邮箱隔离、权限变更、RLS policy、连接池上下文测试通过 |
| frontend checks/build pass | ESLint、TypeScript、Vitest 1 passed、Next 生产构建通过；Playwright 2 passed |
| CI green | Workflow 已编写；正在连接用户指定 Roco211/AI-ERP，尚不计为通过 |

## 小步施工与测试记录

1. 仓库/ADR/依赖/基础设施：uv 实际解析并生成锁文件，解决 Celery 对 Python Redis 客户端 `<6.5` 的约束；PostgreSQL 新卷启动、扩展/角色/迁移实查通过。
2. Identity/tenancy/atomic foundations：第一轮 11 项真实数据库测试通过；静态检查修复 Redis 客户端返回类型与 FastAPI response 字典类型后通过。
3. 前端/API contract：类型检查发现 Problem Details 的 application/problem+json 缺少 schema，从后端源头修复并重生成；新增回归断言。前端 ref 事件处理改为仅在 submit 事件内执行，ESLint 通过。
4. 完整安全边界：扩展到 20 项后端测试，全通过；新增生产 Cookie 约束、拒绝高权限连接、所有租户表策略、实际 Redis 限流和 UUIDv7 验证。
5. 前端验证：Vitest 1 项通过、生产构建通过。Next 构建与类型检查/服务启动需顺序运行；本机浏览器所需共享库缺失已通过工作目录中的包解压补齐，CI 使用 Playwright 官方 install --with-deps。

## Migration status

当前 schema revision：`0001_bootstrap`，单 head。新建 `forge` schema 以及 organizations、users、permissions、roles、user_roles、role_permissions、sessions、audit_events、outbox_events、idempotency_keys。

应用角色 forge_app：NOSUPERUSER、NOCREATEDB、NOCREATEROLE、NOBYPASSRLS，且非表 owner。迁移/seed 使用独立高权限连接。已有生产迁移只允许追加；downgrade 删除 schema，仅供开发环境，不作为生产升级方案。

## Run commands

完整命令见根 README 与 Makefile：`make env install infra migrate seed`，分别运行 `make api` / `make web`，可选 `make worker` / `make beat`。验证：`make lint test contract`、`pnpm build`、`pnpm test:e2e`。使用 `make down` 停止基础设施并保留数据。

## Architecture decisions requiring review

- ADR 0004：登录幂等重试使用 keyed HMAC 派生 opaque token，token 明文不入库；8 小时 session、24 小时幂等记录、Origin fail-closed、限流阈值需部署前复核。Next 代理后的连接 IP 可能共享，生产应确定可信代理及账户级限流策略。
- ADR 0005：登录/会话解析的 SECURITY DEFINER 窄函数，以及 worker 只读取待处理租户 ID 的桥接函数，需安全复核。RLS 防御漏写租户条件，不宣称能抵御持有应用数据库凭据后的任意 SQL。
- ADR 0006：Bootstrap consumer 仅记录身份事件处理日志；消费为至少一次，未来业务消费者必须按 event ID 去重。未处理类型保留 pending。
- 运行配置：生产应固定镜像 digest、隔离 migration credentials、设置 HTTPS/可信代理、选择追踪收集端。OpenTelemetry 接入点及可选 Sentry 已存在，外部追踪端未配置。

## Unresolved issues / release gate

GitHub Actions 真实绿色结果尚待验证。工作在当前本地 Work 任务中完成；已创建独立仓库目录，但工具未提供单独注册桌面项目条目的接口。当前任务模型/推理模式无法通过工具核实或更改，未声称选择了 GPT6-ASTRA HIGH。

完成所有门槛后建议 `git tag bootstrap-v0.4`。本次不会提前进入 Catalog，也不会在 CI 未通过时宣告 Bootstrap 完整验收通过。

## 本地最终验证（2026-09-05）

- 后端：20 passed；Ruff、Ruff format、Pyright 0 errors。
- 前端：ESLint、TypeScript、Vitest 1 passed；Next.js 16.3.4 生产构建成功。
- Playwright：2 passed（登录/同源 API/HttpOnly Cookie/导航/个人信息/退出/移动端无溢出）。
- uv sync --locked、pnpm install --frozen-lockfile：通过。
- OpenAPI 重新生成后的 git diff：无漂移。
- PostgreSQL 18.6 / vector 0.8.6 / pg_trgm 1.6；Alembic 0001_bootstrap。
- 开发 seed 连续执行两次成功。
