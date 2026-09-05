# Forge ERP 项目迁移上下文

你现在接手一个正在设计并即将进入施工阶段的 AI 原生 ERP 项目。请将本文视为当前项目的权威上下文，并在后续执行中优先遵守。

---

# 1. 项目目标

项目名称暂定：

**Forge ERP**

目标：

构建一个 **AI Native ERP**，MVP 首先聚焦 **五金行业商贸/批发/门店企业**。

第一版不是通用 ERP，不做大型制造和复杂财务。

MVP 重点解决：

- 商品 SKU 多、规格复杂
- 商品命名混乱
- 单位换算复杂
- 客户价格不统一
- 采购和销售开单慢
- 库存难管理
- 补货判断困难
- 老板无法快速理解经营状况

核心理念：

> AI 负责理解、推理、协调、建议和调用业务能力；ERP Domain Engine 负责库存、金额、权限、状态、成本等确定性规则。

AI 不是数据库，也不是库存/财务计算引擎。

---

# 2. MVP 功能边界

## P0 必须支持

### 基础资料

- 商品
- 商品分类
- 品牌
- 单位
- 商品单位换算
- 客户
- 供应商
- 仓库
- 用户
- 角色与权限

### 销售

- 销售订单
- 销售订单确认
- 销售出库
- 销售退货
- 客户历史价格
- 销售毛利

### 采购

- 采购订单
- 采购订单确认
- 采购入库
- 采购退货
- 供应商历史采购价格

### 库存

- 实时库存
- 可用库存
- 库存占用
- 库存流水
- 调拨
- 盘点
- 库存调整
- 最低库存
- 补货建议
- 库存预警

### 轻量资金

- 应收
- 收款
- 应付
- 付款

### AI

- AI Chat
- 智能商品搜索
- 自然语言开单
- 库存查询
- 补货建议
- AI Daily Brief
- 老板经营问答

---

# 3. MVP 明确不做

第一版不要实现：

- 总账
- 会计凭证
- 资产负债表
- 利润表
- 完整税务
- 复杂发票系统
- BOM
- MRP
- MES
- 生产计划
- 工序
- HR
- 工资
- OA
- 复杂 CRM
- 多法人合并
- 多币种复杂会计
- 电商平台
- 微服务
- Kafka
- Elasticsearch
- MongoDB
- Neo4j
- GraphQL
- Temporal
- Kubernetes
- 多 Agent 网络

不要“顺手”加入这些能力。

---

# 4. 核心业务原则

以下属于项目级硬约束。

## 4.1 多租户

所有业务数据归属：

`organization_id`

规则：

- organization\_id 来自认证后的 Runtime Context
- 不允许客户端随意提交 organization\_id 决定租户
- AI Tool 不允许从 LLM 参数接受 organization\_id
- Application Layer 必须校验租户
- PostgreSQL 使用 RLS 作为第二层防御
- 必须有跨租户自动测试

---

## 4.2 库存事实源

库存采用：

`InventoryMovement + InventoryBalance`

其中：

**InventoryMovement**

是不可变库存事实流水。

**InventoryBalance**

是为了高性能查询维护的实时投影。

规则：

- 所有库存数量变化必须产生 InventoryMovement
- InventoryMovement 禁止 UPDATE
- InventoryMovement 禁止 DELETE
- InventoryBalance 不允许普通业务模块直接修改
- 只有 InventoryEngine 可以修改 InventoryBalance
- InventoryBalance 必须能够与 InventoryMovement 对账重建

---

## 4.3 销售库存逻辑

销售订单确认：

`reserved_qty 增加`

但：

`on_hand_qty 不变`

即：

`available_qty = on_hand_qty - reserved_qty`

销售出库 POST 时：

- on\_hand 减少
- reservation 被消费
- 创建 InventoryMovement
- 记录实际库存成本

---

## 4.4 采购库存逻辑

采购订单：

不增加库存。

采购入库 POST：

