# 库存 v0.6 施工规范

状态：用户于 2026-09-05 回复“OK, 请继续”，进入 I0–I5 施工；验收状态单独记录。

基线：2026-09-05，Catalog 分支 `catalog/v0.5`，代码提交 `b049055`，数据库 revision `0004_product_embeddings`。这些是编写时的本地基线，不代表 Catalog PR 已合并。正式施工从评审后的 Catalog 基线建立 `inventory/v0.6` 分支，不重写既有迁移。

权威依据：[项目迁移上下文](architecture/migration-context.md)第 4–7、9、12–18、23–26、40–46、54 节。技术边界沿用 [AGENTS.md](../AGENTS.md) 和既有 ADR。原规范的硬约束直接继承；原文未确定的业务选择集中列于 [ADR 0012](adr/0012-inventory-ledger-and-posting.md)，用户已同意继续，按 D1–D6 建议实施，限制仍须在交付中说明。本文不授权采购、销售、资金或 AI 业务工具施工。

验收逐项见[库存 v0.6 验收清单](inventory-v0.6-acceptance.md)。发生真实冲突时，记录冲突及影响，不静默改变原语义。

## 1. 目标与范围

第一条完整流程：选择商品、仓库和单位 → 保存期初单 → 过账 → 查看库存数量、成本和流水 → 在允许条件下冲销。后续增量加入调整、调拨与盘点。

| 本阶段交付 | 边界 |
|---|---|
| InventoryEngine | 入库、出库、占用、释放、调拨、调整、冲销、查询；入出库和占用先作为内部业务能力及领域测试入口 |
| 库存事实与投影 | 不可变流水、库存余额、占用明细投影、对账及受控重建 |
| 期初库存 | 按商品/仓库录入数量及成本，显式过账 |
| 库存调整 | 盘盈、盘亏、损耗等数量调整，原因必填；不提供任意覆盖库存余额的入口 |
| 仓库调拨 | 同组织内两个仓库即时调拨，两端原子提交；不支持在途、分批签收 |
| 盘点 | 指定商品/仓库范围，保存基准、录入实盘、复核差异、过账及冲销 |
| 库存工作台 | 数量、占用、可用量、成本权限、流水来源、单据列表和低库存候选 |
| 测试及交付 | 领域性质、真实数据库、并发、RLS、API、前端、迁移、契约和 CI |

本阶段不实现采购订单/收货/采购退货、销售订单/出库单/销售退货、应收应付、收付款、AI 开单或经营问答。成本金额仅是库存估值，不生成会计凭证或资金余额。不新增批次、序列号、货位、保质期、预占超卖、负库存、FIFO、多币种、完整导入平台、审批流或新基础设施。

低库存只提供确定性候选。当前没有销量、在途采购和实际供应商履约数据，不能虚构日均销量、可售天数或推荐采购数量。本地 embedding 继续服务商品搜索，不参与库存数量或成本计算。

## 2. 必须保持的业务不变量

1. 所有业务数据属于认证 RuntimeContext 中的 organization；客户端不能选择租户。
2. 所有库存数量变化都形成 InventoryMovement。流水禁止 UPDATE、DELETE；余额是可重建投影，只有 InventoryEngine 可以修改。
3. `available_qty = on_hand_qty - reserved_qty`；数据库保护 `on_hand_qty >= 0`、`reserved_qty >= 0`、`reserved_qty <= on_hand_qty`。
4. 销售订单确认将来只增加占用，不改变现有量；采购订单将来不增加库存。本阶段不能用测试用内部方法冒充这些业务单据已经实现。
5. 出库使用当前移动加权平均成本；普通出库不重新计算平均成本。库存数量归零时库存金额必须归零。
6. 数量变化、投影、来源单据、Audit、Outbox、幂等结果处于一个数据库事务，任一步失败全部回滚。过账不交给 Celery。
7. 实际变更库存的单据采用 `DRAFT → POSTED → REVERSED`。没有 `POSTED → DRAFT`，也不能修改或删除已过账内容；修正新增冲销事实。
8. 多库存键以 `(warehouse_id, product_id)` 统一排序加锁。锁内读取、计算、校验，不以页面上的可用量作为写入依据。
9. 金额、单价、成本、数量、比例使用 Decimal/NUMERIC；API 传十进制字符串。单位换算保留历史快照，不能按最新换算重算旧单。

