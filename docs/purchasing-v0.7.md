# 采购 v0.7 施工规范

状态：2026-09-05 用户授权明确规范后直接分步实现。基线 `inventory/v0.6@1105bfa`，数据库 `0007_inventory_projection`；Catalog/库存 PR 仍独立待评审，本阶段不自动合并。实施选择见 [ADR0013](adr/0013-purchasing-receipts-and-returns.md)，逐项验证见[验收清单](purchasing-v0.7-acceptance.md)。

## 1. 目标与范围

完成供应商→采购订单→确认→一次/分批收货→库存与成本→采购退货→历史采购价格闭环。订单本身不增加库存；只有收货POST增加现有量、不可变流水和移动平均成本。退货POST减少可用库存，保护已有占用。

保持当前Python3.14/FastAPI/Pydanticv2/SQLAlchemy2/Alembic/psycopg3/PostgreSQL18+pgvector/Redis/Celery/uv和NextAppRouter/React/TS/shadcn BaseUI/Tailwind/TanStackQuery/Table/RHF/Zod/pnpm，不升级大版本或新增基础设施。领域计算在服务端Decimal；浏览器展示服务器结果，若未来添加正式前端金额计算必须用decimal.js。

不做销售、应收应付、付款、税务/发票、运费分摊、折扣、多币种、审批流、采购询比价、自动补货、AI业务工具。采购页的参考金额不能被称为待付款金额；结算状态不以UNPAID占位猜测事实。

## 2. 订单生命周期

| Command | 前置 | 结果 |
|---|---|---|
| Save/UpdateDraft | DRAFT，版本正确；有效供应商、仓库、商品及单位 | 保存商业/换算快照，库存不变；编辑不能提交status字段 |
| Confirm | DRAFT；确认时资料与换算版本仍有效 | CONFIRMED，商业内容不可改；数量和成本不变 |
| Cancel | DRAFT/CONFIRMED且有效收货为零，必填原因 | CANCELLED；已有待收货草稿可查询但不能过账 |
| Close | CONFIRMED，必填原因 | CLOSED；停止剩余收货；不将PARTIAL伪改为RECEIVED |

不自动关闭全量收货订单，不提供CLOSED/CANCELLED重开。订单确认后如改变采购条款，关闭剩余并另建订单。确认后禁止SQL修改商业内容/增删行。

按订单行有效收货量（POSTED收货减收货冲销，不减采购退货）计算收货状态：全零UNRECEIVED；全部行达到原订数量RECEIVED；其余PARTIAL。退货量单独显示，退货不自动恢复待收额度。所有数量以base_qty比较；禁止累计超收。收货状态与订单状态分离；未来结算状态也独立。

## 3. 收货和退货

收货从单一CONFIRMED订单创建。每行关联原订单行，单位、产品、因子、供应商和仓库继承订单快照；输入此次实收数量，单价默认订单价，可明确修改。草稿不占待收额度，POST时在订单锁下重新检查剩余量与权限。新业务要求主数据有效；确认后单位因子改变不追溯改写订单，新收货仍按确认快照核算。

收货/退货状态统一DRAFT/POSTED/REVERSED。草稿可编辑，过账后内容与行不可改删；修改错误使用冲销。全部行一个数据库事务提交，任何一行失败不产生部分收货。

退货必须关联原POSTED收货行，只能原仓原单位退回原供应商。累计有效退货不超过原实收；并发退货由原订单及原收货锁协调。不能动用已占用库存，也不跨仓寻找库存。退货参考金额按原收货价，库存金额按退货时当前移动平均成本，分别展示及审计，不生成退款或损益。

冲销沿用库存ADR0012：任何受影响库存键出现后续流水即拒绝整单冲销，即使后续单已经冲销。收货仍有有效关联退货时也拒绝收货冲销。冲销增加反向事实、保留原记录；订单实收和退货额度在同事务更新/重算。已关闭订单不自动重开。

## 4. 数量与计价

输入数量和单价均Decimal字符串、最多6位小数；金额4位，ROUND_HALF_UP，内部精度50。保存原数量、单位、因子、base_qty、换算版本及名称快照。

