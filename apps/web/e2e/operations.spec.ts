import {
  expect,
  test as base,
  type Page,
  type Locator,
} from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";

type Identity = { id: string; code: string };
const fixture = (...args: string[]) =>
  execFileSync(
    "uv",
    [
      "run",
      "--project",
      "../api",
      "python",
      path.resolve("../api/tests/browser_operations_fixture.py"),
      ...args,
    ],
    { encoding: "utf8" },
  );
const test = base.extend<{ operationsIdentity: Identity }>({
  operationsIdentity: async ({}, provideIdentity) => {
    const identity = JSON.parse(fixture("create")) as Identity;
    try {
      await provideIdentity(identity);
    } finally {
      fixture("cleanup", identity.id);
    }
  },
});

async function login(page: Page, identity: Identity, role = "admin") {
  await page.goto("/login");
  await page.getByLabel("企业代码", { exact: true }).fill(identity.code);
  await page
    .getByLabel("邮箱", { exact: true })
    .fill(role + "@operations-browser.example.test");
  await page
    .getByLabel("密码", { exact: true })
    .fill("operations-browser-fixture-only-4827");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("button", { name: "切换导航", exact: true })).toBeVisible();
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
async function fact(region: Locator, label: string, value: string | RegExp) {
  const field = region
    .locator("dl > div")
    .filter({ has: region.page().getByText(label, { exact: true }) });
  await expect(field.locator("dd")).toHaveText(value);
}
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}