- on\_hand 增加
- 创建 InventoryMovement
- 更新移动加权平均成本
- 可生成 Payable

---

## 4.5 默认禁止负库存

MVP：

`allow_negative_stock = false`

数据库层也应保护：

- on\_hand\_qty >= 0
- reserved\_qty >= 0
- reserved\_qty <= on\_hand\_qty

库存不足时返回明确领域错误：

`INSUFFICIENT_STOCK`

---

# 5. 库存成本

MVP 只支持：

**Moving Average / 移动加权平均成本**

公式：

`new_avg_cost = (old_value + incoming_value) / new_qty`

销售出库：

使用当前 avg\_unit\_cost。

出库不会重新计算平均成本，只减少：

- qty
- inventory\_value

最后库存数量归零时：

`inventory_value` 强制归零，避免 Decimal 累积形成幽灵金额。

不要让 LLM 计算库存成本。

---

# 6. 商品模型

五金行业商品必须支持灵活规格属性。

示例：

`304不锈钢外六角螺栓 M8×30`

可能包含：

- 分类：紧固件
- 品牌
- 材质：304
- 直径：M8
- 长度：30mm
- 类型：外六角
- 强度：A2-70
- 表面处理
- 基础单位：个
- 采购单位：箱
- 销售单位：包/箱/个

Product 核心字段：

- sku
- barcode
- name
- short\_name
- category\_id
- brand\_id
- model
- specification
- attributes JSONB
- base\_unit\_id
- default\_purchase\_unit\_id
- default\_sales\_unit\_id
- standard\_price
- retail\_price
- wholesale\_price
- min\_stock\_qty
- reorder\_qty
- preferred\_supplier\_id
- search\_text
- status

attributes 使用 JSONB，例如：
```json
{
  "材质": "304",
  "直径": "M8",
  "长度": "30mm",
  "类型": "外六角",
  "强度": "A2-70"
}
```

---

# 7. 单位换算

一个商品可以：

`1箱 = 20包`

`1包 = 50个`

库存底层统一按 Base Unit。

例如：

`1箱 = 1000个`

订单必须保存历史快照：

- unit\_id
- qty
- unit\_to\_base\_factor
- base\_qty

不能以后根据最新单位换算关系重新计算历史订单。

---

# 8. 客户价格

同一商品可以不同客户不同价格。

建议价格优先级：

1. 客户专属价
2. 客户最近成交价
3. 客户价格等级
4. 商品标准价

AI 可以建议“沿用上次价格”，但最终价格解析由确定性 Pricing Engine 完成。

---

# 9. 单据原则

所有实际改变库存的单据统一状态：

- DRAFT
- POSTED
- REVERSED

规则：

`POSTED → DRAFT`

不存在。

已 POSTED 单据：

- 不允许修改
- 不允许删除
- 修正只能生成 reversal

典型：

原出库：

`-100`

冲销：

`+100`

审计链必须完整。

---

# 10. 销售订单状态

SalesOrder 业务状态：

- DRAFT
- CONFIRMED
- CLOSED
- CANCELLED

履约状态单独保存：

- UNFULFILLED
- PARTIAL
- FULFILLED

结算状态单独保存：

- UNPAID
- PARTIAL
- PAID

不要把所有含义塞进单个 status。

---

# 11. 采购订单状态

PurchaseOrder：

- DRAFT
- CONFIRMED
- CLOSED
- CANCELLED

收货状态：

- UNRECEIVED
- PARTIAL
- RECEIVED

结算状态：

- UNPAID
- PARTIAL
- PAID

---

# 12. InventoryEngine

库存必须集中由 InventoryEngine 管理。

正式接口方向：

- receive\_stock()
- issue\_stock()
- reserve\_stock()
- release\_reservation()
- transfer\_stock()
- adjust\_stock()
- reverse\_movement()
- get\_balance()
- get\_movements()

禁止其他模块执行：

`UPDATE inventory_balances`

---

# 13. 库存并发规则

库存事务必须：