例：1箱=1000个，买2箱，每箱1250元 → 2000个、金额2500元。若箱价除以1000无法用6位成本精确表达，也不能先舍入单位成本而改变总入库价值。向InventoryEngine receive传入base_qty与准确行金额，均价由引擎计算。

例：库存先100@10再100@12，当前均价11；向第一笔收货退50，参考退货金额500、库存价值减少550。两者差额留作业务事实展示，非已实现的会计核算。

零成本允许但需明确0；空值不是零。单据合计为行金额相加，不对原始总数量重复计算。末次退完原行参考金额精确清零；普通部分退货不允许金额越界。

## 5. 数据与应用落点

模块 `modules/purchasing/{domain,application,api}`；页面 `features/purchasing/`，现有 `/purchase` 导航接入；DTO走OpenAPI→TS生成。

新表：purchase_orders、purchase_order_lines、purchase_documents（库存单据采购附属信息）、purchase_document_lines（库存行与订单行/原收货行关系及价格金额）。所有表organization_id + FORCE RLS + 复合外键。订单行实收/已退为从有效单据聚合的读模型，避免额外可失配计数；状态查询用同一口径。

扩展库存来源为PURCHASE_RECEIPT/PURCHASE_RETURN，保留库存流水→库存行的强外键与不可变触发器。采购Command写业务单据，只有InventoryEngine改库存余额；不得开放generic inventory POST绕过采购额度、状态或权限。库存来源详情应能显示采购类型并跳转采购页，不出现未识别类型500。

## 6. 事务与安全

RuntimeContext从会话建立；每个请求服务端验证当前权限，先权限后幂等回执。动作按订单write/confirm/cancel/close、receipt、return、reverse分别授权；数量查看purchase.read，价格与成本另需product.cost.read。敏感写动作同时需价格权限。无价格权限的列表、详情、历史价格、错误、回执不能泄露金额。

锁序：幂等锁→订单行头锁（统一订单ID）→相关库存单据头锁（统一ID）→供应商/仓库/商品/单位共享锁→InventoryEngine既有库存键锁（warehouse,product）→写流水/投影/单据/Audit/Outbox/幂等→一次COMMIT。不要在持有库存键锁后反向获取采购订单锁；与Catalog停用及Inventory盘点/调拨交叉测试。操作超时/死锁返回可重试冲突，不能悄悄分批提交。

Audit保存actor/source/request ID/资源及实际前后值；Outbox只保存必要元数据。Redis/worker停止不影响核心过账；恢复后事件可重试但不重复库存写入。新migration只追加，空库和带真实库存数据0007都需升级测试。

## 7. API与页面

REST前缀 `/api/v1/purchasing`：orders列表/详情、创建/修改草稿、confirm/cancel/close；receipts和returns创建；documents列表/详情/修改草稿/post/reverse；price-history按供应商/商品过滤、日期+ID稳定排序且分页。所有侧效应要求Idempotency-Key和expected_version（创建除外），API不接受organization_id或通用status PATCH。

采购工作台分订单、收货、退货、历史价。商品通过现有键盘ProductPicker添加；从订单生成部分收货，从收货生成退货；明确显示订购/已收/待收/已退、输入单位与基本单位。提交前确认，不确定响应沿用原键重试，详情忽略较早的异步响应；过账后只读。

## 8. 增量与验收

P0：规范/ADR/分支及权限边界。P1：migration、订单草稿/确认/取消/关闭及领域测试。P2：分批收货+InventoryEngine集成+超收/并发/事务故障。P3：退货/两种金额/冲销/历史价。P4：工作台、真实浏览器、只读角色和移动端。P5：迁移/全量回归/契约漂移/CI/文档/可选dev seed。

每步相关测试通过后提交；不以mock、skipped、旧库存CI代替采购验证。最终报告包括实现、目录、运行命令、migration head、逐项测试证据、未决问题及需复核决策。全部通过后建议`purchasing-v0.7`标签，仍不自动合并依赖PR。
