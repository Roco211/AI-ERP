# ADR 0017 — 单助手、受租户隔离的持久状态与草稿复核

Status: Implemented for authorized v0.11; real-provider samples verified, retained for architecture review

基线 operations-v0.10。沿用 ADR0008 的 LangGraph/LangChain/LangSmith 方向，对话模型按 ADR0018 由用户在网页配置供应商、地址和模型；embedding 仍为 ADR0011 的本地 BGE-M3。

1. 实现 LangGraph checkpointer 协议的 PostgreSQL 适配器，由 Alembic 管理，JSON 存储并强制组织/所有者 RLS。官方 saver 默认表不具备本项目组织/owner 约束，因此不直接运行 setup()。持久 state 不保存授权上下文。
2. 会话仅创建者可读。当前权限指纹变化时停止旧会话恢复及历史访问，以避免旧结果泄露。所有工具/批准重新认证，模型请求在 DB 事务之外。
3. 单助手使用固定工具注册表和受限 JSON 决策协议，不把 provider 兼容 Chat Completions 等同于指定模型已支持原生工具协议。缺密钥/模型不支持清楚失败，不更换服务商。
4. 模型负责意图与证据选择，服务端渲染事实数字、范围与内部来源。业务值复用现有 Query/Command 的 Decimal、权限和口径；不新增第二套业务事实。
5. 本期 Risk 1 采用用户复核后建草稿，这是产品选择而非原规范的强制要求。Risk 2–4 不注册。提案绑定 server preview/version/hash；批准重新核对依据和权限。
6. 永久建单回执与既有 Command、Audit、Outbox 同事务。业务成功后的 checkpoint 故障不重复建单。原有通用幂等过期不影响永久回执。开单预览完全无业务写入；批准在 SAVEPOINT 内重算并核对依据，再调用原 Command，提交前比较实际持久头部和每行数量、换算、价格、来源与金额。共享引用锁不能阻止新价格或历史记录插入，因此任何实际捕获差异都要求重新复核，并回滚该 Command、Audit、Outbox 和普通幂等记录，不只比较总金额。
7. 日报按用户请求与权限生成，正文七天有界保留；永久单据来源不随会话正文删除。LangSmith 默认仅本地 trace，可选上传白名单诊断元数据，禁止自动上传完整消息/工具结果。

详见 [施工规范](../ai-v0.11.md) 和 [验收清单](../ai-v0.11-acceptance.md)。这些选择不改变库存、销售、采购或轻量资金语义，不扩展会计/账期、外部执行工具、新基础设施或多 Agent。

用户后续明确要求网页自由配置供应商：以组织级管理员设置替代固定CommandCode/终端凭据，支持自定义OpenAI兼容接口。凭据使用cryptography Fernet，HKDF从既有高熵SESSION_SECRET按组织派生；恢复需保留秘密，密钥轮换后可重新录入供应商凭据。配置版本参与会话指纹，旧上下文不自动发给新供应商。公网HTTPS、显式本机/内网、连接IP固定及敏感地址拒绝约束不提供任意HTTP工具。
