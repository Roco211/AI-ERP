# Sales v0.8 — S2 交付记录

本增量基于sales/v0.8的S1提交135e87c，实现销售分批出库后端。用户在S1交付后回复“请继续”；草稿PR #4继续使用，不合并、不发布销售版本、不进入资金/AI业务。

## Implementation summary

- POST /api/v1/sales/shipments：从单一已确认订单创建出库草稿，只接收来源订单/行、数量和原因。继承客户、仓库、商品、单位、确认时因子与单价；草稿零库存影响。
- PUT /api/v1/sales/documents/{id}/draft：替换草稿行并更新版本；不能更换来源订单，不能输入价格、成本、租户或占用ID。
- POST /api/v1/sales/documents/{id}/post：按订单→相关凭据→有效资料/换算→库存键→占用加锁；重查状态、版本、累计已发和来源，再消费本单占用并记录ISSUE。全发保持CONFIRMED，履约状态为FULFILLED；关闭释放未发，不重开。
- GET documents列表/详情：默认只看数量，销售价格和实际成本独立授权；成本还需价格读取权限。实际成本来自本次不可变ISSUE流水，不用以后均价回算。毛利汇总等待S3。
- InventoryEngine新增sales.ship能力和跨凭据来源核验，保留原库存同行限制。出库行/占用行ID独立；普通库存及采购命令不能代为操作销售单。
- 审计包含实际前后状态和成本事实，Outbox仅元数据；幂等和全部库存/单据/事件一起提交，成功HTTP响应之前COMMIT。Redis/worker不决定核心过账成功。
- 详情共享锁保持单据内部一致；列表锁定选中订单后重新筛选，避免并发状态变化混入不匹配项。页大小≤100、每单≤200行；金额总额受20,4上限约束。

## Migration status

追加0010_sales_shipments（父版本0009_sales），不修改旧迁移。新增inventory_document_lines.reservation_source_line_id、租户自引用外键、索引、受调用者权限与RLS约束的只读来源校验函数、延迟关联约束。

SQL延迟校验兼容先插入库存行再插入销售附属行及草稿整行替换；引擎在ISSUE之前立即核验。历史查询fixture的空链接继续可读，但不能经引擎消费库存。不将该兼容性描述为SQL独立拒绝所有非法草稿。

空库及0001/0004/0005/0006/0007/0008/0009升级已验证；0009用确认订单、冻结价格、占用凭据/流水/投影证明原事实保留。开发库升级记录与最新CI见本轮verification附件。

## Run commands

使用原有本地环境配置，在仓库根目录运行：

```sh
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate
make api
# 另一终端
make web
# 需要为开发管理员同步S1已有销售权限时
make seed
# 后端与来源回归
uv run --project apps/api pytest apps/api/tests/test_sales_shipments.py apps/api/tests/test_sales_shipment_safety.py apps/api/tests/test_sales_reservation_sources.py apps/api/tests/test_sales_read_consistency.py -q
make lint test contract
pnpm build
```

API继续使用HttpOnly Cookie会话与同源/api；写操作需要Origin、Idempotency-Key及sales.ship权限。销售页面尚未实施，本阶段由API与真实数据库自动测试验收。不在文档记录本地凭据。

## Test results

定向验证：36项出库集成/安全测试、15项引擎来源测试、2项列表读取一致性测试通过；含真实采购与库存命令的混合路径。额外运行迁移、S1价格/订单和ASGI边界回归。完整后端回归 **219 passed in 191.01s**，无跳过；Ruff、格式检查、Pyright 通过。前端 lint、typecheck、Vitest **12 passed** 和生产构建通过。最新提交的 CI 与浏览器 smoke 结果由本轮最终 verification 附件记录，避免将旧提交的通过结果计入本次验收。

全阶段验收46项中17项有完整证据；其余保留待办，详见[清单](sales-v0.8-acceptance.md)。完整仓库目录见[sales-v0.8-s2-tree.txt](sales-v0.8-s2-tree.txt)。

## Unresolved issues / architecture decisions requiring review

- S3退货、销售冲销、净毛利/历史价格工作台，S4销售UI，S5可选seed与全阶段交付仍未实现；当前没有销售reverse或return路由。
- S2保留实际出库成本事实，S3才能按原实际成本退货并恢复合法冲销占用；不应提前放开通用库存冲销销售单。
- 并发分页可能变短或总数变化；重新筛选保证已返回行符合当前锁定状态，并不提供跨页时间快照。
- 现有3项TanStack Table/React Compiler警告保留。未做生产容量承诺，未新增基础设施或升级大版本。
- ADR0014原决策延续，没有改变确认占用、冻结换算、原出库成本或权限语义。S3重点复核退货不补发、闭单后禁止旧出库冲销及退货金额/成本两种尾差。
- 不调整0.7.0已发布版本标识，不建议sales-v0.8标签；完整销售验收通过后再评估发布。