1. SELECT InventoryBalance FOR UPDATE
2. Validate
3. Create Movement
4. Update Balance
5. Update source document
6. Audit
7. Outbox
8. COMMIT

任何一步失败全部 rollback。

多 SKU 锁必须按统一顺序：

`warehouse_id, product_id`

排序加锁，避免 deadlock。

---

# 14. 数值规则

禁止 float 处理：

- Money
- Price
- Quantity
- Rate
- Cost

Python：

`Decimal`

PostgreSQL：

Quantity / Price：

`NUMERIC(20,6)`

金额：

`NUMERIC(20,4)`

最终现金支付可按：

`NUMERIC(20,2)`

API 中 Decimal 建议序列化成 string。

前端正式金额计算使用 decimal.js。

---

# 15. API 设计

API：

`/api/v1`

使用 REST + explicit business command。

例如：

正确：

`POST /sales-orders/{id}/confirm`

错误：

`PATCH /sales-orders/{id} {status:"CONFIRMED"}`

状态转换必须通过明确业务 Command。

---

# 16. Application Command

所有业务写操作必须通过 Command。

例如：

- CreateSalesOrderCommand
- ConfirmSalesOrderCommand
- PostSalesShipmentCommand
- ReverseSalesShipmentCommand
- CreatePurchaseOrderCommand
- PostPurchaseReceiptCommand
- AllocateReceiptCommand

UI、API、AI Tool 都调用同一 Application Command。

禁止：

AI → HTTP localhost → API

如果处于同一 Python application 中，AI Tool 直接调用 Application Layer。

---

# 17. Idempotency

所有 side-effect endpoint / command 必须支持：

`Idempotency-Key`

重复同一 key：

不得产生重复业务单据。

相同 key + 不同 request body：

返回：

`IDEMPOTENCY_KEY_REUSED`

---

# 18. 错误模型

采用 Problem Details 风格。

示例：
```json
{
  "type": "/errors/INSUFFICIENT_STOCK",
  "title": "Insufficient stock",
  "status": 409,
  "code": "INSUFFICIENT_STOCK",
  "detail": "Available stock is lower than requested quantity.",
  "request_id": "...",
  "context": {
    "product_id": "...",
    "requested_qty": "12",
    "available_qty": "10"
  }
}
```

关键领域错误包括：

- INSUFFICIENT\_STOCK
- INSUFFICIENT\_AVAILABLE\_STOCK
- ORDER\_ALREADY\_CONFIRMED
- ORDER\_ALREADY\_CANCELLED
- SHIPMENT\_EXCEEDS\_ORDER
- RECEIPT\_EXCEEDS\_PO
- INVALID\_UNIT\_CONVERSION
- PRODUCT\_INACTIVE
- CUSTOMER\_INACTIVE
- PERMISSION\_DENIED
- DOCUMENT\_ALREADY\_POSTED
- DOCUMENT\_VERSION\_CONFLICT
- PAYMENT\_EXCEEDS\_OUTSTANDING
- IDEMPOTENCY\_KEY\_REUSED

领域冲突通常返回 HTTP 409。

---

# 19. 后端技术栈

已冻结：

- Python 3.14
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- psycopg 3
- PostgreSQL 18
- pgvector
- Redis
- Celery
- uv
- Ruff
- Pyright
- pytest
- Hypothesis
- OpenTelemetry
- Sentry

架构：

**Modular Monolith**

暂不做微服务。

---

# 20. 数据库

主数据库：

PostgreSQL 18

Extension：

- pg\_trgm
- vector

主键：

优先 PostgreSQL：

`uuidv7()`

业务数据 ID 不使用用户可读 ID。

单据另外拥有：

- SO-202609-000001
- PO-202609-000001
- SH-202609-000001
- GR-202609-000001
- SR-...
- PR-...
- TR-...
- ST-...
- RC-...
- PY-...

单号通过 document\_sequences 原子 UPSERT 生成。

禁止：

`SELECT MAX(order_no)`

---

