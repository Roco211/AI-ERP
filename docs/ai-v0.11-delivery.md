# AI assistant v0.11 交付记录

当前状态：A0–A5 47项实现验收完成，代码提交 `0c585f42ad973f0c3a1466354474af829b6d85ee` 的两条 CI 均通过。评审为 [PR #7](https://github.com/Roco211/AI-ERP/pull/7)，分支 `ai/v0.11`，不自动合并或发布。

## Implementation summary

沿用原有架构，新增网页模型服务配置与 `/ai` 工作台。一套组织配置服务私有用户对话；一个有界 LangGraph 调用现有 Query，显示服务端事实与来源。每日简报直接读取统计，销售/采购经过可编辑预览及明确复核，只创建草稿。

模型权限来自当前 cookie 会话。查询前后重新认证，模型等待不占数据库事务。PostgreSQL 保存 JSON checkpoint、运行预算和复核进度；永久回执与业务草稿、Audit/Outbox 原子提交。七天正文清理保留已创建业务单据。

供应商配置支持 OpenAI 兼容接口、云端 HTTPS 及明确启用的本机/内网服务。密钥按组织加密，不回显。模型或权限变化后阻断旧上下文；本地 embedding 不受影响。

## Final repository tree

[完整仓库目录](ai-v0.11-repository-tree.txt)（排除依赖、构建产物、凭据和测试输出）。

## Run commands

```sh
make env install infra migrate seed

# 在四个终端分别运行：
make api
make web
make worker
make beat
```

在 `http://localhost:3100/settings/llm` 配置并测试模型；使用 `http://localhost:3100/ai`。

验证：

```sh
make lint test
make contract
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
bash scripts/test_browser_with_worker.sh
```

详细网页操作、密钥恢复和可选 LangSmith 配置见 [模型服务设置](ai-v0.11-model-setup.md)。原有开发账户/密码保留；不自动启用资金、不重置资料。

## Migration status

`0015_reporting → 0016_ai_assistant → 0017_ai_provider_settings → 0018_ai_retention`。迁移仅向前追加；旧发布迁移未改动。当前体验库已升级，升级前备份已实际恢复到独立数据库并比对原始事实，恢复库随后删除。

## Test results

参见 [逐项验收](ai-v0.11-acceptance.md) 与 [证据映射](ai-v0.11-acceptance-evidence.md)。固定响应评估验证应用保护边界，真实供应商评估验证所配置模型，两类不混用。完整 CI 结果：

| 检查 | 结果 |
| --- | --- |
| 后端、迁移、RLS/tenancy、固定模型评估 | 1069 passed |
| 前端 | 136 passed |
| 生产浏览器（含 worker / beat） | 24 passed |
| 锁定安装、Ruff、格式、Pyright、lint、typecheck、build | 通过 |
| OpenAPI → TypeScript 漂移 | 无 |
| 实际模型 | 商品/库存查询和销售/采购预览通过；缺条件与禁止操作提示正确；模糊选实体请求受控拒绝 |

代码提交的 [push CI](https://github.com/Roco211/AI-ERP/actions/runs/33993106772) 与 [PR CI](https://github.com/Roco211/AI-ERP/actions/runs/33993119410) 均成功。最终文档提交自身也必须通过其精确 SHA 的两条 CI；交付目录另附 `Forge-ERP-AI-v0.11-verification.json` 记录，不能用上面的代码提交结果替代。

真实模型验收只创建待复核预览，核对数量2、单价3.50、服务端合计7后拒绝，销售和采购订单数均未改变。

## Unresolved issues

- 真实模型的模糊指令可能出现格式错误或尝试选择未明确资料；本次“随便挑一个”样例首次格式失败，原轮重试被服务器实体校验拒绝，未生成提案。这是受控拒绝，不代表模型总能正确澄清。遇到此类情况请补充准确编码、单位和数量，再开始新一轮。
- 本期建单以本轮明确输入为依据，不自动继承旧轮次的实体选择、数量和单价。补条件时请一起提交完整开单内容；已有预览可以直接在表单中修改并重新计算。
- LangSmith 远端未配置，默认关闭；元数据脱敏边界已本地验证，不声称远端连接通过。

## Architecture decisions requiring review

- [ADR0017](adr/0017-ai-assistant-runtime-and-draft-review.md)：单助手、服务端事实、草稿复核、永久去重、保留期及调用预算。
- [ADR0018](adr/0018-web-managed-model-provider.md)：组织模型配置、密钥派生/恢复、内网选择权限与接口兼容边界。

本轮不进入 v1.0、不自动合并或发布。最终文档提交自身 CI 验证通过后，建议按评审流程合并，再创建 `ai-v0.11` 标签；本轮未创建该标签。