## 3. 沿用现有架构的实现位置

下面是计划新增的位置，不是当前已存在的实现，也不改变 monorepo 顶层结构：

```text
apps/api/src/forge_erp/modules/inventory/
  domain/          # 数值、快照、成本规则、状态转换；不依赖 FastAPI
  application/     # 显式 Commands、InventoryEngine、权限及事务内编排
  infrastructure/  # SQLAlchemy 2 async 持久化、锁、流水与投影存取
  api/             # Pydantic v2 请求/响应、路由适配
apps/api/migrations/versions/  # 在 0004 之后追加迁移
apps/api/tests/                # 库存领域/数据库/并发/租户/迁移测试
apps/api/scripts/              # 可选开发样例、对账及受控重建入口
apps/web/features/inventory/   # 工作台、单据、盘点与流水组件
apps/web/app/inventory/        # 接入现有 ERP shell 的库存页面
apps/web/e2e/                  # 库存关键流程
docs/api/openapi.json          # 由 FastAPI 生成
apps/web/generated/api/schema.d.ts  # 由 OpenAPI 生成
```

继续使用现有 `authenticated_transaction`、`RuntimeContext`、`execute_once` 和 Audit/Outbox 基础能力。Command 接收已有事务中的 AsyncSession，InventoryEngine 不自行提交、不创建独立事务。Router 不写领域逻辑；其他业务模块不能绕过引擎写库存表。

现有 Catalog 使用 SQLAlchemy async + 参数化 SQL，库存沿用这一方式，不借机整体改造 ORM 或引入通用工作流框架。Catalog 的每组织资料写锁不复制为库存总锁；库存采用按库存键排序的细粒度锁。

现有 ProductPicker 是独立选品预览组件。施工时提取可复用选择/确认接口，将服务端验证后的商品、单位和数量交给单据编辑器；不复制一套搜索逻辑，不让客户端提交的换算因子成为权威值。

## 4. 数据模型草案

所有租户表采用 UUID 标识、`organization_id`、复合租户外键、ENABLE + FORCE RLS；索引从租户和访问路径出发。产品及仓库继续引用 Catalog，不复制主数据事实。

| 表/实体 | 关键字段与约束 |
|---|---|
| InventoryBalance | 唯一 `(organization_id, warehouse_id, product_id)`；on_hand_qty、reserved_qty、inventory_value、avg_unit_cost、version；available_qty 派生而非独立可写字段 |
| InventoryMovement | 库存键、单键递增 sequence、kind、base_qty 增减、reserved_qty_delta、value_delta、单位快照、来源单据/行/操作标识、reservation_id（可空）、原流水引用、过账时间、操作者及 request_id；唯一库存键+sequence，唯一来源行+操作+调拨方向 |
| InventoryReservation | 占用明细投影；库存键、来源类型/ID/行ID、已占用、已消费、已释放、剩余量、version；来源唯一性与租户外键；不建立销售订单表 |
| InventoryDocument | type=OPENING/ADJUSTMENT/TRANSFER/STOCKTAKE，单据号、status、version、原因、仓库引用、创建/过账/冲销元数据、原单与冲销记录关联；单据号组织内唯一 |
| InventoryDocumentLine | 行号、商品、输入单位/数量、换算快照、基准数量、成本输入及已过账成本结果；盘点行另存基准版本、基准量和实盘量；一张单同一库存键不允许重复行 |

