# Forge ERP Purchasing v0.7 交付报告

## Implementation summary

沿用现有单体monorepo、FastAPI/PostgreSQL/InventoryEngine与Next.js架构，已实现采购订单草稿/编辑/确认/取消/关闭、分批收货、采购退货、严格末笔冲销、供应商历史采购价及采购工作台。采购确认不改变库存；只有收货过账增加库存。退货不重开订单待收量。

来源采用既有库存单据新增PURCHASE_RECEIPT/PURCHASE_RETURN，采购附属表用租户复合外键关联来源；不把采购伪装为库存调整。金额由Decimal服务端计算，收货直接传准确总值给库存引擎。Audit/Outbox/幂等随业务原子提交；租户来自认证Runtime Context，四张新表FORCE RLS，应用连接仍为非特权forge_app。

## Final repository tree

完整目录见同目录 `purchasing-v0.7-tree.txt`（排除依赖、密钥、缓存和编译产物）。新增核心路径：

- `apps/api/migrations/versions/0008_purchasing.py/.sql`
- `apps/api/src/forge_erp/modules/purchasing/{domain,application,api}`
- `apps/api/scripts/seed_purchasing.py`
- `apps/api/tests/test_purchasing.py`、`test_purchasing_safety.py`
- `apps/web/features/purchasing/client.ts`、`workspace.tsx`
- `apps/web/tests/purchasing.test.tsx`、`apps/web/e2e/purchasing.spec.ts`
- `docs/purchasing-v0.7.md`、验收清单、ADR0013

## Run commands

从仓库根目录执行；需已有Python3.14/uv、Node24/pnpm11.7、Docker。

```sh
make env install infra migrate seed
make seed-purchasing  # 可选DEMO样例：订单100、已收60、待收40
make api              # 终端1，8100
make web              # 终端2，3100
make worker           # 可选独立终端，Outbox处理
make beat             # 可选独立终端，调度
```

入口 `http://localhost:3100/purchase`，继续使用DEMO企业与原账户密码；配置在本地.env，不把密码写入仓库或报告。已有库只需 `make migrate seed` 后重启应用，勿删卷。可选seed重复运行保留已有采购资料和单据。

```sh
make lint test
make contract
# 契约重新生成后应无差异：
git diff --exit-code -- docs/api/openapi.json apps/web/generated/api/schema.d.ts
pnpm build
pnpm --filter @forge/web exec playwright install --with-deps chromium
pnpm test:e2e
```

本机uv在任务work/bin，执行时可将其加入PATH或使用 `make ... UV=../../work/bin/uv`。Windows/WSL本机浏览器依赖路径仅是当前环境准备，CI用Playwright官方依赖安装。

## Migration status

开发库已由0007升级至 `0008_purchasing`，未修改旧迁移。新增purchase_orders、purchase_order_lines、purchase_documents、purchase_document_lines；库存来源类型扩展、行/已确认内容保护触发器、采购权限和索引。

六种隔离数据库迁移基线全部通过：空库、0001_bootstrap、0004_product_embeddings、0005_inventory、0006_inventory_snapshot、0007_inventory_projection。带数据升级保留既有商品、价格、换算、库存数量/价值和单据事实。迁移不自动授予普通角色采购权限；开发ADMIN由seed补齐权限。

## Test results

- 冻结安装：uv sync --locked、pnpm install --frozen-lockfile通过。
- Ruff、format、Pyright通过，Pyright零错误/警告。
- 后端完整套件132 passed，0 skipped，含真实PostgreSQL/RLS/并发/迁移及已有Catalog、Inventory回归。
- 前端lint/typecheck通过，Vitest12 passed，生产构建通过。
- Playwright Chromium 7 passed，覆盖登录、Catalog、Inventory、采购流程和真实只读账户。
- OpenAPI→TS已重新生成；漂移检查纳入GitHub Actions。
- 草稿 [PR #3](https://github.com/Roco211/AI-ERP/pull/3)，基于Inventory分支。源码92d5743的[源码CI #33966679507](https://github.com/Roco211/AI-ERP/actions/runs/33966679507)全部成功；交付文档提交同样执行完整CI，最终结果在输出报告验收补记中记录。

采购关键实例：2箱×1000基本单位，箱价1.234567，库存入2000个且价值2.4691；100@10与100@12后退50，参考退货金额500、当前库存扣减550；六处注入故障整单回滚，第二行失败前一行也回滚；并发收/退不能超额；提交成功丢响应后原键重试只有一次库存事实。

## Unresolved issues / limits

- 无已知阻断功能验收的问题；交付提交CI继续核验，最新证据记录于输出报告。
- 一订单一供应商一仓，每商品一行，上限200行；不允许超收、不支持已确认订单商业内容修订。
- 退货参考金额和库存成本差额不生成应付、退款或会计凭证；未实现资金、销售、AI业务工具。
- 冲销只允许受影响库存键的末笔整单；不做历史成本重算、部分/级联冲销。关闭订单不自动重开。
- 列表最多100条/页（UI25条），单据详情有行数上限；部分列表仍逐条聚合详情，尚未做生产峰值容量验证。
- TanStack Table的3条React Compiler跳过自动记忆化提示、Vitest现有配置未来加载器提示不影响检查和浏览器通过；不为此升级框架主版本。
- Catalog/Inventory/Purchasing评审分支依赖仍未合并；浏览器验收留下独立标识的开发样例事实，未清删库存流水。

## Architecture decisions requiring review

ADR0013固定P1–P6。需要业务确认的边界：一仓采购与禁止超收；确认后不可修订；收货可显式覆盖采购价；退货不重开待收；退货参考金额按原收货价、库存按当前均价；严格末笔整单冲销；资金后续单独接入。多仓、超收容差、折扣/税费/运费分摊、原批次成本及付款核销均需独立规范。

验收与评审完成后建议标签 `purchasing-v0.7`。本轮未自动创建标签或合并依赖PR。

## 交付复核修正

CI在0349bf5发现共用事务依赖默认在HTTP响应之后提交，导致立即读取偶发404/旧状态。已将Catalog/Inventory（采购复用该入口）事务改为成功响应前提交；提交异常返回500 Problem Details。`test_api_commit_boundary.py` 直接观察ASGI响应与独立数据库连接，四项在旧实现全部复现，修复后通过；最终后端132项。此前失败run33966901630保留用于追溯；修复提交的最终CI另在输出报告记录。