# 21. PostgreSQL RLS

所有 tenant-owned table：

- ENABLE ROW LEVEL SECURITY
- FORCE ROW LEVEL SECURITY
- USING policy
- WITH CHECK policy

transaction 开始时：
```java
SELECT set_config(
  'app.organization_id',
  :organization_id,
  true
)
```

Application DB user 必须：

- NOSUPERUSER
- NOCREATEDB
- NOCREATEROLE

FastAPI 不能使用 postgres superuser。

---

# 22. Authentication

Web MVP 使用：

**Opaque server-side session**

不要浏览器 localStorage JWT。

浏览器：

- Secure Cookie
- HttpOnly
- SameSite=Lax

数据库只保存 session token 的 SHA-256 hash。

密码：

Argon2，通过 pwdlib[argon2]。

Auth API：

- POST /api/v1/auth/login
- POST /api/v1/auth/logout
- GET /api/v1/auth/me

登录输入：

- organization\_code
- email
- password

允许未来同一个 email 存在于多个 organization。

---

# 23. Authorization

Authentication：

“你是谁？”

Authorization：

“你能做什么？”

权限必须由 Backend Enforcement。

前端隐藏按钮不是权限系统。

Prompt 不是权限系统。

AI 继承当前用户权限。

示例 permission：

- catalog.read
- catalog.write
- product.cost.read
- sales\_order.read
- sales\_order.create
- sales\_order.confirm
- sales\_shipment.post
- purchase\_order.create
- purchase\_order.confirm
- purchase\_receipt.post
- inventory.read
- inventory.adjust
- finance.receivable.read
- finance.payment.create
- ai.use
- ai.execute

用户与 Role：

User ↔ UserRole ↔ Role ↔ RolePermission ↔ Permission

一个用户可以有多个 Role。

---

# 24. Audit

所有关键 mutation 记录：

- actor\_type
- actor\_id
- organization\_id
- action
- resource\_type
- resource\_id
- before
- after
- request\_id
- source
- created\_at

source：

- WEB
- AI
- API
- SYSTEM

用户通过 AI 操作时：

actor\_type = USER

source = AI

不要把责任主体简单写成 GPT。

---

# 25. Transactional Outbox

核心业务事务必须：

ERP data mutation

- \


outbox event INSERT

在同一个 DB transaction 中提交。

Background worker 再消费 Outbox。

不要只依赖：

DB COMMIT → celery.delay()

否则可能 DB 成功、Celery 消息丢失。

Outbox consumer 使用：

`FOR UPDATE SKIP LOCKED`

---

# 26. Background Jobs

使用：

Celery + Redis

适合：

- Outbox publishing
- Product embedding
- Stock alerts
- Overdue receivable detection
- Daily brief preprocessing
- Import processing
- Notifications
- Analytics projection

禁止 Celery 处理：

- 核心销售出库 transaction
- 核心采购入库 transaction
- 核心付款 transaction

这些必须同步、原子完成。

---

# 27. 前端技术栈

已冻结：

- Next.js App Router
- React
- TypeScript
- shadcn/ui
- Base UI
- Tailwind CSS
- TanStack Query
- TanStack Table
- React Hook Form
- Zod
- openapi-typescript
- openapi-fetch
- decimal.js
- Recharts
- Playwright
- Vitest
- pnpm

---

# 28. UI 原则

整体视觉：

**Dense but calm**

不是 Marketing Landing Page。

左侧一级导航：

- Dashboard
- AI
- Sales
- Purchase
- Inventory
- Products
- Customers
- Suppliers
- Finance
- Reports
- Settings

全局：

`Cmd/Ctrl + K`

可以：

- 导航
- AI command
- 搜索商品
- 开单
- 查库存
- 查询经营数据

---

# 29. 前端状态管理

Server State：

TanStack Query

Form State：

React Hook Form

Table State：

TanStack Table

URL State：

Search Params

不要创建巨大的：

`useERPStore()`

Zustand 如果以后使用，只用于少量纯 UI state。