字段名称及迁移文件拆分在施工中按此语义确定；不得用自由 JSON 替代数量、成本、外键和状态的数据库约束。必要的显示快照可保存商品编码/名称、单位名称，保证后续重命名不使历史单据失去可读性。

为覆盖“所有库存变化产生流水”，建议占用也写入同一不可变流水：占用/释放行 `base_qty=0`、`value_delta=0`、`reserved_qty_delta` 非零；按占用出库一行同时记录现有量减少和占用减少。没有任何数量/占用变化的动作只记录单据/Audit/Outbox，不制造虚假数量流水。该选择属于 ADR 0012 的 D2。

除原规范的现有量对账外，验收增加：

```text
balance.on_hand_qty  == SUM(movement.base_qty)
balance.reserved_qty == SUM(movement.reserved_qty_delta)
balance.inventory_value == SUM(movement.value_delta)
balance.reserved_qty == SUM(reservation.remaining_qty)
```

成本流水还保存本次使用的成本及计算前后平均成本快照，用于按 sequence 重放验证。sequence 在持有库存键锁时分配；不能用时间戳或 UUID 顺序替代确定的过账顺序。过账时间由服务器记录为 UTC；用户填写的业务日期不改变成本计算顺序，不允许回溯插入历史成本序列。冲销原流水最多一次，由唯一约束保护。

流水表只给 forge_app SELECT/INSERT，撤销 UPDATE/DELETE/TRUNCATE，并增加禁止修改/删除触发器。已过账单据禁止追加、修改或删除行，业务内容用数据库保护；头部只允许受限的 POSTED→REVERSED 转换及相应版本/冲销元数据更新，不能因“不可变”而阻断合法冲销。

“只有 InventoryEngine 写余额”通过模块边界、代码检查及集成测试约束；同一个 forge_app 连接不能识别调用它的 Python 模块。RLS 负责租户隔离，不声称能阻止持有应用数据库凭据的人任意执行同租户 SQL。若需要数据库级唯一写入口，另行评审受限函数和权限模型，不默认新增特权桥。

## 5. 数量、成本与单位

原规范固定 Quantity/Price 为 `NUMERIC(20,6)`，金额为 `NUMERIC(20,4)`。建议库存单位成本也为 `NUMERIC(20,6)`，内部 Decimal 精度至少 50；量化采用 ROUND_HALF_UP，详见待评审 D1。

- 输入拒绝 float、NaN、Infinity、越界及无法表示的数量；正向数量输入必须大于零，差异方向由业务操作表达。盘点实盘允许零。
- 复用 `ConversionSnapshot`：product_id、unit_id、qty、unit_to_base_factor、base_qty、conversion_version。草稿保存时由服务端捕获；修改数量/单位重新捕获。过账发现换算版本变化则返回冲突，展示新旧差异供用户重新确认，不能偷偷改量。
- 过账后一直使用已存快照；冲销使用原快照，即使商品/单位已停用也不要求重算或重新选择。
- 成本输入按商品基本单位表达，页面清楚显示“元/个”等标签，不能把箱价误当基本单位价。ProductPrice 是销售价格事实，不得自动当采购成本或期初成本。
- 普通入库：`incoming_value = round(base_qty * input_unit_cost, 4)`；`new_value = old_value + incoming_value`；`new_avg_cost = round(new_value / new_qty, 6)`。
- 普通部分出库：`outgoing_value = round(base_qty * current_avg_cost, 4)`，数量/金额减少，avg_unit_cost 不变。舍入导致 outgoing_value 超过剩余金额时，建议返回 `COST_PRECISION_CONFLICT`，不悄悄改变成本公式或写负金额。
- 全量出库：扣除完整剩余 inventory_value，记录与按平均成本计算结果的尾差，确保余额金额恰好为零；建议归零后的 avg_unit_cost 保留最后值，下一次入库按新数量/金额计算。
- 调拨的目标入库金额直接使用来源实际扣除金额，不按目标价或再次乘算；源仓按出库规则，目标仓按收到的实际金额计算新平均成本。
- 非负库存金额、金额溢出和舍入边界均需显式校验；不能通过数据库隐式舍入修正应用错误。纯金额重估不在 v0.6 范围。

