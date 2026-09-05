# Sales v0.8 — 最终交付记录

本轮完成 S5，沿用 `sales/v0.8` 与草稿 PR #4，基线为已发布 `purchasing-v0.7` / main `64bec0a`。本文件汇总 S1–S5；各增量记录保留当时状态。最终提交 SHA、GitHub CI 链接和本地运行核验记录在仓库外的 `Forge-ERP-Sales-v0.8-verification.json`，避免提交内自引用 SHA。发布、合并与标签创建不属于本轮动作。

## Implementation summary

- 订单支持草稿、确定性客户报价、手动价审计、确认占用、取消及关闭。确认只增加占用；只有实际出库减少现有库存并记录当时实际成本。履约状态与业务状态分离，关闭释放剩余占用，退货不重新产生待发数量。
- 分批出库冻结原订单客户、仓库、商品、单位因子、价格及来源。InventoryEngine 通过独立占用来源消费本订单库存，拒绝超发、跨订单、跨仓库和跨租户消费。所有相关写入、Audit、Outbox 和幂等记录原子提交。
- 退货关联原出库，按原价冲减销售、按原实际成本回收库存；两种金额尾差分别处理。严格末笔整单冲销保留原事实，不支持级联冲销；关闭后不能冲销出库。毛利按有效过账事实计算，历史成交价及其单位快照可追溯。
- 销售工作台提供订单、出库、退货、成交历史，沿用现有键盘选品、表单、分页和生成 DTO。服务端计算金额、库存和毛利；销售员与仓管可使用各自独立权限，敏感字段由后端过滤。
- S5 补齐出库幂等过期、清理、撤权及并发矩阵；修复订单关键字含反斜杠时的字面搜索。补充极小单价、分批出库库存成本尾差与全退净额归零回归，保持已有逐出库行舍入规则。
- S5 增加可选开发销售样例，全部业务写入走现有 Command；认证操作人及持久化权限来自现有 DEMO 管理员。重复执行检查原始来源和事实，保留当前业务进展；无法核实的标记或冲突资料也保留，不自动接管、重建或重置。
- S5 增强未知结果提交的导航保护：保留原请求体和幂等键，并拦截当前页面内的应用链接和浏览器历史切换。应用挂载时提前注册历史事件分发器，跟踪条目位置并恢复原条目，避免路由先卸载待重试请求或追加条目破坏前进、后退顺序。无法识别位置的旧历史条目只保留待确认页面，该条目被替换的旧网址不能恢复。具体边界见下文。
- 应用、API 元数据与前后端包版本统一为 `0.8.0` / `Sales`，作为本分支待发布版本。没有升级依赖、增加基础设施或实现资金及 AI 业务工具。

## Migration status

当前 head 为 `0011_sales_returns`。销售阶段追加 `0009_sales`、`0010_sales_shipments`、`0011_sales_returns`；S4/S5 没有追加迁移或改写旧迁移。空库建库、0008 采购数据升级、0009 已确认占用升级及 0010 已过账出库升级均在回归内检查，保留原价格、成本、权限、库存和占用事实。

新销售表沿用 `organization_id`、FORCE RLS、租户复合关联以及非超级用户 `forge_app`。库存事实仍由不可变 Movement 和可重建 Balance/Reservation 投影组成，普通业务模块不能直接修改投影。

## Run commands

```sh
# 首次本地配置；已有 .env 会保留，不把凭据写入文档
make env
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate seed

# 可选：创建销售演示；重复执行保留已有业务事实
make seed-sales

# 分别在终端运行
make api
make web
make worker
make beat

# 检查；完整后端测试应使用专用测试数据库
make lint
uv run --project apps/api pytest apps/api/tests -q
pnpm test
make contract
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm test:e2e
```

访问 `http://localhost:3100/sales`，使用现有开发企业和账户。已有环境先运行 `make seed` 同步管理员销售权限，密码沿用已有配置。写请求继续使用同源 `/api`、HttpOnly Cookie、Origin 校验和幂等键。不要使用删除数据库卷作为升级步骤。

本机已创建演示订单 `SO-60C0B3FFB17C4D55`：初始库存 200、订购 100、出库 60、退货 10；核验时现有 150 / 占用 40 / 可用 110，净销售 750 / 净成本 550 / 毛利 200。再次初始化返回 `preserved`，订单和事实保持不变。可直接访问：`http://localhost:3100/sales?order=8dad4947-c498-474d-aa74-02b035a594ee`。以后操作会改变这些实时数值，页面现状为准；此订单 ID 只属于本机演示环境。

