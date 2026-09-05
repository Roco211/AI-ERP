# 库存 v0.6 验收清单

状态：63项验收已通过。2026-09-05；源码提交 b6fbe2a 的 GitHub CI 已成功，文档交付提交另以本分支最新 CI 和外部交付报告确认。

规范：[库存 v0.6 施工规范](inventory-v0.6.md)。新增政策：[ADR 0012](adr/0012-inventory-ledger-and-posting.md)。勾选必须附实际提交、命令、测试用例/报告及结果；历史 v0.5 CI、测试夹具或 skipped 项不能证明本阶段完成。D1–D6 已获用户继续实施授权；规则变化时同步更新验收预期。

## A. 范围与架构（I0–I1）

- [x] A01 记录 Catalog 评审/合并基线、库存分支与实际 migration head；保留既有数据与历史。
- [x] A02 ADR D1–D6 有明确结论，未定细节不伪装为原规范；不提前建设采购、销售、资金、AI 业务工具。
- [x] A03 冻结技术栈、模块化单体及现有认证/事务基础保持；没有新增禁用基础设施。
- [x] A04 Domain 不依赖 FastAPI；Router 无业务计算；全部库存写入经过 Command/InventoryEngine；代码边界检查覆盖其他模块直接更新余额。
- [x] A05 新租户表 ENABLE + FORCE RLS、复合租户外键、唯一约束和必要索引齐备；forge_app 非 owner/superuser/BYPASSRLS。

## B. 账本、单位与成本（I1）

- [x] B01 每次现有量/占用变化都有不可变事实；forge_app 直接 UPDATE/DELETE/TRUNCATE 流水被拒绝，触发器防改写测试通过。
- [x] B02 现有量、占用、库存金额及占用明细均能从流水对账；流水 sequence 明确，不依赖时间排序猜测先后；历史业务日期不能回溯改变成本序列。
- [x] B03 数据库拒绝负现有量、负占用及 reserved > on_hand；API 返回明确领域错误。
- [x] B04 基本单位统一计算；1 箱=1000 个、输入 2 箱，保存 2000 个及原单位/因子/版本快照。
- [x] B05 草稿换算变化需重新确认；过账历史不受换算/商品名称变更影响；停用后的原快照仍可查询及合法冲销。
- [x] B06 float、非有限值、越界、不可表示的小数及负向非法输入被拒绝；API 数值输出为字符串。
- [x] B07 入库 100@10，再入库 100@12 → 数量 200、金额 2200、均价 11；出库 50 → 成本 550、余额数量 150、金额 1650、均价仍 11。
- [x] B08 全量出库库存金额精确归零；尾差有据可查；再次入库均价正确，不沿用空库存历史成本。
- [x] B09 零成本、微量数量、六位成本舍入、部分出库金额边界与 NUMERIC 上限有明确预期及自动测试，无负金额或隐式改值。
- [x] B10 Hypothesis 生成合法操作序列，每步校验数量/占用/金额/重放结果一致及不变量；不是仅重复几组固定样例。

## C. 占用、并发与事务（I1–I3）

- [x] C01 现有 100，占用 60 → 现有仍 100、占用 60、可用 40；金额及均价不变。
- [x] C02 消费上述占用出库 20 → 现有 80、占用 40、可用 40；再释放 10 → 占用 30、可用 50。
- [x] C03 超额占用/释放/消费、使用别的来源占用均拒绝；无占用出库不得动用已占用数量；没有通用手工占用 API。
- [x] C04 现有 10，并发出库两次各 7，只有一次成功；失败事务不留流水、余额变更或成功幂等响应。
- [x] C05 同一库存键首次创建余额/首次入库并发安全，唯一一条余额且流水无丢失。
- [x] C06 多商品双向调拨遵循同一锁序，验证无丢失更新和预期内的锁等待/重试；不同库存键不被组织总锁串行化。
- [x] C07 过账与主数据停用/换算变更的交叉并发有明确结果，无失效引用新过账；首次入库和停用不能同时错误成功。
- [x] C08 故障分别注入流水写后、余额更新后、单据更新后、Audit/Outbox/幂等保存时，整笔事务回滚且可安全重试。
- [x] C09 同 key 同请求重放原回执；同 key 不同请求返回 IDEMPOTENCY_KEY_REUSED；不同单据操作不串响应。
- [x] C10 不同 key 同时过账同单、幂等记录过期后重复过账，都不会重复扣增库存；数据库唯一约束与状态检查生效。
- [x] C11 请求权限撤销后不能借幂等重放执行操作或读旧成本；超时后的原 key 重试不重复过账。

