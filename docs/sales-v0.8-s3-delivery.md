# Sales v0.8 — S3 交付记录

本增量从 S2 提交 `292b46e` 继续。用户在 S2 交付后回复“OK，请继续”，授权按已有规范实施退货、冲销、毛利和成交历史。继续使用 `sales/v0.8` 与草稿 PR #4，S4 销售页面、S5 最终交付仍待完成。

## Implementation summary

- `POST /api/v1/sales/returns` 按原 POSTED 出库建立退货草稿。只接受原单/原行、数量和原因；继承客户、仓库、商品、单位、确认时换算和原单价。公共草稿修改、过账接口按单据类型选择对应命令与权限。
- 退货保存时计算两种独立快照：原售价的销售冲减金额、原实际 ISSUE 成本的回收金额。成本按原数量比例直接分配，内部 Decimal 精度 50，不先舍入单位成本；最后退完分别取剩余总额，清掉各自尾差。部分舍入超过剩余金额明确返回 `RETURN_PRECISION_CONFLICT`。
- 退货 POST 在父订单和来源单据锁内重算额度与金额，超量拒绝；其他退货改变了已保存的金额/成本时返回 `RETURN_QUOTE_CHANGED`，要求重新保存复核。准确成本交给 InventoryEngine 入库，参与当前移动均价；不恢复历史均价。
- 订单 CLOSED 后仍可退货。退货不减少有效已发、不恢复待发或占用；补发另开订单。新退货及 POST 仍要求资料有效，历史合法冲销允许资料后来停用。
- `POST /api/v1/sales/documents/{id}/reverse` 沿用严格末笔整单冲销。出库要求订单 CONFIRMED、无有效退货，并恢复原库存/价值/均价和原订单占用；退货冲销恢复原库存状态与可退额度，不改变订单占用。后来发生的流水即使被冲销，也不会重新放开更早单据。
- 成本仅来自不可变 ISSUE/RECEIVE 事实。有效出库毛利为销售金额减实际成本；有效退货显示带符号的毛利影响（成本回收减销售冲减）。DRAFT/REVERSED 不计已实现毛利；已冲销单保留其原成本供有权用户追溯。
- 订单详情/列表提供有效出库、退货及净销售金额；成本和净毛利需要销售读取、价格读取、成本读取三项权限。数量仓管仅需对应出入库权限执行命令；回执仅返回 id/status/version/request_id。
- `GET /api/v1/sales/price-history` 按 posted_at/单据 ID/行 ID 稳定分页，筛选客户和商品；排除草稿、仅确认订单和冲销出库。全部退回的未冲销成交仍保留，显示已退数量和金额。读取仅包含冻结交易快照，不附带成本。
- Audit、Outbox、幂等和全部库存/业务事实保持同一事务。新增 reverse 事件注册；真实 ASGI 测试在成功响应头发送时从独立连接验证提交结果。

## Migration status

追加 `0011_sales_returns`，父版本 `0010_sales_shipments`；旧迁移不改写。

- `inventory_document_lines.original_line_id` 使用租户复合自引用外键追溯原出库行。
- `sales_document_lines.return_cost` 保存不可变的准确退货成本快照，使用 NUMERIC(20,4)，只允许销售退货使用。
- INVOKER 来源校验函数与延迟约束保护原订单/出库/客户/仓库/商品/单位/换算/单价关联。引擎在变更前立即复核有效来源、累计额度和准确金额。
- 冲销读取数据库中的原始流水，拒绝调用方伪造的数量/成本/来源，并核对同单冲销凭据和能力。
- 空库以及 0001/0004/0005/0006/0007/0008/0009/0010 升级验证通过。0010 fixture 含真实已发数量、消费占用、独立来源和 ISSUE 成本事实；升级后保持一致。

开发库状态、最终提交与 CI run 见本轮 verification 附件。

## Run commands

沿用本地环境配置；不在文档记录凭据：

```sh
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate
make api
# 另一终端
make web
# 如需为开发管理员同步已有销售权限
make seed
# S3 专项回归
uv run --project apps/api pytest apps/api/tests/test_sales_returns.py apps/api/tests/test_sales_return_safety.py apps/api/tests/test_sales_return_sources.py apps/api/tests/test_sales_margin_history.py -q
make lint test contract
pnpm build
pnpm test:e2e
```

继续使用同源 `/api`、HttpOnly Cookie 和 Runtime Context。写请求需要 Origin、Idempotency-Key；冲销额外需要 `sales.reverse` 与对应 `sales.ship` 或 `sales.return`。销售操作页面将在 S4 实施，本轮通过 API 和真实数据库测试验收。

## Test results

专项验证包括原成本回收、两种尾差、额度并发、退货/冲销失败回滚、关闭竞争、数量角色、撤权后幂等、RLS、来源伪造、不可变事实和重建。独立审查发现退货冲突错误码分类问题，已修复并由回归覆盖；变更原退货来源在锁定新来源之前明确拒绝。

完整后端回归 **305 passed in 351.54s**，无跳过。Ruff、格式检查和项目配置的 Pyright 通过；前端 lint、typecheck、Vitest **12 passed** 和生产构建通过。最新提交 CI 和浏览器结果保存在最终 verification 附件中，避免把旧提交 CI 当作本次验收。阶段清单现为 **34/46**，S4/S5 待办继续保留。

完整目录见 [sales-v0.8-s3-tree.txt](sales-v0.8-s3-tree.txt)，阶段验收见[清单](sales-v0.8-acceptance.md)。

## Unresolved issues / architecture decisions requiring review

- S4 销售工作台、S5 可选 seed、最终幂等矩阵和全阶段验收仍未完成；当前不建议 `sales-v0.8` 标签，不合并或发布草稿 PR。
- 退货草稿因需要明确金额，保存时就拒绝超出剩余可退量；POST 再检查。与 S2 允许超出待发量的无库存影响出库草稿不同，此处需要可确定的原金额分配。
- 退货单毛利字段使用带符号的净影响，便于与出库毛利相加；销售页面应清楚标注销售冲减和成本回收。
- 成交价历史保留全退成交；闭单后禁止出库冲销；退货不补发。这些延续 ADR0014，不改变已有业务语义。
- 列表/历史共享父订单锁并复核筛选；并发变化可能让当前页变短或总数变化，不提供跨页时间快照。未做生产容量承诺。
- 既有三项 TanStack Table / React Compiler 警告保留；未新增基础设施或升级框架大版本。资金、收付款、退款及 AI 业务工具不在本轮范围。