---

# 30. API Contract

Source of Truth：

Pydantic / FastAPI OpenAPI

流程：

Pydantic

→ FastAPI OpenAPI

→ openapi.json

→ openapi-typescript

→ generated/api/schema.d.ts

→ openapi-fetch

禁止前端手写一套重复 DTO。

Generated API types 不允许人工编辑。

---

# 31. 前端 API 同源

浏览器只访问：

`/api/v1/*`

Next.js rewrite：

`/api/* → FastAPI`

不要让浏览器直接写：

`http://localhost:8000`

这样 Cookie / CSRF / production routing 更简单。

---

# 32. AI / Agent 技术栈

已冻结：

- LangGraph 为核心 Runtime
- LangChain 作为模型、Tool、Retriever 等组件层
- LangSmith 做 Trace/Evaluation
- PostgreSQL LangGraph Checkpointer

第一阶段只做：

**One ERP Assistant + Tool Registry**

暂时不要 Multi-Agent。

---

# 33. Agent 架构

推荐流程：

START

→ understand\_intent

→ resolve\_entities

→ plan

→ select\_tools

→ risk\_check

→ execute / interrupt

→ synthesize

→ END

高风险操作：

risk\_check

→ LangGraph interrupt

→ approve / edit / reject

→ resume

→ execute

---

# 34. Agent Runtime Context

这些内容不能交给 LLM 任意修改：

- organization\_id
- user\_id
- permissions
- request\_id
- conversation\_id

应该放在 Runtime Context。

Graph State 可以保存：

- messages
- intent
- resolved\_entities
- proposed\_actions
- tool\_results
- pending\_approval
- final\_answer

AI Tool 绝不从 LLM 参数获取 organization\_id。

---

# 35. AI Tool 分类

Query Tools：

- search\_products
- get\_product
- get\_inventory
- get\_inventory\_movements
- search\_customers
- get\_customer
- get\_customer\_price\_history
- search\_suppliers
- get\_supplier\_purchase\_history
- get\_sales\_summary
- get\_profit\_summary
- get\_low\_stock\_products
- get\_overdue\_receivables

Draft Tools：

- create\_sales\_order\_draft
- create\_purchase\_order\_draft
- create\_sales\_return\_draft
- create\_purchase\_return\_draft
- create\_stock\_transfer\_draft
- create\_stocktake\_draft

Execution Tools：

- confirm\_sales\_order
- post\_sales\_shipment
- confirm\_purchase\_order
- post\_purchase\_receipt
- post\_stock\_adjustment
- record\_receipt
- record\_payment

---

# 36. AI 风险等级

Risk 0：

只读

Risk 1：

创建 Draft

Risk 2：

业务承诺，例如 Confirm Order

Risk 3：

实物库存/资金变化，例如：

- Shipment POST
- Receipt POST
- Payment
- Inventory Adjustment

Risk 4：

批量、大额、异常高风险动作

高风险需要 Policy / Human Approval。

---

# 37. AI 安全原则

LLM 输出视为不可信，必须 Schema Validation。

企业内容也视为：

**Untrusted Business Content**

包括：

- 商品描述
- 客户备注
- 供应商备注
- Excel
- PDF
- Email
- 网页内容
- 上传文件

这些内容不能改变：

- System Policy
- Permission
- Tool Policy
- Risk Policy

Prompt injection 不能提升权限。

---

# 38. AI 商品搜索

五金行业商品搜索不能纯 embedding。

搜索优先：

1. Exact SKU
2. Exact Barcode
3. Exact Specification
4. Structured Attributes
5. Keyword / trigram
6. Semantic similarity

例如：

`304 8*30`

应能匹配：

`304不锈钢外六角螺栓 M8×30`

PostgreSQL 第一版足够：

- B-tree
- pg\_trgm
- JSONB GIN
- pgvector

暂不上 Elasticsearch。

---

# 39. AI 自然语言开单

用户：

