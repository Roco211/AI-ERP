# Funds v0.9 交付记录

用户授权同时推进资金 v0.9 与运营 v0.10。本阶段基于已发布销售版本 main `5a5c0e07053e1935ae11fdbf78609246308c8901`，使用 `funds/v0.9` 和草稿 PR #5。最终提交、当前提交的 CI 与本机运行核验记录在仓库外 `Forge-ERP-Funds-v0.9-verification.json`。未验证项目继续保留待验收状态，不以文档代替测试。

## Implementation summary

- 增加应收/应付来源、往来汇总、收付款、多来源核销、客户/供应商退款、期初、调整、旧单衔接及可追溯冲销。金额使用 Decimal/NUMERIC 四位精度，页面仅展示服务端结果。
- 资金明确启用后，销售出库与采购入库按已冻结商业金额同步记入来源；退货同步冲减。与原业务、库存、Audit、Outbox、幂等在同一事务提交。订单确认不产生现金，采购商业退款不使用当前库存成本计算。
- 旧单不自动变为欠款。期初绑定支持正欠款、已结清零余额和历史待退款，转移前后往来总额不变。历史已结金额单独记录，不混入新现金；部分付款状态包含历史结算。
- 所有七张资金表 FORCE RLS、租户复合引用及不可变约束。操作按资金读取、收付、退款、冲销、启用分权；原仓管过账无需资金权限，响应不泄露金额。
- 资金操作永久回执防止普通幂等记录到期/清理后重复登记。启用切点与原业务过账、往来余额与收付款竞争均采用明确锁序；原单详情读取避免并发数量与金额混用。
- 资金工作台支持启用/期初、部分及多来源收付、退款/冲销、来源与原单跳转；销售/采购详情按权限追加资金摘要。沿用提交结果不明时的原键/原请求重试和应用内导航保护。
- 普通 ERP Outbox 事件与可选本地 embedding 事件分开有界处理。模型关闭或暂不可用不会阻塞资金等普通事件，不改变核心事实同步提交方式。

## Migration status

追加 `0012_funds`，不改写 `0011_sales_returns` 或更早已发布迁移。新表为 `funds_activation`、`funds_sources`、`funds_entries`、`funds_cash_documents`、`funds_cash_allocations`、`funds_cash_reversals`、`funds_operations`。应用继续使用非超级用户 `forge_app`。

本机旧数据库升级前已备份，升级后保留示例订单、库存、用户和密码。开发 ADMIN 权限已同步；DEMO 不被自动启用资金或回填旧账。空库及旧版本升级由真实 PostgreSQL 18 测试验证。

## Run commands

```sh
make env
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate seed
# 分别运行
make api
make web
make worker
make beat
# 在专用开发/测试数据库验证
make lint test contract
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm test:e2e
uv run --project apps/api alembic -c apps/api/alembic.ini current
```

入口 `http://localhost:3100/funds`，沿用原开发账户；旧 `/finance` 自动转向资金页并保留筛选。配置 `BUSINESS_TIMEZONE` 控制业务日期，默认 Asia/Shanghai。不要删除数据库卷作为升级步骤，也不要在真实经营数据上运行测试。

## Test results

验收回归进行中，当前有实际证据：完整后端 468 项通过（493.87 秒），随后采购详情一致性与历史部分结算修复的 40 项专项通过（包含新增 3 项）。最终当前提交的完整 CI 将重新覆盖全部测试。

资金专项覆盖普通收付/退款、正负期初衔接、商业过账联动、七表跨租户/不可变约束、四方向余额竞争、切换锁等待、永久回执以及业务/Audit/Outbox/COMMIT 故障。真实浏览器、最终前端构建、契约与 CI 结果完成后在此补齐。

完整目录见 [funds-v0.9-tree.txt](funds-v0.9-tree.txt)，27 项映射见[验收清单](funds-v0.9-acceptance.md)。

## Unresolved issues / architecture decisions requiring review

- 待确认提交保存在当前页面内存。应用内导航可保护原请求；刷新、跨文档跳转、退出、会话失效或浏览器崩溃后先查询服务器记录。本轮不在本地长期保存敏感业务请求。
- 启用点前的旧账必须人工核对。系统支持有证据的期初和来源绑定，不假定旧出入库都尚未收付，也不把缺少历史来源映射当成完整账务。
- CNY 与四位金额精度、正负余额分开展示、无自动跨来源抵销/抹零、无预收预付为本阶段范围。退款和冲销保留事实且检查后续依赖；这些业务选择应由门店使用者复核。
- 采购/销售原单仍遵守已发布库存冲销约束。资金依赖可能进一步阻止冲销，不做自动级联。
- 普通 ERP Outbox 事件已有独立处理通道；可选本地模型的健康与重新建索引仍属于独立维护事项。
- 无生产容量、长期归档或高可用部署承诺。既有 TanStack Table / React Compiler 和 Vite 配置提示作为工具链维护项保留。

验收完成后建议评审合并并创建 `funds-v0.9`。本轮不自动合并、打标签或发布；通过本阶段后继续已经授权的运营 v0.10。