标准成本样例：入库 100 个、单位成本 10；再入库 100 个、单位成本 12；结果 200 个、金额 2200、平均成本 11。出库 50 个，成本 550；剩余 150 个、金额 1650、平均成本仍为 11。

## 6. 单据与操作规则

### 6.1 期初库存

建议每个商品/仓库在首次业务前录入一次有效期初。过账要求该库存键没有未冲销的业务流水且现有量/占用/金额为零；允许仅有已冲销期初及其冲销的历史。普通业务发生后不能用新期初覆盖已有库存。

期初数量必须大于零，成本必须显式输入且非负；零数量表示没有期初行。允许显式零成本，但界面提示它会影响估值，不把空值当零。并发期初在库存键锁下校验，最多一个成功。期初不生成应付或付款。

### 6.2 库存调整

采用增加/减少数量及原因。减少只允许扣减可用量，按当前成本出库；增加必须显式提供基本单位成本，采用入库成本规则。禁止直接输入调整后的库存余额来绕过流水，禁止修改已有流水补账。

POSTED 单据不能编辑；修改草稿需要 expected_version。v0.6 不提供单据物理删除；不引入额外已过账状态。作废草稿如将来需要，独立评审，不混入冲销。

### 6.3 调拨

一张调拨单限定同组织的一对不同仓库，可含多个商品；源仓可用量足够，目标仓/商品可用于新业务。占用数量不自动跨仓迁移。每行生成同组的出、入两条流水，数量相反、金额相反；单据及两仓更新一个事务提交。任意一行失败整单回滚。

### 6.4 盘点

采用待评审的乐观盘点方案 D4：草稿选定仓库与商品，保存每行基准数量、余额 version 和单位快照；未输入实盘量不能当作零，不在范围内的商品不受影响。实盘差异 `counted_base_qty - baseline_on_hand_qty` 由服务端计算。

过账时对全部相关库存键加锁。如果任一基准 version 已变化（包括占用变化），整单返回 `STOCKTAKE_STALE`，显示冲突行，要求刷新基准并重新确认/复盘；不得将旧差异自动套在新库存上。刷新基准是显式草稿命令并产生 Audit，不自动覆盖用户已输入的实盘数据。

盘盈成本默认建议当前平均成本，提交前必须显式确认；零库存或没有可用历史成本时必须输入成本。盘亏采用当前成本并保护已占用库存；实盘低于 reserved_qty 时拒绝过账，不能自动释放别的业务的占用。零差异行不写数量流水，整张零差异盘点仍可过账并留审计。

### 6.5 占用基础能力

仅实现受 RuntimeContext、事务和来源约束的内部命令，不开放手工占用/释放的通用 HTTP 入口。来源使用同组织已验证对象或未来业务适配器，不能相信任意客户端 source_type/source_id。

`reserve_stock(q)` 要求可用量足够；`release_reservation(q)` 不超过剩余占用；`issue_stock(q, reservation)` 同时消费对应占用与现有量，不得使用其他单据的占用。无占用的调整/调拨只能减少可用量。占用不改变金额和平均成本。

测试用来源仅存在隔离测试夹具，不混入生产来源枚举或种子。销售确认、撤销、部分履约的接口和单据状态留给销售阶段；本阶段先验证对应引擎不变量。

### 6.6 冲销

原规范确定以新增相反流水纠错，但未规定后续成本变化的处理。建议 v0.6 使用 D3 的保守规则：只允许整单冲销，且原单必须仍是所有涉及库存键的最后一次流水操作（零差异行不参与此判断）。有后续入出库、调拨或占用流水时拒绝 `REVERSAL_DEPENDENCY_CONFLICT`，不重放整段历史改写成本。