“给宏达开 10 箱 304 M8×30，还是上个月价格。”

流程：

1. resolve customer
2. resolve product
3. resolve unit
4. query unit conversion
5. query last customer price
6. query inventory
7. calculate deterministic preview
8. create DRAFT
9. optionally ask user to confirm

默认 AI 自动创建 Draft 是 Risk 1。

不要默认自动 POST 出库。

---

# 40. 补货逻辑

补货数量由确定性算法计算，不让 LLM 猜。

基础：

average\_daily\_sales

days\_of\_stock

reorder\_point

supplier lead time

safety stock

open purchase quantity

例如：

`available_qty <= reorder_point`

成为补货候选。

AI 负责解释：

“为什么建议买。”

系统算法负责：

“买多少。”

---

# 41. 测试原则

必须有：

- unit tests
- domain tests
- integration tests
- API tests
- concurrency tests
- tenancy tests
- agent evals
- Playwright E2E

尤其 InventoryEngine。

关键测试：

- purchase 100 @10 → avg cost 10
- purchase 100 @12 → avg cost 11
- ship 50 → cost 550
- inventory 10 ship 11 → reject
- sales confirm 100 → reserved +100, on\_hand unchanged
- partial shipment → reservation/on\_hand correct
- concurrent issue → only one succeeds
- duplicate idempotency → one document only
- posted document immutable
- reversal restores inventory
- cross-tenant access denied
- AI inherits cost permission
- prompt injection does not bypass approval

Hypothesis 应用于 Inventory property tests。

必须长期满足：

`InventoryBalance.on_hand == SUM(InventoryMovement.base_qty)`

---

# 42. Observability

Software：

- structured JSON logging
- OpenTelemetry
- Sentry

AI：

- LangSmith

贯穿统一：

- request\_id
- trace\_id
- conversation\_id

HTTP Request ID：

`X-Request-ID`

不存在则服务器生成。

---

# 43. Repository 结构

目标：
```bash
forge-erp/
├── AGENTS.md
├── README.md
├── .env.example
├── docker-compose.yml
├── Makefile
│
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   ├── alembic.ini
│   │   ├── migrations/
│   │   ├── scripts/
│   │   ├── src/forge_erp/
│   │   │   ├── main.py
│   │   │   ├── core/
│   │   │   ├── modules/
│   │   │   ├── ai/
│   │   │   └── workers/
│   │   └── tests/
│   │
│   └── web/
│       ├── app/
│       ├── components/
│       ├── features/
│       ├── lib/
│       └── generated/api/
│
├── docs/
│   ├── architecture/
│   ├── domain/
│   ├── api/
│   ├── ai/
│   └── adr/
│
├── infra/
│   ├── docker/
│   └── postgres/
│
└── scripts/
```

---

# 44. 后端模块结构
```
modules/
├── identity
├── organization
├── catalog
├── customers
├── suppliers
├── inventory
├── sales
├── purchasing
├── receivables
├── payables
├── analytics
└── audit
```

模块内部：
```
domain/
application/
infrastructure/
api/
```

规则：

Domain 不依赖 FastAPI。

FastAPI Router 不写业务逻辑。

---

# 45. AGENTS.md 核心规则

Work 开始施工后，应在根目录创建 `AGENTS.md`，至少包含：

