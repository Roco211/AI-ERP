# 采购 v0.7 验收清单

状态：33项验收通过；源码92d5743的GitHub CI已全绿。交付文档提交继续执行同一完整CI，最终提交及run记录在输出报告。规范见[purchasing-v0.7.md](purchasing-v0.7.md)。

- [x] A01 基线1105bfa、独立分支、ADR P1–P6；未扩展资金/销售/AI工具。
- [x] A02 冻结栈、Domain/Command/Router边界；只有InventoryEngine写余额。
- [x] A03 新表FORCE RLS、复合外键、非特权forge_app；跨租户读写和缺上下文拒绝。
- [x] A04 不改旧migration/流水；新增采购来源可在库存页追溯；普通库存Command无法绕过采购规则。
- [x] B01 订单保存/编辑/确认均不改库存；确认独立于收货状态。
- [x] B02 草稿版本冲突、失效资料、换算变化拒绝；确认后历史快照冻结。
- [x] B03 确认后API/SQL均不可改删商业内容或增删行。
- [x] B04 取消仅无有效收货；关闭停止后续收货；状态不混入结算信息。
- [x] C01 一次/分批收货、部分/完全状态及待收量准确；退货不重开待收量。
- [x] C02 2箱×1000基本单位、采购箱价的收货金额和库存价值一致。
- [x] C03 明确零价、6位精度、4位金额/总计、非有限/float/越界拒绝。
- [x] C04 两次并发收货不能超收；同单不同键过账只成功一次。
- [x] C05 任一行/流水/余额/Audit/Outbox/幂等故障导致整单回滚并可重试。
- [x] C06 确认订单换算快照用于后续收货；资料停用交叉并发无失效引用。
- [x] D01 退货引用原收货供应商/仓库/单位；跨来源/租户/过量拒绝。
- [x] D02 退货参考金额按原价，库存成本按当前均价，两者可不同且有记录。
- [x] D03 超库存或侵占reserved拒绝；并发累计退货不能超原实收。
- [x] D04 最后一批退完参考金额精确归零；舍入冲突明确报错。
- [x] D05 收货/退货冲销均保留原事实、恢复投影和额度；重复冲销拒绝。
- [x] D06 后续流水/退货依赖拒绝整单冲销；不支持部分/级联或CLOSED自动重开。
- [x] E01 purchase.read、各动作、价格权限分别测试；撤权不借幂等回执绕过。
- [x] E02 无价格权限列表/详情/错误/回执不泄露金额；历史价接口明确授权。
- [x] E03 Audit含实际前后值，Outbox无商业金额；Redis/worker不决定已提交库存。
- [x] E04 历史采购价只来源有效过账收货，按供应商/商品/单位显示可追溯来源。
- [x] F01 真实UI完成订单确认→分批收货→库存追溯→退货/合法冲销。
- [x] F02 ProductPicker键盘、多单位、服务端金额；表单/表格沿用RHF/Zod/TanStack。
- [x] F03 加载/错误/冲突/只读/重复点击/断网原键重试与乱序详情有测试。
- [x] F04 列表分页/筛选有上限；200行限制；移动端和无价格角色浏览器验证。
- [x] G01 空库、0007带数据、增量前一版本可upgrade head，既有价格/库存/RBAC保留。
- [x] G02 冻结安装、Ruff/format/Pyright/pytest及既有库存/Catalog回归通过，无跳过冒充。
- [x] G03 lint/typecheck/Vitest/build/Playwright通过；OpenAPI→TS无漂移。
- [x] G04 最新交付提交GitHub CI全绿，记录commit/run链接。
- [x] G05 可选seed幂等走Command；完整报告/目录/命令/迁移/限制/决策齐备。

## 实际验收证据

基线 `1105bfa`；独立分支 `purchasing/v0.7`。规范提交 `4201613`，后端提交 `efe714b`，工作台与回归提交 `92d5743`。依赖 Inventory PR #2，未自动合并或打标签。

测试简称：P = `apps/api/tests/test_purchasing.py`，S = `apps/api/tests/test_purchasing_safety.py`，M = `apps/api/tests/test_migrations.py`。全部本地后端 **128 passed / 0 skipped**；Vitest **12 passed**；生产构建和真实浏览器 **7 passed**。Ruff/format/Pyright、frontend lint/typecheck 通过；lint 有3条 TanStack Table 的 React Compiler 提示，无错误。

