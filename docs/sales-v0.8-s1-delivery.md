# Sales v0.8 — S1 交付记录

本增量实现销售订单后端、服务端价格快照和库存占用；基于已发布purchasing-v0.7/main64bec0a，在sales/v0.8施工。草稿PR #4继续保留，未合并、未发布销售版本。

## Implementation summary

- 新增sales模块：订单列表/详情、保存/编辑草稿、确认、取消、关闭和报价接口；状态、版本、RBAC、幂等与业务校验在Command内。
- 草稿不影响库存；确认全量占用，有一行不足则全部回滚；取消/关闭通过InventoryEngine释放。客户/商品/仓库/单位后来停用不阻止历史义务释放。
- AUTO在保存时解析，MANUAL明确输入；确认冻结已保存价格。价格优先级为专属、有效成交历史、客户等级、标准；目录基础单位语义不变。Decimal精度50、最终单价6位/金额4位。
- 内部占用凭据及独立库存行通过销售附属表关联订单行，普通库存/采购接口及引擎能力拒绝绕过。余额与占用投影仍由InventoryEngine独占维护。
- 新表FORCE RLS和租户复合外键；已确认订单及占用来源受SQL不可变保护。无成本销售员可保存/确认；无价格人员读数量并释放已有占用。回执只有身份、状态、版本和request ID。
- Audit/Outbox/幂等与占用一次提交；注册sales.order事件消费者；HTTP成功前COMMIT。OpenAPI与TypeScript已自动生成；库存页面显示“销售库存占用”且不开放通用修改操作。

## Migration status

新增0009_sales，追加到0008_purchasing，旧迁移未修改。开发库已升级；空库及六个历史基线的升级测试均通过，0008用真实结构的历史采购/库存/价格/角色fixture验证数据保留。

四张新表：sales_orders、sales_order_lines、sales_documents、sales_document_lines。S1实际创建的sales_documents仅为内部RESERVATION；SHIPMENT/RETURN只预留结构，没有公开创建/过账命令。S2将追加跨凭据占用来源字段及对应约束，不改写已发布迁移。

## Run commands

在仓库根目录使用已有环境配置：

```sh
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate
make api
# 另一终端
make web
# 开发账号/角色需要同步新权限时
make seed
# 检查
uv run --project apps/api pytest apps/api/tests/test_sales_orders.py apps/api/tests/test_sales_pricing.py apps/api/tests/test_sales_safety.py apps/api/tests/test_api_commit_boundary.py -q
make lint test contract
pnpm build
```

API位于/api/v1/sales/orders与/api/v1/sales/price-quote，浏览器继续同源/api。写操作需要现有Cookie会话、Origin和Idempotency-Key。开发配置/密码保持本地，不写入交付记录。销售工作台尚未开放，本增量主要通过API与自动测试验收。

## Test results

- 完整后端：164 passed、0 skipped（已发布基线132项保持；含新增销售、提交边界及0008迁移覆盖）。在独立临时数据库运行并清理，forge_app负责业务与RLS测试。
- 销售相关最终定向测试由完整回归覆盖；此前34项定向测试通过，之后新增独立库存键并发测试及100件验收样例由完整164项验证。
- 故障注入涵盖库存流水、余额、库存单据、订单、Audit、Outbox、幂等和COMMIT；全部回滚后可原键重试。
- Ruff、format、Pyright通过；前端lint/typecheck、12项Vitest及生产build通过。最新提交CI在PR #4记录，发布前须核对实际run。
- OpenAPI→TypeScript已再生成；CI执行冻结安装、接口漂移、全后端、前端build及既有Playwright回归。

## Unresolved issues / scope limits

S2分批出库与跨凭据占用消费、S3退货/冲销/毛利、S4销售工作台和S5全阶段验收尚未实施。成交历史查询的优先级测试使用隔离fixture，不能代替真实出库业务验收。无销售seed、应收/收款/退款或AI工具。本增量不调整0.7.0已发布版本号，也不建议sales-v0.8标签。

首次在开发库验证时，测试清理与运行中的Outbox消费者发生锁竞争；最终回归已迁到独立数据库，未将失败或清理错误计为通过。报价权限首次接入误用了不存在的product.read，已修正为既有catalog.read并由报价API回归覆盖。

现有3项TanStack Table/React Compiler lint警告保留；无新增lint错误。当前测试规模不代表生产容量认证。

## Architecture decisions requiring review

按用户继续指令实施ADR0014的S1范围与定价/权限原则；尚未发现需要改变原权威上下文的冲突。后续重点保持：退货不重开待发、按原出库实际成本回收入库、闭单后拒绝旧出库冲销、成本权限与普通销售操作分开。完整业务与UI验收前不要合并或发布销售版本。

完整仓库目录见[sales-v0.8-s1-tree.txt](sales-v0.8-s1-tree.txt)，46项阶段验收见[清单](sales-v0.8-acceptance.md)。