1. Business writes MUST go through application commands.
2. FastAPI routes MUST NOT contain domain logic.
3. AI tools MUST call the same application commands used by normal UI/API.
4. AI tools MUST NOT mutate business tables directly.
5. Domain code MUST NOT depend on FastAPI.
6. Every operation executes inside organization scope.
7. organization\_id comes from authenticated runtime context.
8. Cross-tenant reads/writes are forbidden.
9. New tenant tables require RLS.
10. InventoryMovement is immutable.
11. InventoryBalance is a projection.
12. Only InventoryEngine mutates InventoryBalance.
13. Every inventory change creates InventoryMovement.
14. Negative stock is forbidden in MVP.
15. Inventory locks use deterministic warehouse/product order.
16. Posted documents are immutable.
17. Corrections use reversal.
18. Business state transitions use explicit Commands.
19. Never change business status through generic CRUD.
20. Never use float for money, price, rate, quantity.
21. Backend uses Decimal.
22. Database uses NUMERIC.
23. Financial calculations are server-authoritative.
24. Related ERP mutation + Audit + Outbox must commit atomically where applicable.
25. Background queues are not source of truth for committed business state.
26. Side-effect endpoints support idempotency.
27. Server enforces permission.
28. UI hiding is not authorization.
29. Prompt is not authorization.
30. AI inherits user's permissions.
31. LLM output is untrusted until validated.
32. AI entity-changing tools use resolved IDs.
33. High-risk tools require policy evaluation/approval.
34. Enterprise documents/content are untrusted prompt content.
35. Every bug fix requires regression test.
36. Inventory changes require domain tests.
37. Concurrency-sensitive code requires concurrency tests.
38. AI behavior changes require eval.
39. DB schema changes require Alembic.
40. Do not add infrastructure dependency without ADR.
41. Do not introduce microservices without demonstrated need.
42. Do not upgrade framework major versions unless explicitly requested.
43. Never manually edit uv.lock or pnpm-lock.yaml.
44. Never manually edit generated OpenAPI TypeScript.
45. Merged migrations are append-only.
46. Dev seed contains no production secrets.
47. Application DB connection uses non-superuser PostgreSQL role.
48. Browser uses same-origin `/api`.
49. Do not add Kafka/Elastic/Mongo/GraphQL/Temporal/K8s unless explicitly approved.

---

# 46. ADR

Architecture decisions should be stored in:

`docs/adr/`

Initial ADR list:

- 0001-modular-monolith.md
- 0002-postgresql-primary-database.md
- 0003-server-authoritative-domain.md
- 0004-cookie-session-auth.md
- 0005-postgresql-row-level-security.md
- 0006-transactional-outbox.md
- 0007-openapi-contract-generation.md
- 0008-langgraph-agent-runtime.md

Future:

- inventory ledger
- moving average cost
- posted-document immutability

---

# 47. 当前项目进度

设计阶段已经完成：

- System Design v0.1
- Technology Architecture v0.2
- Implementation Specification v0.3
- Bootstrap Specification v0.4

现在不应继续进行大量抽象架构讨论。

**当前任务已经进入正式施工阶段。**

---

# 48. 当前唯一施工目标：Bootstrap v0.4

现在只实现 Platform Foundation。

不要提前实现：

- Catalog
- Inventory
- Purchasing
- Sales
- Receivables
- Payables
- AI business tools

Bootstrap 范围：

- Monorepo
- uv backend
- pnpm frontend
- Docker infrastructure
- PostgreSQL 18 + pgvector
- Redis
- FastAPI
- Next.js
- shadcn/Base UI
- Alembic
- SQLAlchemy async
- Organization
- User
- RBAC
- Opaque HttpOnly Cookie Session
- Argon2
- Tenant Context
- PostgreSQL RLS
- Request ID
- structured logging
- Problem Details errors
- Audit foundation
- Transactional Outbox foundation
- Idempotency foundation
- OpenAPI → TypeScript generation
- Next.js same-origin `/api`
- TanStack Query provider
- Basic ERP Shell
- Login page
- Dev seed
- Backend tests
- RLS/Tenancy tests
- Frontend smoke tests
- GitHub Actions CI

---

# 49. Bootstrap 本地基础设施

开发模式推荐：

PostgreSQL / Redis：

Docker

FastAPI：

本地 uv

Next.js：

本地 pnpm

Docker PostgreSQL：

`pgvector/pgvector:pg18`

需要创建：

- pg\_trgm
- vector

Application database user：

`forge_app`

必须：

- non-superuser
- no createdb
- no createrole

Migration 使用更高权限独立 connection。

---

# 50. Bootstrap Auth API

实现：

