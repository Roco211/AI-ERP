import {
  expect,
  test as base,
  type Page,
  type Locator,
} from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

type Identity = { id: string; code: string };
const runFixture = (...args: string[]) =>
  execFileSync(
    "uv",
    [
      "run",
      "--project",
      "../api",
      "python",
      path.resolve("../api/tests/browser_funds_fixture.py"),
      ...args,
    ],
    { encoding: "utf8" },
  );
const test = base.extend<{ fundsIdentity: Identity }>({
  fundsIdentity: async ({}, provideIdentity) => {
    const identity = JSON.parse(runFixture("create")) as Identity;
    try {
      await provideIdentity(identity);
    } finally {
      runFixture("cleanup", identity.id);
    }
  },
});

async function login(page: Page, identity: Identity, role = "admin") {
  await page.goto("/login");
  await page.getByLabel("企业代码", { exact: true }).fill(identity.code);
  await page
    .getByLabel("邮箱", { exact: true })
    .fill(role + "@funds-browser.example.test");
  await page
    .getByLabel("密码", { exact: true })
    .fill("funds-browser-fixture-only-9638");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
}
async function logout(page: Page) {
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.waitForURL("**/login");
}
async function create(page: Page, resource: string, body: unknown) {
  const response = await page.request.post("/api/v1/" + resource, {
    data: body,
    headers: {
      Origin: "http://localhost:3100",
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function get(page: Page, resource: string) {
  const response = await page.request.get("/api/v1/" + resource);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function setup(page: Page) {
  const customer = await create(page, "customers", {
    code: "F-CUSTOMER",
    name: "资金验收客户",
  });
  const supplier = await create(page, "suppliers", {
    code: "F-SUPPLIER",
    name: "资金验收供应商",
  });
  const unit = await create(page, "units", { code: "F-EACH", name: "资金个" });
  const category = await create(page, "categories", {
    code: "F-CATEGORY",
    name: "资金验收分类",
  });
  const product = await create(page, "products", {
    sku: "F-BOLT",
    name: "资金验收螺栓",
    category_id: category.id,
    base_unit_id: unit.id,
  });
  const warehouse = await create(page, "warehouses", {
    code: "F-WAREHOUSE",
    name: "资金验收仓库",
  });
  await create(page, "product-prices", {
    product_id: product.id,
    price_type: "standard",
    price: "15",
  });
  const opening = await create(page, "inventory/openings", {
    warehouse_id: warehouse.id,
    reason: "资金独立验收库存",
    lines: [
      {
        product_id: product.id,
        unit_id: unit.id,
        qty: "200",
        input_unit_cost: "11",
      },
    ],
  });
  await create(page, `inventory/documents/${opening.id}/post`, {
    expected_version: opening.version,
  });
  return { customer, supplier, unit, category, product, warehouse };
}
async function salesOrder(
  page: Page,
  data: Awaited<ReturnType<typeof setup>>,
  qty: string,
) {
  const draft = await create(page, "sales/orders", {
    customer_id: data.customer.id,
    warehouse_id: data.warehouse.id,
    reason: "资金验收销售",
    lines: [
      {
        product_id: data.product.id,
        unit_id: data.unit.id,
        qty,
        pricing_mode: "AUTO",
      },
    ],
  });
  await create(page, `sales/orders/${draft.id}/confirm`, {
    expected_version: draft.version,
  });
  return get(page, `sales/orders/${draft.id}`);
}
async function shipment(
  page: Page,
  order: { id: string; lines: { id: string }[] },
  qty: string,
) {
  const draft = await create(page, "sales/shipments", {
    source_id: order.id,
    reason: "资金验收出库",
    lines: [{ source_line_id: order.lines[0].id, qty }],
  });
  await create(page, `sales/documents/${draft.id}/post`, {
    expected_version: draft.version,
  });
  return get(page, `sales/documents/${draft.id}`);
}
async function salesReturn(
  page: Page,
  original: { id: string; lines: { id: string }[] },
  qty: string,
) {
  const draft = await create(page, "sales/returns", {
    source_id: original.id,
    reason: "资金验收退货",
    lines: [{ source_line_id: original.lines[0].id, qty }],
  });
  await create(page, `sales/documents/${draft.id}/post`, {
    expected_version: draft.version,
  });
  return draft;
}
async function activateUI(page: Page) {
  await page.goto("/finance?side=AR");
  await expect(page).toHaveURL(/\/funds\?side=AR/);
  await page.getByRole("button", { name: "启用资金管理", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("资金操作说明").fill("期初已核对，独立企业切换");
  await dialog.getByRole("button", { name: "确认启用" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText(/资金管理已启用/)).toBeVisible();
}
async function recordUI(
  page: Page,
  action: string,
  partyId: string,
  allocations: { number: string; amount: string }[],
  expectedAmount: string,
) {
  await page
    .getByRole("button", { name: `登记${action}`, exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "资金往来对象" })
    .selectOption(partyId);
  await dialog.getByLabel("资金操作说明").fill(`已核实实际${action}`);
  for (const line of allocations) {
    await dialog
      .getByRole("checkbox", { name: `选择来源 ${line.number}`, exact: true })
      .check();
    await dialog
      .getByLabel(`核销金额 ${line.number}`, { exact: true })
      .fill(line.amount);
  }
  await dialog.getByRole("button", { name: "预览核销金额" }).click();
  await expect(
    dialog.getByRole("region", { name: "收付款金额预览" }),
  ).toContainText(`本次${action}总额：${expectedAmount}`);
  return dialog;
}
async function confirmCash(page: Page, dialog: Locator, action: string) {
  const response = page.waitForResponse(
    (item) =>
      item.request().method() === "POST" &&
      item.url().endsWith("/api/v1/funds/cash"),
  );
  await dialog
    .getByRole("button", { name: `确认${action}`, exact: true })
    .click();
  const completed = await response;
  expect(completed.ok(), await completed.text()).toBeTruthy();
  const receipt = await completed.json();
  await expect(dialog).toBeHidden();
  return receipt;
}
async function reverseCashUI(page: Page) {
  await page
    .getByRole("region", { name: "收付款详情" })
    .getByRole("button", { name: "冲销收付款" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("资金操作说明").fill("误登记的退款冲销");
  await dialog.getByRole("button", { name: "确认冲销" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("region", { name: "收付款详情" })).toContainText(
    "已冲销",
  );
}

test("funds activation, multiple receivables, partial/full customer refunds, reversal and historical zero binding", async ({
  page,
  fundsIdentity,
}) => {
  test.setTimeout(240000);
  await login(page, fundsIdentity);
  const data = await setup(page);
  const oldOrder = await salesOrder(page, data, "2");
  const oldShipment = await shipment(page, oldOrder, "2");
  await activateUI(page);
  expect((await get(page, "funds/sources?side=AR")).total).toBe(0);
  const order = await salesOrder(page, data, "10");
  const first = await shipment(page, order, "6");
  const second = await shipment(page, order, "4");
  const sources = (await get(page, "funds/sources?side=AR")).items;
  const one = sources.find(
    (item: { source_document_id: string }) =>
      item.source_document_id === first.id,
  );
  const two = sources.find(
    (item: { source_document_id: string }) =>
      item.source_document_id === second.id,
  );
  expect(one.settlement_amount).toBe("90.0000");
  expect(two.settlement_amount).toBe("60.0000");
  await page.goto("/funds?side=AR");
  let dialog = await recordUI(
    page,
    "收款",
    data.customer.id,
    [
      { number: one.number, amount: "50" },
      { number: two.number, amount: "20" },
    ],
    "70.0000",
  );
  const paid = await confirmCash(page, dialog, "收款");
  const cash = await get(page, `funds/cash/${paid.id}`);
  expect(cash.amount).toBe("70.0000");
  expect(cash.allocations).toHaveLength(2);
  await salesReturn(page, first, "6");
  expect(await get(page, `funds/sources/${one.id}`)).toMatchObject({
    settlement_amount: expect.stringMatching(/^0(?:\.0{1,4})?$/),
    refund_amount: "50.0000",
  });
  await page.goto("/funds?side=AR");
  dialog = await recordUI(
    page,
    "客户退款",
    data.customer.id,
    [{ number: one.number, amount: "20" }],
    "20.0000",
  );
  await confirmCash(page, dialog, "客户退款");
  expect((await get(page, `funds/sources/${one.id}`)).refund_amount).toBe(
    "30.0000",
  );
  await reverseCashUI(page);
  expect((await get(page, `funds/sources/${one.id}`)).refund_amount).toBe(
    "50.0000",
  );
  await page.goto("/funds?side=AR");
  dialog = await recordUI(
    page,
    "客户退款",
    data.customer.id,
    [{ number: one.number, amount: "50" }],
    "50.0000",
  );
  await confirmCash(page, dialog, "客户退款");
  expect((await get(page, `funds/sources/${one.id}`)).refund_amount).toMatch(
    /^0(?:\.0{1,4})?$/,
  );
  await page
    .getByRole("region", { name: "收付款详情" })
    .getByRole("button", { name: "查看来源" })
    .click();
  await page
    .getByRole("region", { name: "资金来源详情" })
    .getByRole("link", { name: "查看原业务单据" })
    .click();
  await expect(
    page.getByRole("region", { name: "销售库存单据详情" }),
  ).toContainText(first.number);
  await page.goto(`/sales?order=${order.id}`);
  await expect(
    page.getByRole("region", { name: "订单资金结算概览" }),
  ).toContainText("40.0000");
  await page
    .getByRole("region", { name: "订单资金结算概览" })
    .getByRole("link", { name: "查看资金往来" })
    .click();
  await expect(page).toHaveURL(new RegExp("party=" + data.customer.id));
  await page.getByRole("tab", { name: "历史单据绑定" }).click();
  await page
    .getByRole("row")
    .filter({ hasText: oldShipment.number })
    .getByRole("button", { name: "绑定期初" })
    .click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("历史未结金额")).toHaveValue("0.0000");
  await dialog
    .getByLabel("资金操作说明")
    .fill("切换前该单已经结清，明确零欠款");
  await dialog.getByRole("button", { name: "确认绑定" }).click();
  await expect(dialog).toBeHidden();
  await salesReturn(page, oldShipment, "2");
  const mapped = (await get(page, "funds/sources?side=AR")).items.find(
    (item: { source_document_id: string }) =>
      item.source_document_id === oldShipment.id,
  );
  expect(mapped.refund_amount).toBe("30.0000");
});

test("payables use receipt commercial value and support opening, payment, supplier refund and adjustment reversal", async ({
  page,
  fundsIdentity,
}) => {
  test.setTimeout(210000);
  await login(page, fundsIdentity);
  const data = await setup(page);
  await activateUI(page);
  const order = await create(page, "purchasing/orders", {
    supplier_id: data.supplier.id,
    warehouse_id: data.warehouse.id,
    reason: "资金验收采购",
    lines: [
      {
        product_id: data.product.id,
        unit_id: data.unit.id,
        qty: "50",
        unit_price: "5",
      },
    ],
  });
  await create(page, `purchasing/orders/${order.id}/confirm`, {
    expected_version: order.version,
  });
  const confirmed = await get(page, `purchasing/orders/${order.id}`);
  const receipt = await create(page, "purchasing/receipts", {
    source_id: order.id,
    reason: "资金验收入库",
    lines: [{ source_line_id: confirmed.lines[0].id, qty: "50" }],
  });
  await create(page, `purchasing/documents/${receipt.id}/post`, {
    expected_version: receipt.version,
  });
  const receiptDetail = await get(page, `purchasing/documents/${receipt.id}`);
  const source = (await get(page, "funds/sources?side=AP")).items[0];
  expect(source.commercial_amount).toBe("250.0000");
  await page.goto("/funds?side=AP");
  let dialog = await recordUI(
    page,
    "付款",
    data.supplier.id,
    [{ number: source.number, amount: "100" }],
    "100.0000",
  );
  await confirmCash(page, dialog, "付款");
  const returned = await create(page, "purchasing/returns", {
    source_id: receipt.id,
    reason: "采购全退核对退款",
    lines: [{ source_line_id: receiptDetail.lines[0].id, qty: "50" }],
  });
  await create(page, `purchasing/documents/${returned.id}/post`, {
    expected_version: returned.version,
  });
  expect(await get(page, `funds/sources/${source.id}`)).toMatchObject({
    settlement_amount: expect.stringMatching(/^0(?:\.0{1,4})?$/),
    refund_amount: "100.0000",
  });
  await page.goto("/funds?side=AP");
  dialog = await recordUI(
    page,
    "供应商退款",
    data.supplier.id,
    [{ number: source.number, amount: "100" }],
    "100.0000",
  );
  await confirmCash(page, dialog, "供应商退款");
  expect((await get(page, `funds/sources/${source.id}`)).balance).toMatch(
    /^0(?:\.0{1,4})?$/,
  );
  await page.goto("/purchase");
  await page
    .getByRole("row")
    .filter({ hasText: order.number })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "订单资金结算概览" }),
  ).toContainText("已结清");
  await page.goto("/funds?side=AP");
  await page.getByRole("button", { name: "录入期初往来", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "资金往来对象" })
    .selectOption(data.supplier.id);
  await dialog.getByLabel("资金往来金额").fill("12.1234");
  await dialog.getByLabel("资金操作说明").fill("核实旧应付期初");
  await dialog.getByRole("button", { name: "保存资金记录" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("region", { name: "资金来源详情" }),
  ).toContainText("12.1234");
  await page.getByRole("button", { name: "登记往来调整", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "资金往来对象" })
    .selectOption(data.supplier.id);
  await dialog.getByLabel("资金往来金额").fill("-2.1234");
  await dialog.getByLabel("资金操作说明").fill("已核对的期初退款修正");
  await dialog
    .getByRole("checkbox", { name: "我已核实负数金额对应真实待退款余额" })
    .check();
  await dialog.getByRole("button", { name: "保存资金记录" }).click();
  await expect(dialog).toBeHidden();
  await page
    .getByRole("region", { name: "资金来源详情" })
    .getByRole("button", { name: "冲销来源" })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("资金操作说明").fill("误填的调整予以冲销");
  await dialog.getByRole("button", { name: "确认冲销" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("region", { name: "资金来源详情" }),
  ).toContainText("已冲销");
});

test("isolated funds roles and lost receipt preserve one cash fact across Back at 390px", async ({
  page,
  fundsIdentity,
}) => {
  test.setTimeout(240000);
  await login(page, fundsIdentity);
  const data = await setup(page);
  await create(page, "funds/activate", {
    business_date: (await get(page, "funds/settings")).business_today,
    reason: "角色验收切换",
  });
  const ar = await create(page, "funds/openings", {
    side: "AR",
    party_id: data.customer.id,
    amount: "100",
    reason: "角色验收应收",
  });
  await create(page, "funds/openings", {
    side: "AP",
    party_id: data.supplier.id,
    amount: "200",
    reason: "角色验收应付",
  });
  const source = await get(page, `funds/sources/${ar.id}`);
  await logout(page);
  for (const [role, side, blockedSide] of [
    ["ar_reader", "AR", "AP"],
    ["ap_reader", "AP", "AR"],
  ]) {
    await login(page, fundsIdentity, role);
    await page.goto(`/funds?side=${side}`);
    await expect(
      page.getByRole("tab", { name: side === "AR" ? "应收来源" : "应付来源" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "登记收款", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "登记付款", exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await page.request.get(`/api/v1/funds/summary?side=${blockedSide}`)
      ).status(),
    ).toBe(403);
    await logout(page);
  }
  await login(page, fundsIdentity, "cashier");
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "资金", exact: true })
    .click();
  await expect(page.getByRole("button", { name: "应付与付款" })).toHaveCount(0);
  const dialog = await recordUI(
    page,
    "收款",
    data.customer.id,
    [{ number: source.number, amount: "25.1234" }],
    "25.1234",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const attempts: { key: string | undefined; body: string | null }[] = [];
  await page.route("**/api/v1/funds/cash", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    attempts.push({
      key: route.request().headers()["idempotency-key"],
      body: route.request().postData(),
    });
    const response = await route.fetch();
    if (attempts.length === 1) {
      expect(response.ok()).toBeTruthy();
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await dialog.getByRole("button", { name: "确认收款" }).click();
  await expect(dialog.getByRole("alert")).toContainText("提交结果待确认");
  await expect(dialog.getByLabel("资金操作说明")).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "取消", exact: true }),
  ).toBeDisabled();
  const current = page.url();
  const historyLength = await page.evaluate(() => history.length);
  await page.goBack();
  await expect(page).toHaveURL(current);
  expect(await page.evaluate(() => history.length)).toBe(historyLength);
  await expect(
    dialog.getByRole("button", { name: "重试原提交" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "重试原提交" }).click();
  await expect(dialog).toBeHidden();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  expect((await get(page, "funds/cash?side=AR")).total).toBe(1);
  expect((await get(page, `funds/sources/${ar.id}`)).settlement_amount).toBe(
    "74.8766",
  );
  await expect(
    page.getByRole("button", { name: "冲销收付款", exact: true }),
  ).toHaveCount(0);
  const payment = await page.request.post("/api/v1/funds/cash", {
    data: {
      side: "AP",
      party_id: data.supplier.id,
      kind: "SETTLEMENT",
      business_date: (await get(page, "funds/settings")).business_today,
      method: "CASH",
      reason: "越权付款应拒绝",
      allocations: [{ source_id: ar.id, amount: "1" }],
    },
    headers: {
      Origin: "http://localhost:3100",
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  expect(payment.status()).toBe(403);
  await page.unroute("**/api/v1/funds/cash");
  await logout(page);
  await login(page, fundsIdentity, "no_funds");
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/funds/")) requests.push(request.url());
  });
  await page.goto("/funds");
  await expect(
    page.getByRole("region", { name: "资金工作台" }).getByRole("alert"),
  ).toContainText("没有查看资金");
  expect(requests).toHaveLength(0);
});
