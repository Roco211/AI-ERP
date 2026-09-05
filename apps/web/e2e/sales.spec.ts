import { expect, test as base, type Page } from "@playwright/test";
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
      path.resolve("../api/tests/browser_sales_fixture.py"),
      ...args,
    ],
    { encoding: "utf8" },
  );
const test = base.extend<{ salesIdentity: Identity }>({
  salesIdentity: async ({}, provideIdentity) => {
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
    .fill(role + "@sales-browser.example.test");
  await page
    .getByLabel("密码", { exact: true })
    .fill("sales-browser-fixture-only-8472");
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
async function setup(page: Page) {
  const unit = await create(page, "units", { code: "EACH", name: "销售个" });
  const box = await create(page, "units", { code: "BOX", name: "销售盒" });
  const category = await create(page, "categories", {
    code: "BOLT",
    name: "销售验收分类",
  });
  const product = await create(page, "products", {
    sku: "SALES-E2E-BOLT",
    name: "销售验收螺栓",
    category_id: category.id,
    base_unit_id: unit.id,
  });
  await create(page, "product-units", {
    product_id: product.id,
    unit_id: box.id,
    unit_to_base_factor: "10",
  });
  await create(page, "product-prices", {
    product_id: product.id,
    price_type: "standard",
    price: "15",
  });
  const customer = await create(page, "customers", {
    code: "C001",
    name: "销售验收客户",
  });
  const wh = await create(page, "warehouses", {
    code: "MAIN",
    name: "销售验收仓库",
  });
  const opening = await create(page, "inventory/openings", {
    warehouse_id: wh.id,
    reason: "独立销售浏览器测试期初",
    lines: [
      {
        product_id: product.id,
        unit_id: unit.id,
        qty: "200",
        input_unit_cost: "11",
      },
    ],
  });
  await create(page, "inventory/documents/" + opening.id + "/post", {
    expected_version: opening.version,
  });
  return { unit, box, product, customer, wh };
}
async function seedOrder(
  page: Page,
  data: Awaited<ReturnType<typeof setup>>,
  confirmed = false,
) {
  let order = await create(page, "sales/orders", {
    customer_id: data.customer.id,
    warehouse_id: data.wh.id,
    reason: "权限浏览器测试订单",
    lines: [
      {
        product_id: data.product.id,
        unit_id: data.unit.id,
        qty: "10",
        pricing_mode: "AUTO",
      },
    ],
  });
  if (confirmed)
    order = await create(page, "sales/orders/" + order.id + "/confirm", {
      expected_version: order.version,
    });
  return order;
}
async function saveDraft(
  page: Page,
  resource: "orders" | "shipments" | "returns",
) {
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith("/api/v1/sales/" + resource),
  );
  await page.getByRole("button", { name: "保存销售草稿" }).click();
  const saved = await response;
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const receipt = await saved.json();
  await expect(page.getByRole("dialog")).toBeHidden();
  return receipt;
}
async function confirmAction(page: Page, action: string, reason?: string) {
  await page
    .getByRole("region", {
      name: action === "确认订单" ? "销售订单详情" : "销售库存单据详情",
    })
    .getByRole("button", { name: action, exact: true })
    .click();
  if (reason) await page.getByLabel("销售操作原因").fill(reason);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认执行" })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
}
async function readOrder(page: Page, id: string) {
  const response = await page.request.get("/api/v1/sales/orders/" + id);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
const privateFields = new Set([
  "unit_price",
  "amount",
  "price_source",
  "actual_cost",
  "return_cost",
  "returned_cost",
  "gross_margin",
  "shipment_cost",
  "net_cost",
  "net_sales_amount",
  "shipment_amount",
  "return_amount",
  "returned_amount",
]);
function expectNoFinancialFields(value: unknown) {
  if (Array.isArray(value)) {
    for (const item of value) expectNoFinancialFields(item);
  } else if (value && typeof value === "object")
    for (const [key, item] of Object.entries(value)) {
      expect(privateFields.has(key), "unexpected financial field " + key).toBe(
        false,
      );
      expectNoFinancialFields(item);
    }
}

test("isolated sales keyboard order, partial shipment lost response, return, reversal, history and sources", async ({
  page,
  salesIdentity,
}) => {
  test.setTimeout(180000);
  await login(page, salesIdentity);
  const data = await setup(page);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "销售", exact: true })
    .click();
  await page.waitForURL("**/sales*");
  await page.getByRole("button", { name: "新建销售订单" }).click();
  await page
    .getByRole("combobox", { name: "销售客户", exact: true })
    .selectOption(data.customer.id);
  await page
    .getByRole("combobox", { name: "出库仓库", exact: true })
    .selectOption(data.wh.id);
  await page.getByLabel("销售单据原因").fill("键盘开单与原单退货验收");
  await page.getByRole("button", { name: "添加销售商品" }).click();
  const search = page.getByRole("combobox", { name: "搜索商品", exact: true });
  await search.fill(data.product.sku);
  await expect(
    page.getByRole("option", { name: new RegExp(data.product.sku) }),
  ).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await page.getByLabel("选品单位", { exact: true }).selectOption(data.box.id);
  await page.getByLabel("选品数量", { exact: true }).fill("10");
  await page.getByRole("button", { name: "确认选择", exact: true }).click();
  await expect(page.getByLabel("销售单位单价", { exact: true })).toHaveValue(
    "150.000000",
  );
  await expect(page.getByText("价格来源：标准价")).toBeVisible();
  const saved = await saveDraft(page, "orders");
  const order = page.getByRole("region", { name: "销售订单详情" });
  const doc = page.getByRole("region", { name: "销售库存单据详情" });
  await expect(order.getByText(/草稿 · 未出库/)).toBeVisible();
  expect(await readOrder(page, saved.id)).toMatchObject({
    amount: "1500.0000",
    lines: [
      {
        qty: "10.000000",
        base_qty: "100.000000",
        unit_price: "150.000000",
        unit_to_base_factor: "10.000000",
      },
    ],
  });
  await confirmAction(page, "确认订单");
  await expect(order.getByText(/已确认 · 未出库/)).toBeVisible();
  expect((await readOrder(page, saved.id)).lines[0]).toMatchObject({
    reserved_base_qty: "100.000000",
    executable_qty: "10.000000",
  });

  await order.getByRole("button", { name: "创建出库单" }).click();
  await expect(page.getByLabel("销售数量", { exact: true })).toHaveValue(
    "10.000000",
  );
  await page.getByLabel("销售数量", { exact: true }).fill("6");
  await page.getByLabel("销售单据原因").fill("先发六盒");
  const shipment = await saveDraft(page, "shipments");
  const shipmentNumber = (
    await (
      await page.request.get("/api/v1/sales/documents/" + shipment.id)
    ).json()
  ).number;
  const attempts: { key: string | undefined; body: string | null }[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/sales/documents/" + shipment.id + "/post")
    )
      attempts.push({
        key: request.headers()["idempotency-key"],
        body: request.postData(),
      });
  });
  await page.route(
    "**/api/v1/sales/documents/" + shipment.id + "/post",
    async (route) => {
      const committed = await route.fetch();
      expect(committed.ok()).toBeTruthy();
      await route.abort("failed");
    },
    { times: 1 },
  );
  await doc.getByRole("button", { name: "过账", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认执行" })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "提交结果待确认",
  );
  await expect(page.getByRole("button", { name: "确认执行" })).toHaveCount(0);
  await expect(
    page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }),
  ).toBeDisabled();
  const pendingUrl = page.url();
  const historyLength = await page.evaluate(() => window.history.length);
  await page.goBack();
  await expect(page).toHaveURL(pendingUrl);
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "提交结果待确认",
  );
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);
  expect(attempts).toHaveLength(1);
  await page.getByRole("button", { name: "重试原提交" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(doc.getByText(/已过账 ·/)).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  expect(attempts[0].key).toBeTruthy();
  const movementResponse = await page.request.get(
    "/api/v1/inventory/movements",
    { params: { document_id: shipment.id } },
  );
  expect((await movementResponse.json()).items).toHaveLength(1);
  expect(await readOrder(page, saved.id)).toMatchObject({
    fulfillment_status: "PARTIAL",
    net_sales_amount: "900.0000",
    net_cost: "660.0000",
    gross_margin: "240.0000",
  });

  // The blocked Back did not replace, append, or discard any history entry.
  await page.goBack();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.goForward();
  await expect(page).toHaveURL(pendingUrl);
  await expect(doc.getByText(/已过账 ·/)).toBeVisible();
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);

  await doc.getByRole("button", { name: "创建退货单" }).click();
  await page.getByLabel("销售数量", { exact: true }).fill("1");
  await page.getByLabel("销售单据原因").fill("退回一盒");
  const returned = await saveDraft(page, "returns");
  await confirmAction(page, "过账");
  await expect(doc.getByText(/已过账 ·/)).toBeVisible();
  expect(await readOrder(page, saved.id)).toMatchObject({
    fulfillment_status: "PARTIAL",
    net_sales_amount: "750.0000",
    net_cost: "550.0000",
    gross_margin: "200.0000",
    lines: [
      {
        reserved_base_qty: "40.000000",
        shipped_base_qty: "60.000000",
        returned_base_qty: "10.000000",
      },
    ],
  });
  await confirmAction(page, "冲销", "误记退货，按原事实冲回");
  await expect(doc.getByText(/已冲销 ·/)).toBeVisible();
  expect(await readOrder(page, saved.id)).toMatchObject({
    gross_margin: "240.0000",
    lines: [
      {
        returned_base_qty: expect.stringMatching(/^0(?:\.0+)?$/),
        reserved_base_qty: "40.000000",
      },
    ],
  });
  await doc.getByRole("button", { name: "原销售出库单", exact: true }).click();
  await expect(doc.getByRole("heading")).toContainText("销售出库");
  await doc.getByRole("button", { name: /来源订单/ }).click();
  await expect(order.getByText(/已确认 · 部分出库/)).toBeVisible();
  await expect(
    order.getByText("已实现净毛利：240", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "客户成交历史", exact: true }).click();
  await expect(
    page.getByRole("row").filter({ hasText: data.product.sku }),
  ).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: data.product.sku })
    .getByRole("button")
    .click();
  await expect(doc.getByRole("heading")).toContainText("销售出库");
  await doc.getByRole("link", { name: "查看库存流水" }).click();
  await page.getByRole("link", { name: shipmentNumber, exact: true }).click();
  await expect(doc.getByRole("heading")).toContainText(shipmentNumber);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const returnedDoc = await page.request.get(
    "/api/v1/sales/documents/" + returned.id,
  );
  expect((await returnedDoc.json()).status).toBe("REVERSED");
  await logout(page);
});