## D. 单据操作（I2–I4）

- [x] D01 保存/编辑草稿不改变库存；expected_version 过期拒绝；单据不接受 generic status PATCH。
- [x] D02 期初按基本单位成本入账，不能覆盖已有有效业务；并发期初最多一个成功；零成本必须显式确认。
- [x] D03 调整原因必填；增加按确认成本入账，减少按当前成本并保护占用；不支持直接覆盖余额或伪造应付。
- [x] D04 POSTED 内容和行不能经 API/直接 SQL 修改删除，也不能追加行；只有合法冲销更新限定状态元数据。
- [x] D05 无后续依赖的整单冲销产生关联反向流水，恢复操作前数量/金额/均价，原事实不变；重复冲销拒绝。
- [x] D06 原单有后续流水时按已评审政策拒绝，包括后续单据已冲销的情形；双仓任何一端有依赖时调拨整单不能部分冲销。
- [x] D07 调拨源仓减少与目标仓增加的数量、金额相抵；目标均价正确；跨租户/同仓调拨拒绝。
- [x] D08 调拨任一行或任一仓失败整单回滚；占用不随调拨迁移。
- [x] D09 盘点未填写数量不等于零，未选商品不被影响；有基准、实盘、差异、原因和成本确认记录。
- [x] D10 盘点期间入出库/占用引起基准变化，过账整单拒绝 STOCKTAKE_STALE；刷新不静默覆盖实盘数据，需重新确认。
- [x] D11 盘盈明确成本，盘亏遵守当前成本，实盘少于已占用量不能自动释放占用或过账。
- [x] D12 全零差异盘点可过账并留审计，不伪造数量流水；混合差异只对真实变化写流水。

## E. 安全、可观测性与后台（所有增量）

- [x] E01 使用真实 PostgreSQL 18 的 forge_app 测试跨租户 SELECT/INSERT/UPDATE、关联外键、无租户上下文及连接池租户切换，均无数据泄漏。
- [x] E02 API 拒绝客户端 organization_id，跨租户 ID 返回不泄露存在性的错误；worker/维护命令显式设置租户。
- [x] E03 数量只读、成本读取、各业务动作、冲销、对账的权限分别测试；仅隐藏按钮不算通过。
- [x] E04 无 product.cost.read 时，详情、列表、流水、错误、Audit 可读入口及幂等回执均不泄露成本；日志/Outbox 仅包含必要元数据。
- [x] E05 关键变更 Audit 记录 actor/source/资源/前后变化/request ID；Outbox 与业务同事务，库存事件处理器可重试且去重。
- [x] E06 Redis/worker 暂停时业务过账仍原子提交；恢复后 Outbox 继续处理，重复消费不重复改变库存。
- [x] E07 Cookie/Origin、Problem Details、request ID、structured logging 与登录/退出回归通过；不记录凭据或完整敏感请求。

## F. 工作台与用户流程（I2–I5）

- [x] F01 库存页面按商品/仓库显示现有量、占用、可用量；成本按权限呈现；余额可追溯到流水及来源单据。
- [x] F02 键盘 ProductPicker 可选商品/单位/数量并进入单据；数量和正式金额由服务端验证，没有 float 权威计算。
- [x] F03 表单覆盖输入错误、保存、版本冲突、过账确认、重复点击、网络结果不明及原 key 重试；POSTED 页面只读。
- [x] F04 Playwright 实际完成期初→查看余额/流水→合法冲销，以及调整、调拨、盘点成功/冲突场景；包含无成本权限和移动端核心路径。
- [x] F05 Vitest 验证关键表单/状态交互；真实浏览器与真实 API/数据库流程不能全由 mock 替代。
- [x] F06 低库存按已评审商品级口径，覆盖跨仓汇总、无余额、零阈值、占用影响；不虚构采购建议或销量。
- [x] F07 分页/筛选/稳定排序及页数上限正确，流水并发新增时翻页不重复漏读；超出单据行数上限明确拒绝。
- [x] F08 记录代表性测试数据规模、查询计划、数据库/API/整页耗时和设备条件；发现全表无界加载则修复，不把少量样例测试称为生产容量保证。

