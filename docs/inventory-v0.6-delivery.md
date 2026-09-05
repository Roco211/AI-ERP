# Forge ERP 库存 v0.6 交付报告

日期：2026-09-05。范围 I0–I5；沿用现有架构。代码与63项验收已完成，源码提交 `b6fbe2aa1d88d61ce59af9840d9a27b161847d21` 的 GitHub CI 全绿。独立草稿 PR #2：https://github.com/Roco211/AI-ERP/pull/2，基于 Catalog PR #1，尚未合并。完整逐项证据见 [验收清单](inventory-v0.6-acceptance.md)。源码 [CI run](https://github.com/Roco211/AI-ERP/actions/runs/33957296487) 已通过；文档提交继续执行完整CI，最终提交/run由外部交付报告记录。

## Implementation summary

新增库存工作台：库存总览、不可变流水与来源单据、低库存、期初、调整、调拨、盘点、整单冲销。内部库存引擎支持占用、消费和释放，为后续销售集成保留能力；本阶段没有实现销售单或人工占用入口。

库存事实与余额投影分离，Decimal/NUMERIC计算移动平均成本；双仓操作和业务/Audit/Outbox/幂等同事务。新增表强制租户 RLS，HTTP 使用既有 Cookie Runtime Context 与同源 API。权限区分数量、成本、各类操作、冲销和维护。

盘点保留基准、实盘和单位换算快照。库存变动或对账修复使旧基准失效，需显式刷新重核。浏览器对响应丢失保留原请求/原幂等键重试；修复了 ProductPicker 在多个匹配项时第一次向下键跳过首项的问题，并增加回归测试。修复快速切换单据时旧请求覆盖新详情的时序问题，过账确认在数据刷新后关闭。表单/表格/请求分别沿用 React Hook Form、TanStack Table、TanStack Query；没有增加基础设施。

## Final repository tree

详见同目录 `inventory-v0.6-tree.txt`。主要新增：

```text
apps/api/migrations/versions/0005_inventory.*
apps/api/migrations/versions/0006_inventory_snapshot.py
apps/api/migrations/versions/0007_inventory_projection_version.py
apps/api/src/forge_erp/modules/inventory/
  domain/{schemas,values}.py
  application/{documents,engine,queries,maintenance}.py
  api/router.py
apps/api/scripts/{seed_inventory,reconcile_inventory}.py
apps/api/tests/test_inventory_{engine,documents,safety}.py
apps/web/features/inventory/client.ts
apps/web/features/inventory/workspace.tsx
apps/web/e2e/inventory.spec.ts
apps/web/tests/{inventory,picker}.test.tsx
docs/inventory-v0.6*.md
docs/adr/0012-inventory-ledger-and-posting.md
```

## Run commands

仓库根目录运行；需要已安装 uv、Node24、pnpm11.7 和 Docker Compose：

```sh
make env
make install infra migrate seed
make seed-catalog       # 可选
make seed-inventory     # 可选，独立INV-DEMO演示商品/仓库
make api               # 独立终端
make web               # 独立终端；生产构建可 pnpm build 后 pnpm --filter @forge/web start
make worker            # 独立终端
make beat              # 独立终端
```

体验地址 http://localhost:3100/inventory，企业代码 DEMO，账户沿用已有开发账户，密码存于本地 `.env`，报告不复制凭据。演示商品 INV-DEMO-BOLT 期初100，基本单位成本1.25，金额125。账户权限通过原 RBAC 管理；开发 ADMIN 可用 make seed 补齐新权限。

只读对账（实际UUID替换占位符）：

```sh
uv run --project apps/api python apps/api/scripts/reconcile_inventory.py \
  --organization DEMO --actor admin@example.test \
  --warehouse <仓库UUID> --product <商品UUID>
```

默认 dry-run；确认范围和差异后显式加 `--repair`。必须有现存授权操作者；修复与正常写入同键互斥，只改投影并留审计。不得通过普通SQL覆盖余额。

## Migration status

实际 head：`0007_inventory_projection`。0005建表/RLS/约束/权限，0006加入流水事务快照，0007分离 movement_sequence 和余额 version。空库及0001、带数据0004/0005/0006升级已验证。开发数据库升级完成，余额事实序号与流水水位检查 **0处不一致**；未删除或重写历史资料/库存事实。

## Test results

- 锁定安装：uv sync --locked、pnpm frozen lockfile通过。
- Ruff、format、Pyright、ESLint、TypeScript通过；Pyright零错误；ESLint仅2条TanStack Table/React Compiler兼容性提示。
- 全新临时数据库的完整后端84项通过；新增6项权限/快照检查连同原安全测试共26项通过。完整90项后端测试已在GitHub CI通过。
- Vitest 6项通过；生产build通过；真实Playwright 5项通过，包含Bootstrap、Catalog、库存、移动端和实际只读账户；库存额外连续3轮共6项通过。
- OpenAPI重新导出并生成TypeScript无drift。
- 逐项验收见清单；源码CI链接及提交见本文开头；最终文档提交的run在外部交付记录中提供。没有将skipped或历史Catalog CI计为本阶段成功。

可复验命令：

```sh
make lint test contract
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm --filter @forge/web exec playwright install --with-deps chromium
pnpm test:e2e
```

本机CPU为AMD Ryzen5 5600X，12逻辑核、15GiB内存、WSL2。500商品/500余额/500事实的暖查询：库存总览26–34ms、流水3–4ms、低库存323–376ms；200行过账约1.21–1.27s（Command测量，不含整个浏览器提交链）。DEMO 14余额时，真实同源API约9–34ms，筛选页导航到结果可见约104ms。完整数据与查询计划见 `evidence/`。这些数据不构成生产容量保证。

## Unresolved issues

- PR #1、PR #2均需评审并按依赖顺序合并；尚未发布生产或创建版本标签。
- 生产规模、峰值并发、备份恢复演练不在本次本机容量验证结论内。低库存聚合需在实际大规模数据下继续量测。
- GitHub对现有Actions的Node20声明发出弃用提示，实际runner以Node24执行且已成功；后续维护时更新Action版本。
- 当前服务以开发启动方式运行，重启机器后需重新启动；不是系统自启动部署。
- 开发浏览器测试留下INV-E2E标记的真实单据/流水；保留事实历史，不删除流水清理。

## Architecture decisions requiring review

D1–D6已按用户授权实施，不存在等待重新设计的阻塞。合并时重点复核：成本按仓独立；只允许各库存键末笔整单冲销（后续单即使已冲销仍阻止较早单）；盘点基准变更需要重核；最低库存按组织汇总；最多200行/单；数据库RLS隔离租户，模块写边界由单一Engine入口与测试约束，不能声称数据库凭据能区分应用模块。

全部验收和最新提交CI通过后，建议在正式合并提交创建 `inventory-v0.6` 标签；本次不自动打tag、不进入采购/销售施工。