test("real seller sees sales prices and can confirm while inventory cost and shipping stay forbidden", async ({
  page,
  salesIdentity,
}) => {
  test.setTimeout(120000);
  await login(page, salesIdentity);
  const data = await setup(page);
  const saved = await seedOrder(page, data);
  await logout(page);
  await login(page, salesIdentity, "seller");
  await page.goto("/sales");
  await expect(
    page.getByRole("columnheader", { name: "订单金额", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看", exact: true }).click();
  const order = page.getByRole("region", { name: "销售订单详情" });
  await expect(order).toBeVisible();
  await expect(order.getByText(/净毛利|库存成本|出库成本|净成本/)).toHaveCount(
    0,
  );
  const detail = await readOrder(page, saved.id);
  expect(detail.amount).toBe("150.0000");
  for (const field of [
    "gross_margin",
    "shipment_cost",
    "return_cost",
    "net_cost",
  ])
    expect(detail).not.toHaveProperty(field);
  await confirmAction(page, "确认订单");
  await expect(order.getByText(/已确认 · 未出库/)).toBeVisible();
  await expect(order.getByRole("button", { name: "创建出库单" })).toHaveCount(
    0,
  );
  const denied = await page.request.post("/api/v1/sales/shipments", {
    headers: {
      Origin: "http://localhost:3100",
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      source_id: saved.id,
      reason: "无权出库",
      lines: [{ source_line_id: detail.lines[0].id, qty: "1" }],
    },
  });
  expect(denied.status()).toBe(403);
  await logout(page);
});

test("real quantity warehouse performs shipment and return without receiving prices or costs", async ({
  page,
  salesIdentity,
}) => {
  test.setTimeout(150000);
  await login(page, salesIdentity);
  const data = await setup(page);
  const saved = await seedOrder(page, data, true);
  await logout(page);
  await login(page, salesIdentity, "warehouse");
  await page.goto("/sales");
  await expect(
    page.getByRole("columnheader", { name: "订单金额", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建销售订单" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "客户成交历史" })).toHaveCount(
    0,
  );
  expectNoFinancialFields(await readOrder(page, saved.id));
  await page.getByRole("button", { name: "查看", exact: true }).click();
  const order = page.getByRole("region", { name: "销售订单详情" });
  const doc = page.getByRole("region", { name: "销售库存单据详情" });
  await order.getByRole("button", { name: "创建出库单" }).click();
  await expect(page.getByLabel("销售单位单价", { exact: true })).toHaveCount(0);
  await page.getByLabel("销售数量", { exact: true }).fill("4");
  await page.getByLabel("销售单据原因").fill("仓管只看数量出库");
  const shipment = await saveDraft(page, "shipments");
  await confirmAction(page, "过账");
  await doc.getByRole("button", { name: "创建退货单" }).click();
  await page.getByLabel("销售数量", { exact: true }).fill("1");
  await page.getByLabel("销售单据原因").fill("仓管只看数量退货");
  const returned = await saveDraft(page, "returns");
  await confirmAction(page, "过账");
  const detail = await page.request.get(
    "/api/v1/sales/documents/" + returned.id,
  );
  expectNoFinancialFields(await detail.json());
  await confirmAction(page, "冲销", "仓管纠正数量录入");
  await expect(doc.getByText(/已冲销 ·/)).toBeVisible();
  const deniedQuote = await page.request.get("/api/v1/sales/price-quote", {
    params: {
      customer_id: data.customer.id,
      product_id: data.product.id,
      unit_id: data.unit.id,
    },
  });
  expect(deniedQuote.status()).toBe(403);
  const deniedOrder = await page.request.post("/api/v1/sales/orders", {
    headers: {
      Origin: "http://localhost:3100",
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      customer_id: data.customer.id,
      warehouse_id: data.wh.id,
      reason: "不能越权开单",
      lines: [
        {
          product_id: data.product.id,
          unit_id: data.unit.id,
          qty: "1",
          pricing_mode: "AUTO",
        },
      ],
    },
  });
  expect(deniedOrder.status()).toBe(403);
  const shipmentDetail = await page.request.get(
    "/api/v1/sales/documents/" + shipment.id,
  );
  expectNoFinancialFields(await shipmentDetail.json());
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await logout(page);
});

test("same-document Forward preserves an uncertain shipment and retries the original request", async ({
  page,
  salesIdentity,
}) => {
  test.setTimeout(120000);
  await login(page, salesIdentity);
  const data = await setup(page);
  const order = await seedOrder(page, data, true);
  const detail = await readOrder(page, order.id);
  const shipment = await create(page, "sales/shipments", {
    source_id: order.id,
    reason: "前进导航保护验收",
    lines: [{ source_line_id: detail.lines[0].id, qty: "6" }],
  });
  const dashboardUrl = page.url();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "销售", exact: true })
    .click();
  await page.getByRole("button", { name: "销售出库", exact: true }).click();
  await page.getByRole("button", { name: "查看", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "销售库存单据详情" }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "库存", exact: true })
    .click();
  await page.waitForURL("**/inventory*");
  const inventoryUrl = page.url();
  await page.goBack();
  const doc = page.getByRole("region", { name: "销售库存单据详情" });
  await expect(doc).toBeVisible();
  const requests: { key: string | undefined; body: string | null }[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/sales/documents/" + shipment.id + "/post")
    )
      requests.push({
        key: request.headers()["idempotency-key"],
        body: request.postData(),
      });
  });
  await page.route(
    "**/api/v1/sales/documents/" + shipment.id + "/post",
    async (route) => {
      const committed = await route.fetch();
      expect(committed.ok()).toBeTruthy();
      await route.abort("failed");
    },
    { times: 1 },
  );
  await doc.getByRole("button", { name: "过账", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认执行" })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "提交结果待确认",
  );
  const pendingUrl = page.url();
  const historyLength = await page.evaluate(() => window.history.length);
  await page.goForward();
  await expect(page).toHaveURL(pendingUrl);
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "提交结果待确认",
  );
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);
  expect(requests).toHaveLength(1);
  await page.getByRole("button", { name: "重试原提交" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  const movements = await page.request.get("/api/v1/inventory/movements", {
    params: { document_id: shipment.id },
  });
  expect((await movements.json()).items).toHaveLength(1);
  // Forward was blocked without appending a duplicate sales entry or truncating
  // the original forward entry; the exact original order works after receipt.
  await page.goBack();
  await expect(page).toHaveURL(dashboardUrl);
  await page.goForward();
  await expect(page).toHaveURL(pendingUrl);
  await expect(doc.getByText(/已过账 ·/)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(inventoryUrl);
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);
});