## Test results

- 完整后端 **357 passed / 0 skipped，399.39 秒**，包含 Catalog、Inventory、Purchasing、Sales、并发、事务、RLS、迁移和提交边界。版本元数据最后统一后追加平台专项 **11 passed，4.78 秒**；最终提交 CI 再运行完整后端。
- Ruff、111 个 Python 文件格式检查和 Pyright 通过，Pyright 0 error / 0 warning。最终 `uv sync --locked`、`pnpm install --frozen-lockfile` 均通过，依赖版本保持不变。
- 前端完整 Vitest **44 passed / 6 files，42.63 秒**，其中工作台 17 项、导航保护 7 项。前端 lint、生产 build、构建后 typecheck 均通过；lint 仅有 4 条既有 TanStack Table / React Compiler 提示。
- OpenAPI 重新导出并生成 TypeScript 后，已提交契约无差异。
- 当前正式构建的真实浏览器 **11 passed，约 1 分钟**：销售主链 10.3 秒、无成本销售员 7.3 秒、无价格仓管 8.1 秒、Forward 丢响应重试 7.5 秒，以及既有登录/商品/库存/采购 7 项。Back/Forward 拦截与解锁后历史顺序、原键原 body、仅一次库存事实均通过，未使用诊断注入。
- 代码验收提交 `9d7961e` 的 [push CI](https://github.com/Roco211/AI-ERP/actions/runs/33977824715) 与 [PR CI](https://github.com/Roco211/AI-ERP/actions/runs/33977827345) 均成功，覆盖完整测试、生产构建及浏览器回归。最终文档提交同样要求两个 CI 成功；其 SHA、结论和 run 链接在 verification 附件再次核验，不用旧提交结果替代。

S5 新增后端专项：出库幂等 21 项、搜索 3 项、舍入 1 项、开发样例 16 项。幂等清理用例显式模拟已经删除过期记录后的状态，不表示新增 TTL 清理 worker。前期针对 S4 运行版本的浏览器回归确实复现了待重试提交在 Back/Forward 时离开销售页的问题，修复后当前正式构建中的两个导航回归均已通过。

完整仓库目录见 [sales-v0.8-tree.txt](sales-v0.8-tree.txt)，46 项映射见 [验收清单](sales-v0.8-acceptance.md)。

## Unresolved issues / architecture decisions requiring review

- 未知结果提交保存在当前页面内存。应用内链接及当前应用会话已跟踪的 Back/Forward 受保护；刷新、跨文档跳转、显式退出、会话过期、强制关闭或浏览器崩溃不能保证恢复原请求。重新认证后先核对服务器单据再发起新写入。跨重启恢复需要独立持久化设计，本轮不缓存本地敏感业务数据。
- 每个出库行独立按冻结单价计算并舍入到金额 4 位，拆单销售合计可能与订单金额相差最末位。例如 `3 × 0.00005` 的订单为 `0.0002`，分成三笔各 1 件出库时合计 `0.0003`。全出清零保证针对库存成本，全退清零针对原出库行的销售金额和成本；未引入订单层金额分摊。改变此规则需要单独评审和迁移。
- 保持 ADR0014 的全量占用、退货不补发、原成本回收、严格末笔整单冲销、闭单后不冲销出库、全退有效成交保留等选择。评审应确认这些规则符合门店实际操作。
- 页面库存反映查询当时情况，其他订单可能随后改变库存；过账仍执行实时校验。列表分页不保证跨页数据库快照，没有生产容量或性能承诺。
- 开发 seed 只用于可信开发环境。原标记或资料无法核实就保留现状，不能作为生产初始化、数据修复或幂等记录清理服务。
- TanStack Table / React Compiler 与既有 Vite 配置提示属于工具链待维护项；它们不等于本次测试失败。旧 GitHub Actions 的 Node20 声明由 runner 强制使用 Node24，后续可单独维护。

销售代码及本地完整验收已通过，46 项映射齐备；最终交付以 verification 附件所记录提交的两个绿色 CI 为准。建议评审合并后创建 `sales-v0.8` 标签。本轮保持 PR #4 草稿，不创建标签或 GitHub Release，不进入下一业务模块。