符合条件时创建独立冲销记录及反向流水，恢复原操作前数量、金额、平均成本及受影响占用投影；验证所有库存约束后将原单标记 REVERSED。原单业务字段和原流水保持不变。同一原单最多成功冲销一次；重复同幂等键返回原结果，不同键再次请求返回明确状态冲突。

调拨必须两端同时满足条件并整单冲销。冲销被拒绝后，不能建议用任意数量调整冒充精确成本回退。这里的“最后一次”是实际流水顺序：后续单据即使已冲销，其流水仍存在，因此本方案也不承诺逐笔撤销即可恢复更早单据的可冲销资格。需要回退历史依赖时，另行评审更完整的补偿/成本规则。历史冲销、部分冲销和级联冲销不在默认范围。

## 7. 事务、并发与幂等

所有写入口先认证、验证当前权限，再进入幂等重放判断；幂等成功不能绕过已撤销的权限。事务采用现有 authenticated_transaction，按以下统一顺序执行：

1. 从 RuntimeContext 设置事务内租户；参数校验，调用 execute_once 获取操作幂等锁。operation 必须包括资源 ID 及动作，避免同 key 被不同单据错误复用。
2. 锁定并校验相关单据/版本；多单据按 ID 排序。按固定表顺序和 ID 对需要的新业务主数据引用加共享行锁，确认商品、仓库、单位和换算可用。与 Catalog 停用/变更遵循兼容锁序，补充交叉并发测试。
3. 收集完整库存键集合，按 warehouse_id、product_id 排序，依次获取事务级库存键锁。锁名称包含组织；没有余额行也受同一锁保护。然后按相同顺序 INSERT ON CONFLICT DO NOTHING 创建零投影，再 SELECT FOR UPDATE。
4. 按固定顺序锁相关占用投影，检查可用量、单据状态、盘点基准及成本，计算完整结果。
5. 按序追加流水、更新余额/占用投影、更新来源单据，再写 Audit、Outbox 和幂等结果，最后由外层统一 COMMIT。

不能先逐行处理到一半才发现还需反向拿另一库存键锁。单据每个库存键最多一行；调拨方向有独立流水标识。多个不同幂等键同时过账同一单据，也必须由单据锁/状态和流水唯一约束保护只成功一次。

现有幂等记录有到期清理，因此业务唯一约束必须在幂等记录过期后仍防止同一单据重复过账。不对成功响应含混的请求自动生成新 key 重试；界面保留原 key 查询/重试原操作。锁超时及死锁返回可识别冲突并整体回滚；任何有限内部重试都需重开整个事务并保留原 key。

## 8. 权限、API 与界面

建议权限复用原规范的 `inventory.read`、`inventory.adjust`、`product.cost.read`，新增 `inventory.opening`、`inventory.transfer`、`inventory.stocktake`、`inventory.reverse`、`inventory.reconcile`，注册在现有 Permission/RolePermission 体系中。新增权限不默认授予所有已有角色；开发 ADMIN seed 幂等补充。

- 数量与流水查询需要 inventory.read；任何成本/估值字段额外要求 product.cost.read。
- 草稿编辑和过账按对应业务权限检查；需要输入或确认成本的操作另需 product.cost.read。冲销需原操作权限 + inventory.reverse + product.cost.read。
- API 无成本权限时省略/脱敏成本、金额、成本快照及尾差，不能仅在前端隐藏。错误 context、Audit 查询、日志、Outbox payload 和幂等响应也不能成为泄漏通道。
- 建议写操作只缓存/返回单据 ID、状态、版本及 request_id 等无成本回执，详情另按当前权限读取，避免权限撤销后重放旧成本响应。
- 组织字段不在输入 schema 中；跨租户资源统一 NOT_FOUND，鉴权失败返回 PERMISSION_DENIED，不泄露其他租户资料。