## G. 对账、迁移、CI 与交付（I5）

- [x] G01 一致快照对账报告包含水位及差异；在独立测试数据库损坏余额/占用投影后能发现，dry-run 不修改数据。
- [x] G02 经授权维护重建可恢复数量、金额、平均成本和占用明细；范围外租户/库存键不变；原流水不变，修复留审计。
- [x] G03 重建与普通写入互斥或同锁协调；无权限、范围缺失、维护条件不满足时拒绝实修复。
- [x] G04 空库、Bootstrap 0001、带数据 Catalog 0004、库存前一增量均可升级到实际 head；原资料/权限/流水保留，merged migrations 未改写。
- [x] G05 uv.lock、pnpm-lock.yaml 通过冻结安装；Ruff、format check、Pyright、pytest 全过，库存相关测试无跳过冒充通过。
- [x] G06 pnpm lint、typecheck、Vitest、production build、Playwright 全过，Bootstrap/Catalog/本地搜索回归无退化。
- [x] G07 OpenAPI→TypeScript 可重复生成且无 drift；没有手工编辑生成文件。
- [x] G08 GitHub Actions 纳入 inventory/** 分支并保留 PR 检查，实际交付提交的 CI 全绿，报告附 commit/run 链接。
- [x] G09 可选库存开发 seed 幂等、通过业务 Command、不覆盖用户已改资料、不含生产秘密；运行和维护说明与真实命令一致。
- [x] G10 完整交付报告包含 implementation summary、final repository tree、run commands、migration status、test results、unresolved issues、architecture decisions requiring review；全部满足后才建议 inventory-v0.6 标签。

## 实际证据（2026-09-05）

Catalog 基线 `b049055`，PR #1 未合并；独立库存分支 `inventory/v0.6`，草稿 [PR #2](https://github.com/Roco211/AI-ERP/pull/2) 以 Catalog 分支为 base。增量提交：I0 `af08bc4`；I1 `779e9b7`；I2–I4 `d88c387`；修复与验收 `08fb992`；I5 工作台 `7f621ee`。最终源代码提交与实际 CI run 见交付报告；CI 不以旧 Catalog run 替代。

测试文件简称：E = `apps/api/tests/test_inventory_engine.py`，D = `test_inventory_documents.py`，S = `test_inventory_safety.py`（同目录）。以下用例均已运行，不是未来测试计划。

| 验收 ID | 对应实现 / 测试证据 | 结果及边界 |
|---|---|---|
| A01–A03 | Git 增量；AGENTS；ADR 0012；migration tests | 沿用 Catalog；未合并依赖 PR；未建采购/销售/资金/AI 工具 |
| A04 | S `test_only_inventory_engine_writes_balance_and_domain_has_no_http_dependency`；Router/Command 分层检查 | 唯一余额写入口与领域边界通过 |
| A05、B01、E01 | E `test_inventory_rls_and_immutability`；平台 RLS/role/pool；0005 SQL 与 migration tests | forge_app；跨租户读写、复合引用和不可变保护通过 |
| B02、G01–G03 | E live ledger；S rebuild、repair invalidates stocktake、rebuild serializes | 独立测试数据库里损坏投影并修复；dry-run 不写；原流水不改；修复后旧盘点拒绝 |
| B03、C01–C03、D11 | E database rejects invalid balance、exact reservation scenario、cost/precision | 数据库三种非法余额均拒绝；100→占60→消费20→释放10；盘点不自动释放占用 |
| B04–B06、C07 | S exact box、unit version、historical snapshot、conversion change waits、deactivation waits | 2箱→2000；版本变更拒绝旧草稿；历史快照不随改名/停用改变；共享锁保护交叉写 |
| B07–B09 | E cost and precision；D cost adjustment | 100@10+100@12、出50；全出归零、重入成本、零成本、微额与溢出边界 |
| B10 | E `test_generated_cost_and_reservation_sequences`（Hypothesis） | 生成合法入库/占用/释放/出库序列；逐步独立累加数量和金额校验，不以固定样例替代 |
| C04–C06 | E real concurrent issue and first row；S opposing transfer and independent stock locks | 并发10出7仅一次成功；首次余额唯一；双向多商品调拨、不同键并行 |
| C08、E05–E06 | S post faults（6处）、post does not require redis、low stock/outbox recovery、audit keeps before after | 流水、余额、单据、Audit、Outbox、幂等注入故障原子回滚；Redis故障不阻止过账 |
| C09–C11 | D opening/idempotency、concurrent same document；S old idempotency；平台 execute_once；Playwright lost response | 原键重放、异载荷冲突、过期记录与不同键重复过账、撤权和响应丢失重试 |
| D01–D03 | D opening、cost adjustment；S unit version/validation；领域与 Command 分支验证 | 草稿零库存影响；显式成本/原因；期初保护与状态/版本检查 |
| D04 | D document security and immutable content | 直接 SQL 改内容/删行/追加行均拒绝；追加行确认触发器报错，而非仅撞唯一键 |
| D05–D08 | D opening reverse、older reversal rejected、transfer conservation；S transfer target failure | 原事实保留；严格末笔整单冲销；双仓金额相抵；目标失败无半张调拨 |
| D09–D12 | D stocktake baseline refresh and zero difference；E reservation protection；S repair invalidates stocktake | 刷新保留实盘数；基准变化拒绝；零差异不伪造流水；低于占用拒绝 |
| E02–E04 | S no context/cross tenant、each document action requires its own permission（4类）、old idempotency；D cost redaction；维护权限 | 不接受客户端租户；数量只读实际403；成本字段在详情/流水/余额剔除；没有开放 Audit 读取 API |
| E07 | `test_platform.py`、登录/Vitest、Bootstrap Playwright | Cookie/Origin、request ID、Problem Details、日志和退出回归通过 |
| F01–F05 | `apps/web/e2e/inventory.spec.ts`；`tests/inventory.test.tsx`；`tests/picker.test.tsx` | 真实页面+API+数据库：期初、余额、流水来源、调拨冲销、盘点冲突重核、只读登录与移动端；Vitest 6项通过 |
| F06–F07 | S low stock scope；D stable pagination；S pending transaction excluded；schemas limits | 跨仓汇总/无余额/零阈值/占用；分页快照排除后提交事务；页与行数有界 |
| F08 | `docs/evidence/inventory-v0.6-benchmark.json`、`inventory-v0.6-browser-measurement.json` | 500商品/余额/流水；真实200行过账；查询计划；独立记录API/整页测量；不是容量承诺 |
| G04 | `test_migrations.py` 五种基线 → `0007_inventory_projection` | 空库、0001、带数据0004/0005/0006；资料价格换算与流水保留；生产式应用角色测试 |
| G05–G07 | `make install lint test contract`、build、Playwright；交付报告执行结果 | 冻结安装、Ruff/format/Pyright/TS通过；契约生成无差异；无 skipped 冒充通过 |
| G08 | [库存分支 CI](https://github.com/Roco211/AI-ERP/actions/workflows/ci.yml?query=branch%3Ainventory%2Fv0.6) | 源码 `b6fbe2a` 的 [PR CI #33957296487](https://github.com/Roco211/AI-ERP/actions/runs/33957296487) 全部成功；文档提交仍执行同一完整流水线 |
| G09–G10 | README、seed_inventory.py、reconcile_inventory.py、`inventory-v0.6-delivery.md` | 独立演示期初经Command入账；重复seed已验证；报告、树、命令及限制齐备 |

本地结果：在全新临时 PostgreSQL 数据库升级后，完整后端 **84 passed / 0 skipped**；随后补充的权限/历史快照测试所在 S 文件 **26 passed / 0 skipped**（新增6项，最终套件共90项，90项完整后端测试已在GitHub CI通过）。Vitest **6 passed**，Playwright **5 passed**。详情乱序响应的Vitest回归已通过；一次PR浏览器检查暴露详情刷新时序问题，已修复；完整5项浏览器流程通过，库存流程又连续3轮共6项通过。最初的 Outbox 全租户测试断言受演示积压影响，已改为验证目标租户的首次处理/重复不处理；直接SQL追加行测试的错误码预期改为实际约束码23514，并验证触发器消息，重新通过。

无界明细加载只允许单个受200行上限约束的单据；余额、流水、列表均服务端分页。性能环境：WSL2 Linux，AMD Ryzen 5 5600X，12逻辑CPU，15GiB内存，PostgreSQL18本机连接，生产Next构建；测试为本机暖缓存、合成500条及小型DEMO数据，未验证生产峰值并发。TanStack Table 触发2条 React Compiler 跳过自动记忆化的 lint warning；无 lint error，不影响本轮浏览器流程。