- POST /api/v1/auth/login
- POST /api/v1/auth/logout
- GET /api/v1/auth/me

以及：

- GET /healthz
- GET /readyz
- GET /api/v1/system/version

---

# 51. Bootstrap Seed

开发 seed 至少创建：

Organization：

`DEMO`

Role：

`ADMIN`

Admin user：

由环境变量决定 email/password。

禁止将固定生产密码写入 Git。

---

# 52. Bootstrap Frontend

创建：

Next.js App Router

shadcn：

Base UI

初始 Shell：

- Dashboard
- AI
- Sales
- Purchase
- Inventory
- Products
- Customers
- Suppliers
- Finance
- Settings

但 Bootstrap 阶段除：

- Login
- Shell
- Profile/Me

之外其他页面只允许 placeholder。

不要提前写业务功能。

---

# 53. Bootstrap OpenAPI Pipeline

必须打通：

Pydantic

→ FastAPI OpenAPI

→ openapi.json

→ openapi-typescript

→ generated TypeScript

→ openapi-fetch

CI 检查生成结果 drift。

---

# 54. Bootstrap CI

PR 至少：

Backend：
- uv sync --locked
- Ruff
- Ruff format check
- Pyright
- pytest

Frontend：
- pnpm install --frozen-lockfile
- lint
- typecheck
- Vitest
- build

Integration：
- PostgreSQL 18 + pgvector
- Redis
- alembic upgrade head
- API tests
- tenancy/RLS tests

另外：
- clean DB migration test
- OpenAPI drift test

# 55. Bootstrap 完成验收

只有以下全部通过，才允许进入 Catalog v0.5：
- Repository structure frozen
- uv.lock committed
- pnpm-lock.yaml committed
- PostgreSQL boots
- pgvector enabled
- Redis boots
- Clean DB runs alembic upgrade head
- FastAPI boots
- Next.js boots
- shadcn works
- organization exists
- user exists
- login works
- logout works
- /auth/me works
- secure session architecture works
- RBAC works
- Tenant Context works
- PostgreSQL RLS works
- Audit foundation works
- Outbox foundation works
- Idempotency foundation exists
- OpenAPI → TS works
- structured logging works
- request ID works
- backend tests pass
- integration tests pass
- tenancy tests pass
- frontend checks/build pass
- CI green

Bootstrap 完成后建议创建：
git tag bootstrap-v0.4

# 56. 下一阶段

Bootstrap 通过验收后进入：Catalog Specification / Sprint v0.5

顺序：
1. Category
2. Brand
3. Unit
4. Product
5. ProductUnit
6. ProductPrice
7. Customer
8. Supplier
9. SupplierProduct
10. Warehouse
11. Product Search
12. ProductPicker

Catalog v0.5 的重点：
- 五金规格属性模型
- JSONB attribute schema
- 单位换算
- 历史换算快照
- 商品编码
- 商品搜索
- trigram
- JSONB filtering
- embedding
- ProductPicker 快速键盘操作
- Excel 数据导入/清洗的基础设计

不要在 Bootstrap 完成之前进入这一阶段。

# 57. 当前 Work 的执行要求

现在请不要重新设计整个 ERP。
请直接：
1. 检查本上下文。
2. 按 Bootstrap v0.4 开始建立仓库。
3. 创建 AGENTS.md 和 ADR。
4. 使用小步施工。
5. 每个增量执行相关测试。
6. 遇到实现细节时优先遵守本文硬约束。
7. 不要为了“更现代”擅自增加基础设施。
8. 如果发现上下文存在真正冲突，明确指出冲突及建议，不要自行静默改变业务语义。
9. Bootstrap 完成后给出：implementation summary、final repository tree、run commands、migration status、test results、unresolved issues、architecture decisions requiring review。
10. Bootstrap 未通过验收前不要继续 Catalog。

> Provenance: Sections 1–53 and section 54 opening retrieved from the referenced conversation; sections 54–57 supplied directly by the user in this task on 2026-09-05.