以下是拟定的同源 API 契约，正式代码仍由 OpenAPI 生成 TypeScript：

| 方法与路径（统一 `/api/v1` 前缀） | 用途 |
|---|---|
| GET `/inventory/balances`、`/inventory/movements` | 分页查询，按商品、仓库、单据来源筛选 |
| GET `/inventory/low-stock` | 低库存候选，不返回虚构建议采购量 |
| GET `/inventory/documents`、`/inventory/documents/{id}` | 列表及带行明细的单据详情 |
| POST `/inventory/openings`、`/inventory/adjustments`、`/inventory/transfers`、`/inventory/stocktakes` | 分类型创建草稿，各自有明确请求模型和 Command |
| PUT `/inventory/documents/{id}/draft` | 仅编辑草稿，expected_version 必填；不能改变类型或提交 status |
| POST `/inventory/documents/{id}/post`、`/inventory/documents/{id}/reverse` | 显式状态转换，expected_version + Idempotency-Key |
| POST `/inventory/stocktakes/{id}/refresh-baseline` | 显式刷新草稿盘点基准，重新复核差异 |

所有副作用入口都要求 Idempotency-Key；现有 Origin、HttpOnly session、request ID、Problem Details 继续适用。不暴露 `/balances/{id}` 的 PATCH/PUT、不开放流水 UPDATE/DELETE、不用通用 CRUD 修改 status。

新增领域错误至少包括 INSUFFICIENT_STOCK、INSUFFICIENT_RESERVATION、INVALID_DOCUMENT_STATE、DOCUMENT_VERSION_CONFLICT、OPENING_NOT_ALLOWED、STOCKTAKE_STALE、REVERSAL_DEPENDENCY_CONFLICT、UNIT_CONVERSION_CHANGED、COST_PRECISION_CONFLICT。资源/单位校验复用适用的既有错误；响应包含 request_id 和可操作说明，不能回显敏感成本。

库存页面接入现有 shell，提供库存总览、流水、期初/调整/调拨/盘点列表与单据详情。保留 ProductPicker 键盘操作；数量/成本由服务端权威计算，前端如需正式金额预览使用 decimal.js。过账前展示本次变动、单位和目标仓库；超时提示状态待确认，支持使用原 key 重试。冲销原因必填，已过账内容只读。手机宽度可浏览和完成核心单据操作。

列表限制每页最多 100 条并提供稳定排序；流水采用 sequence/ID 游标及确定的查询截止点，防止新增流水导致翻页重复漏读。建议单据最多 200 行，超限拒绝，不隐式分批提交；这是 D6 的初始限额而非性能保证。

## 9. 主数据、低库存、对账与后台

Catalog 联动属于必要适配：商品/仓库在有现有量或占用时不能停用；历史引用保留，不物理删除。新业务过账重查主数据，历史冲销可以使用原快照。锁定关联主数据与库存更新需要覆盖“停用与过账同时发生”的测试，不能只做先查后写。基本单位仍不可变。

当前 `products.min_stock_qty` 是商品级字段，没有仓库维度。D5 建议本阶段按组织汇总该商品全部仓库的 available_qty，与 min_stock_qty 比较；阈值为零表示关闭提醒，低于或等于正阈值进入候选。没有余额行的已启用商品按零计算；不能把同一商品阈值误套给每个仓库。仓库筛选只用于明细展示，不悄悄改变提醒口径。reorder_qty 暂作已有资料展示，不宣称是动态算法计算结果。

对账先提供只读报告：流水数量/金额/占用汇总、占用明细、余额、平均成本重放结果的差异，记录组织、键范围、时间及序列水位。在线单次报告采用一致快照，不能混用多个时点的数据。

重建仅提供维护命令，经 inventory.reconcile 授权，默认 dry-run；实际修复必须显式指定组织和范围，运行在停写维护窗口或使用与正常写入完全一致的锁。只能通过 InventoryEngine 从不可变事实恢复投影，保留修复前后差异及 Audit/Outbox，不能重写流水或来源单据。修复投影不代表新增业务数量，因此不伪造出入库流水。此命令不能作为管理员随意改库存的后门。

