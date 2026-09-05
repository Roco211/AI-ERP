# Sales v0.8 — S4 交付记录

本增量从 S3 `cc023a5` 继续。用户回复“继续”，按已有规范实施销售工作台；沿用 `sales/v0.8` 和草稿 PR #4。S5 最终阶段验收、可选销售 seed 与发布仍待完成。

## Implementation summary

- 销售入口开放订单、销售出库、销售退货和客户成交历史四个页签。列表由服务器分页，页面每页 25 条，支持客户、状态及有界关键字筛选；每单最多 200 行。
- 客户选定后用 ProductPicker 键盘选品和单位；商品与单位解析为 ID，RHF 管理表单，Zod 校验数量和行数。AUTO 获取建议价，MANUAL 必须明确输入（零价也需输入 0）。切换客户清除全部行的价格确认，过期报价或换算响应不能写回当前表单。
- 草稿保存由服务器重新解析 AUTO 价格。页面展示保存后的单价、行金额与总额；若建议价改变，提示再次复核后确认。确认只占用库存，关闭剩余释放占用；界面不会自行计算库存数量、金额或毛利。
- 后端提供冻结销售单位的待发量、可执行量，供出库草稿默认数量；关闭后可执行量为零。现有/仓库占用/可用三值来自同一余额行快照，只有 `inventory.read` 可见。它们是查询当时的库存，过账仍检查实时状态。
- 出库、退货从原单建立，冻结客户、仓库、商品、单位和价格，只输入数量及原因。退货草稿显示服务器成本回收报价，过账后显示真实库存成本。授权用户可查看净销售、净成本和已实现毛利；数量仓管无需成本或价格权限即可处理来源单据。
- 成交价来源可展开，新历史快照可直接跳回原出库，旧快照退回客户/商品历史筛选。销售单据可以查看仅属于该单的库存流水，流水再跳回销售订单或出入库原单。
- 重复点击使用同步提交锁；网络或服务异常后保留原请求体和幂等键，锁定字段、弹窗和页面导航。后续权限拒绝也不能丢弃结果未明的原提交。首次确定性冲突提供重新加载，成功写入但详情读取失败不会再次创建业务单据。
- 价格、成本和毛利按当前权限展示；撤销报价权限会清掉编辑器缓存报价并停止未完成请求。服务端仍执行最终权限校验和租户隔离。
- 复用现有 Base UI、TanStack Query/Table、OpenAPI→TypeScript；新增代码未引入基础设施、框架大版本或持久依赖。

## Migration status

本增量没有新增迁移；数据库 head 保持 `0011_sales_returns`。S4 只扩展读取 DTO 和查询，未修改库存 Engine、应用层写入命令、既有库存事实或旧迁移。

`PriceSource.source_document_id` 为可选字段，兼容旧价格快照。订单新增读取数量、库存流水新增凭据类型及受权限控制的销售订单关联；OpenAPI 与 TypeScript 由同一生成流程更新。

## Run commands

```sh
uv sync --project apps/api --locked
pnpm install --frozen-lockfile
make infra migrate
make api
# 另一终端
make web
# 如需同步开发管理员已有销售权限
make seed

# 后端与工作台回归
uv run --project apps/api pytest apps/api/tests -q
make lint
pnpm test
make contract
pnpm build
pnpm test:e2e
```

体验地址：`http://localhost:3100/sales`，沿用已有开发企业及账户。写请求保持同源 `/api`、HttpOnly Cookie、Origin 和 Idempotency-Key；不在文档记录凭据。浏览器测试创建独立 `BROWSER_SALES_*` 企业和角色，通过已有 Command 准备业务数据，完成后清理，不依赖或改写开发企业销售事实。

## Test results

- 后端完整回归 **316 passed / 0 skipped，455.89 秒**；包含 11 项工作台读取测试及既有 Catalog、Inventory、Purchasing、Sales、RLS、升级和并发回归。
- 前端完整 Vitest **37 passed**，其中销售工作台 17 项；选品与库存来源专项 13 项通过。新增回归覆盖乱序报价/详情、切换客户、缓存撤权、业务冲突、重复点击、响应丢失、原键重试后再遇 403、退货草稿成本及原单筛选。
- Ruff、格式检查、项目配置 Pyright、前端 lint、最终 typecheck 和生产 build 均通过。Lint 有 4 条 TanStack Table / React Compiler 提示（3 条既有、1 条新工作台同类提示），没有错误。
- 真实浏览器 **10 项通过**：新增销售 3 个场景全部通过（主流程 13.9 秒；销售员 8.1 秒；仓管 11.0 秒），既有商品/库存/采购及登录 7 项回归通过（28.8 秒）。销售测试企业已全部清理。
- OpenAPI 重新导出及 TypeScript 生成后无差异；最终提交的 GitHub push / PR CI 结果、run 链接见本轮 verification 附件，不使用 S3 或旧提交结果。

完整目录见 [sales-v0.8-s4-tree.txt](sales-v0.8-s4-tree.txt)。最终 SHA、最新 CI run 与开发服务状态记录在本轮 verification 附件，避免提交内自引用 SHA。

## Unresolved issues / architecture decisions requiring review

- S5 的 C05 出库过期幂等完整矩阵、可选开发销售 seed、全阶段验收与最终交付仍待处理；PR #4 保持草稿，当前不建议销售版本标签，不合并或发布。
- 不确定提交暂存在当前页面内存。正常导航会被提示或阻止；若强制关闭页面或浏览器崩溃，重新登录后应先核对服务器单据。跨重启恢复待确认提交需要独立持久化设计，本轮未引入本地敏感业务缓存。
- 库存三值来自一次查询快照；履约数量采用原有父订单读取锁。库存还会受其他订单影响，不能把页面数量当作后续过账保证。
- 保持 ADR0014：全量占用、退货不补发、原成本回收、严格末笔整单冲销、闭单后不冲销出库、全退有效成交保留。未发现与权威上下文冲突的新业务语义。
- 列表与资料搜索有页数和输入上限，没有生产容量结论；旧快照没有原出库头 ID 时仍通过过滤后的成交历史查找来源。
- TanStack Table 与 React Compiler 的兼容性提示、既有 Vite 配置提示记录为工具链提示，不影响本次检查；资金结算、应收应付和 AI 业务工具未进入本增量。
