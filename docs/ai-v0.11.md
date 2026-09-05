# AI 助手 v0.11 施工规范

状态：A0 规范已冻结，A1–A5 待实施与验证。用户于 2026-09-06 明确选择“明确规范后直接分步实现”。基线为已发布 operations-v0.10 / `ed7749d03b291073d2d86bb846dfa87130422be5`，数据库 `0015_reporting`。原迁移上下文和已有业务规则继续有效。

## 1. 交付范围

沿既定路线交付一位 ERP Assistant：库存、商品、经营及往来问答；每日简报；自然语言销售/采购开单预览，用户复核后创建 DRAFT。默认查询既有 Application Query，业务写入只调用既有 Command。AI 不计算金额、库存、成本、补货量，不直接操作业务表。

不注册订单确认、出入库 POST、退货、库存调整、收付款或冲销工具；不增加会计、账期、多 Agent、外部网页/文件执行、任意 SQL/HTTP 工具或新基础设施。已有补货页继续负责带补货依据的采购创建，普通 AI 采购草稿不冒充补货来源单。

## 2. 模型与执行栈

- LangGraph 为单助手状态机；LangChain Core 的消息、工具/运行组件；PostgreSQL 持久 checkpoint；LangSmith 的显式脱敏诊断接口及评估。所有 Python 依赖使用 uv 锁定，保留 Python 3.14。
- 保留用户指定的 CommandCode chat endpoint `https://api.commandcode.ai/provider/v1/chat/completions` 和 `deepseek-v4-flash-vision-exp`。模型名尚需真实连接验证；如服务不支持，报告错误，不静默换模型或服务商。
- 对话模型凭据仅服务端配置，缺失时清楚提示未配置；CI 使用固定模型响应进行控制逻辑评估，绝不将其计为真实模型验收。
- 本地 Ollama/BGE-M3 仍仅用于 Catalog 语义回退，聊天与 embedding 互不依赖。
- 使用受限 JSON 决策协议，不假定选定模型支持原生 tool calling/JSON schema。每次响应 Pydantic 校验，未知工具、额外身份字段、无效/过大结果一律拒绝。
- 模型网络调用在数据库事务之外。每轮上限 5 次模型调用、8 次工具、80 秒总时限；单次模型等待不超过 20 秒，输入 4000 字，20 行草稿，工具页默认 10/最大 25 条，总结果有界。超限清楚结束，禁止无限修复/重试。

## 3. 身份、会话与 checkpoint

所有接口沿用 opaque HttpOnly session、同源 `/api`、CSRF、request ID、Problem Details。增加 `ai.use`、`ai.draft.create`；AI 权限不代替业务权限。RuntimeContext 的组织、用户、权限、请求和会话 ID 均由服务端绑定，`source=AI`，不来自模型参数。

会话属于组织内的创建者，暂不支持共享。会话、轮次、checkpoint、pending writes、提案和永久创建回执全部具备 organization_id 和 owner_id，以及对应复合外键、FORCE RLS。RLS 同时约束当前组织与所有者，缺失上下文默认无可见行。客户端 UUID 只定位已经授权的对象。

会话记录当前权限指纹；历史读取、继续、恢复、复核、批准时重验身份和指纹。权限发生变化时阻断旧上下文并要求新会话，不能把旧工具结果、成本或客户资料再发给模型/浏览器。注销、停用或会话过期同样阻止后续执行。持久 state 不能保存 cookie、key、权限集合或可被恢复为授权的 RuntimeContext。

使用实现 LangGraph checkpointer 协议的 PostgreSQL 存储适配器，复用 SQLAlchemy/psycopg3 与受限 forge_app。新表经 Alembic 建立，禁止运行时 saver.setup()/DDL 或 migration 连接。只保存 JSON 状态，禁用 pickle 反序列化；保留中断/重启恢复测试。checkpoint 是进度，不是业务事实。

并发消息按会话互斥，使用可过期领取标记，不在等待模型期间占用连接或行锁。失败重试沿用原轮次/请求标识；运行预算及重试次数持久化，不通过重启无限增加模型调用。

## 4. 工具与数据口径

工具固定名称及严格 schema，复用 Application Query 后先经已有 Pydantic 输出 DTO 过滤，再作字段最小化。不能直接把 Query 原始 dict 发给模型。搜索/分页/日期约束在工具层重新执行，不能依赖被绕开的 HTTP 参数校验。

工具组：商品搜索/详情/单位、客户/供应商/仓库查找，库存余额/流水/最低库存，经营概览/来源，资金启用状态与未结清来源，确定性补货建议，销售报价与成交历史、采购历史。每个工具独立要求既有业务权限；价格、成本、毛利与往来字段沿既有规则裁剪。

reporting/replenishment 使用单次 REPEATABLE READ READ ONLY 快照。现有销售报价、历史等含 FOR SHARE 的 Query 保留短普通事务；不得为统一“只读”而改变已有锁语义。工具之间不声称共享一个数据库时点。

必须保留的解释：

- 现存、占用、可用区分；余额是投影，流水为事实。
- 期间销售/毛利/现金与当前应收应付、库存估值区分；合法冲销可能改变历史区间。
- 缺成本、无权限、资金未启用、旧来源未衔接不得当成零。
- **当前没有账期或 due_date，不支持逾期推断**；只回答未结清应收/应付。
- 补货量来自 v0.10 确定性算法，保留 30 完整业务日、7 天复核期、在途与缺交期语义。
- 商品遵循现有精确/结构化/关键词优先、本地语义回退；不能默取歧义候选、猜单位或编造实体 ID。