Outbox 继续使用现有 Celery/Redis。库存事件注册明确处理器，不能被未知事件无限积压或未经处理直接标记成功；采用 event ID 去重及可重试失败策略。v0.6 仅做必要的内部消费/日志，低库存页面可同步查询，不新增外部通知服务。Redis/消费者暂停不能破坏已提交库存事实，恢复后能继续消费。

## 10. 增量施工与每步验证

| 增量 | 交付与依赖 | 必须执行的相关验证 |
|---|---|---|
| I0 规范收口 | 评审 ADR 0012，记录 Catalog 评审基线；明确哪些候选规则被采纳 | 文档引用/验收覆盖；不计作库存功能通过 |
| I1 账本与引擎 | 数值规则、追加迁移、流水/余额/占用、锁、内部 Commands、对账 | 领域样例和 Hypothesis、真实 PostgreSQL、直接 SQL RLS/不可变性、首次建余额与并发出库、事务故障回滚 |
| I2 期初与调整 | 草稿/过账/冲销、Audit/Outbox/幂等，首条页面流程 | API 状态与权限、单位快照、成本脱敏、重复提交、冲销恢复、前端表单与 E2E；更新生成契约 |
| I3 调拨 | 两仓原子流水与成本转移 | 双向多商品并发、对端失败回滚、数量/金额守恒、占用保护、整单冲销及页面流程 |
| I4 盘点 | 基准、实盘、差异、刷新、过账 | 并发库存变化拒绝旧基准、盘盈成本、盘亏占用保护、零差异、刷新确认与 E2E |
| I5 工作台与收尾 | 查询/低库存、受控重建、开发样例、文档/CI/交付报告 | 大列表查询计划及测试数据耗时记录、损坏投影后重建、全量回归、迁移、契约、生产构建、GitHub CI |

每步发现问题就在该步修复并补回归测试；测试跳过不算通过。I1 可用内部能力测试标准入库/出库样例，不提前实现采购/销售页面。新迁移只追加；本机当前 revision 为 0004，新增编号在施工时按实际 HEAD 分配。

迁移验证至少覆盖：空数据库到 head、Bootstrap 0001 到 head、带 Catalog 0004 真实样例数据到 head、已有库存前一增量到下一增量。验证资料数量、单位/价格关系、身份权限、RLS 及已有流水均保留；不能靠重建开发数据库证明升级成功。

现有 CI push 只匹配 main、bootstrap/**、catalog/**。开始代码施工时需追加 inventory/**，沿用 PR 检查，不更换现有工具链。新增依赖仅限规范需要的测试/前端数值库，使用 uv/pnpm 更新锁文件，不手工编辑。

## 11. 验收命令与交付要求

以下命令已存在，用于未来库存实现后的验证；本次文档工作不声称它们证明了库存功能：

```sh
make install             # uv sync --locked / pnpm frozen lockfile
make infra migrate seed
make lint test           # Ruff / format / Pyright / frontend checks / pytest / Vitest
make contract
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm test:e2e
uv run --project apps/api alembic -c apps/api/alembic.ini current
```

Hypothesis、库存迁移/重建与故障注入测试由施工增量加入正常测试入口；新增 seed-inventory / reconcile 命令的实际名称、参数及风险边界完成后再写入运行说明，当前不提供会误导用户的未实现运行命令。

验收报告必须提供：implementation summary、最终仓库树、运行命令、迁移状态、逐项测试证据与 CI 链接、未解决问题、需要评审的架构决定。只有[验收清单](inventory-v0.6-acceptance.md)全部满足，才建议标记 `inventory-v0.6` 并规划采购阶段；不能因为文档完成或历史 CI 通过就宣布库存完成。