test("real worker imports partially, retries only failed row, survives lost confirmation and refresh", async ({
  page,
  operationsIdentity,
}) => {
  test.setTimeout(240000);
  fixture("block-row", operationsIdentity.id);
  await login(page, operationsIdentity);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "资料导入", exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "上传资料", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "上传并预览资料" });
  await dialog.getByLabel("上传资料类型").selectOption("brands");
  await dialog.getByLabel("资料导入模式").selectOption("CREATE_ONLY");
  await dialog.getByLabel("资料Excel文件").setInputFiles({
    name: "品牌导入验收.xlsx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from(fixture("workbook").trim(), "base64"),
  });
  await dialog.getByRole("button", { name: "生成导入预览" }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/imports\?batch=/);
  const batchId = new URL(page.url()).searchParams.get("batch")!;
  const detail = page.getByRole("region", { name: "导入批次详情" });
  await expect(detail).toContainText("预览通过，待确认");
  await fact(detail, "全部行", "2");
  await noOverflow(page);
  await detail
    .getByRole("button", { name: "确认执行导入", exact: true })
    .click();
  dialog = page.getByRole("dialog", { name: "确认导入批次" });
  const attempts: { key: string | undefined; body: string | null }[] = [];
  await page.route(
    `**/api/v1/catalog-imports/${batchId}/confirm`,
    async (route) => {
      attempts.push({
        key: route.request().headers()["idempotency-key"],
        body: route.request().postData(),
      });
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      if (attempts.length === 1) await route.abort("failed");
      else await route.fulfill({ response });
    },
  );
  await dialog
    .getByRole("button", { name: "确认执行", exact: true })
    .dblclick();
  await expect(dialog.getByRole("alert")).toContainText("提交结果待确认");
  expect(attempts).toHaveLength(1);
  const beforeBack = page.url();
  await page.goBack();
  await expect(page).toHaveURL(beforeBack);
  await dialog.getByRole("button", { name: "重试原提交" }).click();
  await expect(dialog).toBeHidden();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  await page.unroute(`**/api/v1/catalog-imports/${batchId}/confirm`);
  await expect(detail).toContainText("部分失败", { timeout: 90000 });
  await fact(detail, "成功行", "1");
  await fact(detail, "失败行", "1");
  await expect(
    detail.getByRole("checkbox", { name: "重试第2行", exact: true }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("checkbox", { name: "重试第3行", exact: true }),
  ).toBeEnabled();
  const firstRows = await get(page, `catalog-imports/${batchId}/rows`);
  const successfulId = firstRows.items.find(
    (row: { status: string }) => row.status === "SUCCEEDED",
  ).target_id;
  const downloadPromise = page.waitForEvent("download");
  await detail.getByRole("button", { name: "下载失败行", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
  const downloaded = await readFile((await download.path())!);
  expect(downloaded.subarray(0, 2).toString()).toBe("PK");
  fixture("release-row", operationsIdentity.id);
  await detail
    .getByRole("checkbox", { name: "重试第3行", exact: true })
    .check();
  await detail
    .getByRole("button", { name: "重试所选失败行", exact: true })
    .click();
  dialog = page.getByRole("dialog", { name: "重试失败行" });
  await dialog.getByRole("button", { name: "确认执行", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(detail).toContainText("全部完成", { timeout: 90000 });
  await page.reload();
  await expect(detail).toContainText("全部完成");
  await fact(detail, "成功行", "2");
  await expect(detail.getByRole("checkbox")).toHaveCount(0);
  const finalRows = await get(page, `catalog-imports/${batchId}/rows`);
  expect(
    finalRows.items.find((row: { row_no: number }) => row.row_no === 2)
      .target_id,
  ).toBe(successfulId);
  expect(
    finalRows.items.every(
      (row: { status: string }) => row.status === "SUCCEEDED",
    ),
  ).toBe(true);
  expect((await get(page, "brands?q=IMPORT-")).total).toBe(2);
  await detail
    .getByRole("button", { name: "查看已保存资料", exact: true })
    .first()
    .click();
  dialog = page.getByRole("dialog", { name: "已保存的资料" });
  await expect(dialog).toContainText("浏览器导入品牌一");
  await noOverflow(page);
});

async function commerce(page: Page) {
  const supplier = await create(page, "suppliers", {
    code: "OPS-S",
    name: "运营验收供应商",
  });
  const customer = await create(page, "customers", {
    code: "OPS-C",
    name: "运营验收客户",
  });
  const unit = await create(page, "units", {
    code: "OPS-EACH",
    name: "运营个",
  });
  const category = await create(page, "categories", {
    code: "OPS-CAT",
    name: "运营验收分类",
  });
  const warehouse = await create(page, "warehouses", {
    code: "OPS-W",
    name: "运营验收仓库",
  });
  const product = await create(page, "products", {
    sku: "OPS-BOLT",
    name: "运营验收螺栓",
    category_id: category.id,
    base_unit_id: unit.id,
    min_stock_qty: "10",
    reorder_qty: "20",
    preferred_supplier_id: supplier.id,
  });
  await create(page, "supplier-products", {
    supplier_id: supplier.id,
    product_id: product.id,
    supplier_sku: "OPS-S-BOLT",
    purchase_unit_id: unit.id,
    lead_days: 5,
  });
  const opening = await create(page, "inventory/openings", {
    warehouse_id: warehouse.id,
    reason: "运营验收期初库存",
    lines: [
      {
        product_id: product.id,
        unit_id: unit.id,
        qty: "10",
        input_unit_cost: "11",
      },
    ],
  });
  await create(page, `inventory/documents/${opening.id}/post`, {
    expected_version: opening.version,
  });
  const config = await get(page, "funds/settings");
  await create(page, "funds/activate", {
    business_date: config.business_today,
    reason: "运营验收明确切换",
  });
  const order = await create(page, "sales/orders", {
    customer_id: customer.id,
    warehouse_id: warehouse.id,
    reason: "运营验收销售",
    lines: [
      {
        product_id: product.id,
        unit_id: unit.id,
        qty: "3",
        pricing_mode: "MANUAL",
        unit_price: "15",
      },
    ],
  });
  await create(page, `sales/orders/${order.id}/confirm`, {
    expected_version: order.version,
  });
  const orderDetail = await get(page, `sales/orders/${order.id}`);
  const shipment = await create(page, "sales/shipments", {
    source_id: order.id,
    reason: "运营验收出库",
    lines: [{ source_line_id: orderDetail.lines[0].id, qty: "3" }],
  });
  await create(page, `sales/documents/${shipment.id}/post`, {
    expected_version: shipment.version,
  });
  const source = (await get(page, "funds/sources?side=AR")).items[0];
  const receipt = await create(page, "funds/cash", {
    side: "AR",
    party_id: customer.id,
    kind: "SETTLEMENT",
    business_date: config.business_today,
    method: "CASH",
    reason: "运营验收实收款",
    allocations: [{ source_id: source.id, amount: "20" }],
  });
  const shipmentDetail = await get(page, `sales/documents/${shipment.id}`);
  return {
    supplier,
    customer,
    unit,
    category,
    warehouse,
    product,
    order,
    shipment: shipmentDetail,
    source,
    receipt,
  };
}

test("overview separates period sales and cash from current balances and follows authorized sources at 390px", async ({
  page,
  operationsIdentity,
}) => {
  test.setTimeout(180000);
  await login(page, operationsIdentity);
  const data = await commerce(page);
  await page.goto("/dashboard");
  await page.setViewportSize({ width: 390, height: 844 });
  const sales = page.getByRole("region", { name: "期间销售", exact: true });
  const cash = page.getByRole("region", { name: "期间收款", exact: true });
  const current = page.getByRole("region", { name: "当前应收", exact: true });
  await fact(sales, "净销售额", "45.0000");
  await fact(sales, "实际净成本", "33.0000");
  await fact(sales, "已实现净毛利", "12.0000");
  await fact(cash, "实际收款", "20.0000");
  await fact(current, "尚待收款", "25.0000");
  await fact(
    page.getByRole("region", { name: "当前库存", exact: true }),
    "当前库存估值",
    "77.0000",
  );
  await noOverflow(page);
  await sales.getByText("查看每日销售变化", { exact: true }).click();
  await expect(sales.getByRole("table")).toContainText("45.0000");
  await sales.getByRole("button", { name: "查看来源" }).click();
  let dialog = page.getByRole("dialog", { name: "期间销售来源" });
  await expect(dialog).toContainText("运营验收螺栓");
  await dialog.getByRole("link", { name: "查看销售单据" }).click();
  await expect(page).toHaveURL(new RegExp("document=" + data.shipment.id));
  await expect(
    page.getByRole("region", { name: "销售库存单据详情" }),
  ).toContainText(data.shipment.number);
  await page.goto("/dashboard");
  await cash.getByRole("button", { name: "查看来源" }).click();
  dialog = page.getByRole("dialog", { name: "期间收款与客户退款" });
  await dialog.getByRole("link", { name: "查看收付款" }).click();
  await expect(page).toHaveURL(new RegExp("cash=" + data.receipt.id));
  await expect(page.getByRole("region", { name: "收付款详情" })).toContainText(
    "20.0000",
  );
  await page.goto("/dashboard");
  await page.getByLabel("概览开始日期").fill("2020-01-01");
  await page.getByLabel("概览结束日期").fill("2020-01-02");
  await page.getByRole("button", { name: "更新概览" }).click();
  await fact(sales, "净销售额", /^0(?:\.0{1,4})?$/);
  await fact(current, "尚待收款", "25.0000");
  await current.getByRole("button", { name: "查看来源" }).click();
  dialog = page.getByRole("dialog", { name: "当前应收来源" });
  await expect(dialog).toContainText(data.source.number);
  await dialog.getByRole("link", { name: "查看资金来源" }).click();
  await expect(page).toHaveURL(new RegExp("source=" + data.source.id));
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.waitForURL("**/login");
  await login(page, operationsIdentity, "viewer");
  await page.goto("/dashboard");
  await expect(sales).toBeVisible();
  await fact(sales, "已过账出库单数", "1");
  await expect(sales.getByText("净销售额", { exact: true })).toHaveCount(0);
  await expect(sales.getByText("实际净成本", { exact: true })).toHaveCount(0);
  await expect(cash).toHaveCount(0);
  await expect(current).toHaveCount(0);
  const response = await get(page, "reporting/overview");
  expect(response.sales).not.toHaveProperty("net_sales_amount");
  expect(response).not.toHaveProperty("cash_ar");
  const blocked = await page.request.get(
    "/api/v1/reporting/sources?metric=cash_ar",
  );
  expect(blocked.status()).toBe(403);
  for (const [route, region, message] of [
    ["/imports", "资料导入工作台", "当前没有查看资料导入的权限"],
    ["/replenishment", "补货建议工作台", "没有"],
  ]) {
    await page.goto(route);
    await expect(
      page.getByRole("region", { name: region }).getByRole("alert"),
    ).toContainText(message);
  }
  await noOverflow(page);
});

test("replenishment rechecks changed stock and creates exactly one traceable purchase draft", async ({
  page,
  operationsIdentity,
}) => {
  test.setTimeout(210000);
  await login(page, operationsIdentity);
  const data = await commerce(page);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "补货", exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  const workspace = page.getByRole("region", { name: "补货建议工作台" });
  const row = workspace.getByRole("row").filter({ hasText: "OPS-BOLT" });
  await row.getByRole("button", { name: "查看补货依据" }).click();
  const basis = page.getByRole("dialog", { name: "补货计算依据" });
  await expect(basis).toContainText("运营验收螺栓");
  await noOverflow(page);
  await basis.getByRole("button", { name: "关闭依据" }).click();

  async function review() {
    await row.getByRole("checkbox", { name: "选择补货商品 OPS-BOLT" }).check();
    await workspace.getByRole("button", { name: "复核采购草稿（1）" }).click();
    const dialog = page.getByRole("dialog", { name: "补货采购草稿复核" });
    await dialog
      .getByLabel("补货采购供应商", { exact: true })
      .selectOption(data.supplier.id);
    await dialog
      .getByLabel("补货收货仓库", { exact: true })
      .selectOption(data.warehouse.id);
    await dialog
      .getByLabel("补货采购单位1", { exact: true })
      .selectOption(data.unit.id);
    await dialog
      .getByLabel("补货采购原因", { exact: true })
      .fill("已核对库存与在途的补货草稿");
    return dialog;
  }
  let dialog = await review();
  await dialog
    .getByRole("button", { name: "预览采购草稿", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "确认创建采购草稿" }),
  ).toBeDisabled();
  await dialog.getByLabel("补货采购单价1", { exact: true }).fill("12.3456");
  await dialog
    .getByRole("button", { name: "预览采购草稿", exact: true })
    .click();
  let preview = dialog.getByRole("region", { name: "补货采购预览" });
  await fact(preview, "采购总额", "246.9120");
  await fact(preview, "采购数量", /^20(?:\.0{1,6})?$/);
  await noOverflow(page);
  const opening = await create(page, "inventory/adjustments", {
    warehouse_id: data.warehouse.id,
    reason: "并发到货改变补货依据",
    lines: [
      {
        product_id: data.product.id,
        unit_id: data.unit.id,
        qty: "1",
        input_unit_cost: "11",
      },
    ],
  });
  await create(page, `inventory/documents/${opening.id}/post`, {
    expected_version: opening.version,
  });
  await dialog.getByRole("button", { name: "确认创建采购草稿" }).click();
  await expect(dialog.getByRole("alert")).toContainText("补货依据已变化");
  expect((await get(page, "purchasing/orders")).total).toBe(0);
  await dialog.getByRole("button", { name: "刷新后重新复核" }).click();
  await expect(dialog).toBeHidden();
  await expect(row).toContainText("可用 8.000000");
  dialog = await review();
  await dialog.getByLabel("补货采购单价1", { exact: true }).fill("12.3456");
  await dialog
    .getByRole("button", { name: "预览采购草稿", exact: true })
    .click();
  preview = dialog.getByRole("region", { name: "补货采购预览" });
  await fact(preview, "采购总额", "246.9120");
  const stockBefore = await get(page, "inventory/balances");
  const arBefore = await get(page, "funds/summary?side=AR");
  const apBefore = await get(page, "funds/summary?side=AP");
  const attempts: { key: string | undefined; body: string | null }[] = [];
  let purchaseId = "";
  await page.route("**/api/v1/replenishment/purchase-orders", async (route) => {
    attempts.push({
      key: route.request().headers()["idempotency-key"],
      body: route.request().postData(),
    });
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    purchaseId = (await response.json()).id;
    if (attempts.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await dialog.getByRole("button", { name: "确认创建采购草稿" }).click();
  await expect(dialog.getByRole("alert")).toContainText("提交结果待确认");
  await expect(
    dialog.getByLabel("补货采购单价1", { exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "关闭采购复核" }),
  ).toBeDisabled();
  const current = page.url();
  await page.goBack();
  await expect(page).toHaveURL(current);
  await dialog.getByRole("button", { name: "重试原提交" }).click();
  await expect(dialog).toBeHidden();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  expect((await get(page, "purchasing/orders")).total).toBe(1);
  const purchase = await get(page, `purchasing/orders/${purchaseId}`);
  expect(purchase.status).toBe("DRAFT");
  expect(purchase.amount).toBe("246.9120");
  expect(purchase.lines[0].base_qty).toBe("20.000000");
  expect(await get(page, "inventory/balances")).toEqual(stockBefore);
  expect(await get(page, "funds/summary?side=AR")).toEqual(arBefore);
  expect(await get(page, "funds/summary?side=AP")).toEqual(apBefore);
  await page.unroute("**/api/v1/replenishment/purchase-orders");
  await workspace.getByRole("link", { name: "查看采购草稿" }).click();
  await expect(page).toHaveURL(new RegExp("/purchase\\?order=" + purchaseId));
  const purchaseDetail = page.getByRole("region", { name: "采购订单详情" });
  await expect(purchaseDetail).toContainText(purchase.number);
  await expect(purchaseDetail).toContainText("草稿");
  await page.reload();
  await expect(purchaseDetail).toContainText(purchase.number);
  await noOverflow(page);
});

test("ten thousand rows preview through the same-origin production proxy with bounded pagination", async ({
  page,
  operationsIdentity,
}) => {
  test.setTimeout(120000);
  await login(page, operationsIdentity);
  await page.goto("/imports");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "上传资料", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "上传并预览资料" });
  await dialog.getByLabel("上传资料类型").selectOption("brands");
  await dialog.getByLabel("资料导入模式").selectOption("CREATE_ONLY");
  await dialog.getByLabel("资料Excel文件").setInputFiles({
    name: "万行品牌.xlsx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from(fixture("workbook-large").trim(), "base64"),
  });
  const started = Date.now();
  await dialog.getByRole("button", { name: "生成导入预览" }).click();
  await expect(dialog).toBeHidden({ timeout: 90000 });
  const detail = page.getByRole("region", { name: "导入批次详情" });
  await expect(detail).toContainText("预览通过，待确认");
  await fact(detail, "全部行", "10000");
  const id = new URL(page.url()).searchParams.get("batch")!;
  const first = await get(
    page,
    `catalog-imports/${id}/rows?page=1&page_size=25`,
  );
  const last = await get(
    page,
    `catalog-imports/${id}/rows?page=400&page_size=25`,
  );
  expect(first.total).toBe(10000);
  expect(first.items).toHaveLength(25);
  expect(last.items).toHaveLength(25);
  expect(last.items[24].row_no).toBe(10001);
  expect((await get(page, "brands?q=LARGE-")).total).toBe(0);
  await noOverflow(page);
  console.log(
    JSON.stringify({ sameOriginTenThousandPreviewMs: Date.now() - started }),
  );
});