| 验收项 | 实现与实际验证 |
|---|---|
| A01–A02 | AGENTS、ADR0013、独立增量提交；既有 `test_only_inventory_engine_writes_balance_and_domain_has_no_http_dependency` 覆盖新增模块 |
| A03 | 0008四张新表FORCE RLS/租户复合外键；P order_rls；S price_redaction_cross_tenant；平台角色、无上下文和连接池回归 |
| A04 | 0008新增采购来源类型，旧迁移未修改；P receipt_reversal；真实浏览器库存来源跳转采购；普通库存过账/冲销拒绝采购类型 |
| B01、B04 | P order_lifecycle、closed_order；确认无流水/余额；关闭阻止新收货但允许退货，取消/关闭状态规则 |
| B02–B03 | P order_snapshot、order_rls；版本/换算变化拒绝；确认后API及SQL商业字段、行保护 |
| C01、D02 | P partial_receipts；100@10+100@12，退50：参考500、库存成本550、差额-50，待收量不重开 |
| C02、C06 | S box_price_preserves_total：2箱×1000，1.234567/箱，入库2000个、金额2.4691；确认后因子改800仍沿用1000；deactivation_serializes 验证主数据停用与收货并发 |
| C03 | P领域非法数字；S五种inexact/out_of_range参数×数量/单价；zero_price；20,6/20,4限额及精度、明确零价 |
| C04 | P concurrent receipts；S duplicate_post：同一订单70+70/100不能超收；同单不同键只能过账一次 |
| C05 | S faults六处故障注入及原键重试；multiline第二行故障验证前一行一并回滚 |
| D01、F04 | S source_ownership、跨租户/200行/101页大小拒绝；P并发退货；浏览器390像素、真实无价格角色 |
| D03 | S return_preserves_reserved_stock；P concurrent returns，累计70+70/100只有一次成功 |
| D04 | P领域return_tail；S return_rounding_tail：0.3×0.000167，最后退完参考金额精确清零，来源价格不可篡改 |
| D05–D06 | P receipt_reversal、partial_receipts、closed_order；原事实保留、额度恢复、末笔依赖拒绝、退货依赖拒绝 |
| E01–E02 | S八种独立动作/价格权限、撤权幂等重放；列表/详情字段剔除、历史价403、真实只读账户POST403 |
| E03 | S audit_outbox_and_redis_independence：Audit实际DRAFT→POSTED/价值，Outbox仅资源ID/版本；Redis故障仍能过账；重复worker消费无重复流水 |
| E04 | P价格历史只计有效POSTED收货；API供应商/商品过滤；UI显示供应商/商品/原单位和来源收货单，采购行可显式沿用同单位最近价 |
| F01–F02 | Playwright purchasing：开单、键盘选品、60+40分批、断网重试、退10、冲销、来源跳转；复用已验证多单位ProductPicker/RHF/Zod/TanStack；浏览器不算金额 |
| F03 | Vitest purchasing：缺权不请求、无价格、空表单、乱序详情、重复点击/网络原键原body重试、版本冲突；真实浏览器丢弃已成功过账的响应后重试 |
| G01 | M六种基线：空库、0001、0004、0005、0006、0007带数据→0008；旧商品价格/单位/库存数量/价值/单据保留 |
| G02–G03 | `make install`、Ruff/format/Pyright、128后端、12Vitest、lint/typecheck/build、7Playwright；OpenAPI从服务端重新生成，CI检查漂移 |
| G04 | [源码CI #33966679507](https://github.com/Roco211/AI-ERP/actions/runs/33966679507)，源码92d5743，全部步骤成功；[采购分支CI](https://github.com/Roco211/AI-ERP/actions/workflows/ci.yml?query=branch%3Apurchasing%2Fv0.7) |
| G05 | `make seed-purchasing` 两次：首建订单100/收60，第二次保留；全部走业务Command；交付报告、目录树、命令、限制与决策已整理 |

本轮测试中修正了测试夹具的只读密码/清理ID、跨来源实际404预期、第二商品必填规格属性和浏览器选择器；随后相关测试全部重跑。采购库存详情成本查询限定实际RECEIVE/ISSUE流水，避免其他类型流水干扰成本展示。未删除或修改既有库存事实。