## 5. 数字与每日简报

回答由服务端事实卡片、来源链接、查询时点和范围组成。模型只选择已取得的证据及受限回答意图；业务数字由服务端格式化，不把模型自由生成的金额、库存或百分比作为答案。没有证据时要求补充条件或明确不可回答。

每日简报默认查询上一个完整业务日，可选业务日期；调用现有 reporting snapshot 生成期间销售/实际毛利/现金、当前余额和库存提示。按当前用户权限生成，未启用资金明确显示。v0.11 在用户打开/请求时生成，不建立跨用户共享敏感日报，不新增邮件/推送或无人审批的业务动作。

企业名称、备注、商品描述、Excel 等均是不可信业务内容。模型不能通过其中的指令改变工具、权限和风险边界。前端以文本和服务端允许的内部链接呈现，不执行 HTML、脚本或模型生成网址。

## 6. 开单预览与批准

风险 1 本期采用“预览→修改并重新预览→明确点击创建草稿”的交互；这是本阶段选择，原规范允许自动建 Draft，并未强制所有 Risk 1 人工审批。

提案必须具备已解析 customer/supplier、warehouse、product、unit UUID 和十进制字符串数量。销售 AUTO 复用既有报价优先级，不接受模型传单价；MANUAL/采购价清楚展示为待用户复核输入。不能把“上月价”默认为最新价或随意定价，无可核对来源时要求用户选定。

预览由服务端现有值对象/报价规则计算，包含名称、单位、换算版本、数量、单价、金额、价格来源、资料依据及 confirmation hash。不通过临时建单再回滚来实现预览。批准锁定相关引用，重新计算并比较依据；变化则要求重新审阅。用户改任何字段必须新预览，不允许批准请求携带未经预览的替换内容。

LangGraph interrupt 持久化提案，approve/edit/reject 经单独 UI/API 命令处理，模型不能给自己批准。提案 30 分钟有效；拒绝/过期不可执行。创建只调用新建销售/采购 DRAFT Command，固定 id=None；不占用库存、不产生实物/现金事实。

永久 proposal→document 回执与业务 Command、Audit、Outbox 同事务提交；普通幂等记录到期、双击、并发批准、响应丢失、业务成功而 checkpoint 失败都只能得到同一张草稿。回执包含提案版本/hash、目标单据及请求来源。恢复只读取回执，不从 checkpoint 推断是否已执行。

## 7. 日志、保留与配置

审计记录 actor、source=AI、conversation/turn/proposal/tool/request 关联及状态；不保存模型凭据、cookie 或全量提示到日志。日志只允许工具名、耗时、错误码、预算计数等元数据。业务 Command 既有审计仍保留原必要事实。

聊天及 checkpoint 正文默认保留 7 天；过期后不可读取/恢复/批准，通过有界清理清除正文；永久建单回执和业务审计继续保留。会话删除清正文并作废未执行提案，不能删除已创建单据或永久回执。后台任务可使用现有 Celery；任务执行前仍校验归属与当前权限。

LangSmith 默认不向外发送提示/业务数据。可配置诊断上传只允许脱敏运行 ID、模型标识、工具名、错误码、计数和评估得分；不接自动全量 callback。没有凭据时本地结构化 trace 和评估仍可用，远端验收单列状态。

## 8. 增量与验证

| 增量 | 实施 | 本增量验证 |
|---|---|---|
| A0 | 本规范、验收、ADR、AGENTS 与来源 | 与现有接口和原约束逐项核对 |
| A1 | 会话/权限/RLS/checkpointer、模型配置、严格 schema | 空库与旧库迁移、跨组织/同组织跨用户、JSON 持久恢复、配置失败 |
| A2 | Tool Registry、受限图、问答/日报、事实输出 | Query 复用、权限裁剪、缺事实、注入/预算/模型错误、确定性 eval |
| A3 | 销售/采购预览、中断、复核、永久草稿回执 | 过期依据、并发、到期重放、原子回滚、无库存/资金副作用 |
| A4 | /ai 会话、事实卡片、日报、草稿编辑/批准及原单追溯 | Vitest、真实浏览器、权限/错误/重试、同源契约 |
| A5 | 真实模型连接与任务评估、升级/回归、CI、交付 | 验收清单逐项证据；未真实验证项不得勾选 |

每个增量运行相关测试后提交；全部完成前不进入 v1.0 试点、不隐式合并/tag/release。保留原数据、密码、历史迁移和已发布标签。必要的本地模型服务凭据由用户配置，不在聊天中索取明文。

## 9. 技术依据

- [原项目上下文](architecture/migration-context.md) 第32–39节及 [ADR0008](adr/0008-langgraph-agent-runtime.md)。
- [CommandCode Provider](https://commandcode.ai/docs/provider)：官方兼容 Chat Completions；不据此推断指定模型的真实可用性。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 与 [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：恢复需要持久 checkpoint 和服务端 thread 标识；业务去重另外由 ERP 回执保证。

外部文档核对日期：2026-09-06。实际安装版本以 uv.lock 为准。
