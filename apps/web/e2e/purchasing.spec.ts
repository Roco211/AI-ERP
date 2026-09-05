import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

test("purchase order, split receiving, lost response, return and inventory source", async ({
  page,
}) => {
  test.setTimeout(120000);
  const suffix = Date.now().toString();
  await page.goto("/login");
  await page.getByLabel("企业代码", { exact: true }).fill("DEMO");
  await page
    .getByLabel("邮箱", { exact: true })
    .fill(process.env.SEED_ADMIN_EMAIL!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.SEED_ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  async function create(resource: string, body: unknown) {
    const r = await page.request.post("/api/v1/" + resource, {
      data: body,
      headers: {
        Origin: "http://localhost:3100",
        "Idempotency-Key": crypto.randomUUID(),
      },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
    return r.json();
  }
  const unit = await create("units", {
    code: "PU-" + suffix,
    name: "采购个-" + suffix,
  });
  const category = await create("categories", {
    code: "PC-" + suffix,
    name: "采购测试分类",
  });
  const product = await create("products", {
    sku: "PUR-E2E-" + suffix,
    name: "采购测试螺栓-" + suffix,
    category_id: category.id,
    base_unit_id: unit.id,
  });
  const supplier = await create("suppliers", {
    code: "PS-" + suffix,
    name: "采购供应商-" + suffix,
  });
  const wh = await create("warehouses", {
    code: "PW-" + suffix,
    name: "采购测试仓-" + suffix,
  });
  await page.goto("/purchase");
  await page.getByRole("button", { name: "新建采购订单" }).click();
  await page
    .getByRole("combobox", { name: "采购供应商", exact: true })
    .selectOption(supplier.id);
  await page
    .getByRole("combobox", { name: "收货仓库", exact: true })
    .selectOption(wh.id);
  await page.getByLabel("采购单据原因").fill("采购浏览器验收");
  await page.getByRole("button", { name: "添加采购商品" }).click();
  const search = page.getByRole("combobox", { name: "搜索商品", exact: true });
  await search.fill(product.sku);
  await expect(
    page.getByRole("option", { name: new RegExp(product.sku) }),
  ).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await page.getByLabel("选品数量", { exact: true }).fill("100");
  await page.getByRole("button", { name: "确认选择", exact: true }).click();
  await page.getByLabel("采购单位单价", { exact: true }).fill("10");
  await page.getByRole("button", { name: "保存采购草稿" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  const order = page.getByRole("region", { name: "采购订单详情" });
  const doc = page.getByRole("region", { name: "采购库存单据详情" });
  await expect(order.getByText(/草稿 · 未收货/)).toBeVisible();
  await order.getByRole("button", { name: "确认订单", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认执行" })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(order.getByText(/已确认 · 未收货/)).toBeVisible();
  async function stockPost(lost = false) {
    if (lost)
      await page.route(
        "**/api/v1/purchasing/documents/*/post",
        async (route) => {
          await route.fetch();
          await route.abort("failed");
        },
        { times: 1 },
      );
    await doc.getByRole("button", { name: "过账", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "确认执行" })
      .click();
    if (lost) {
      await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
        "提交结果待确认",
      );
      await page.getByRole("button", { name: "重试原提交" }).click();
    }
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(doc.getByText(/已过账 ·/)).toBeVisible();
  }
  await order.getByRole("button", { name: "创建收货单" }).click();
  await page.getByLabel("采购单据原因").fill("第一批收货");
  await page.getByLabel("采购数量", { exact: true }).fill("60");
  await page.getByRole("button", { name: "保存采购草稿" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await stockPost(true);
  await doc.getByRole("button", { name: /来源订单/ }).click();
  await expect(order.getByText(/已确认 · 部分收货/)).toBeVisible();
  await order.getByRole("button", { name: "创建收货单" }).click();
  await expect(page.getByLabel("采购数量", { exact: true })).toHaveValue("40");
  await page.getByLabel("采购单据原因").fill("第二批收货");
  await page.getByRole("button", { name: "保存采购草稿" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await stockPost();
  await doc.getByRole("button", { name: "创建退货单" }).click();
  await page.getByLabel("采购单据原因").fill("退回多余商品");
  await page.getByLabel("采购数量", { exact: true }).fill("10");
  await expect(
    page.getByLabel("采购单位单价", { exact: true }),
  ).toHaveAttribute("readonly", "");
  await page.getByRole("button", { name: "保存采购草稿" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await stockPost();
  const balances = await page.request.get("/api/v1/inventory/balances", {
    params: { warehouse_id: wh.id },
  });
  expect(Number((await balances.json()).items[0].on_hand_qty)).toBe(90);
  await doc.getByRole("button", { name: "冲销", exact: true }).click();
  await page.getByLabel("采购操作原因").fill("误退货冲回");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认执行" })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(doc.getByText(/已冲销 ·/)).toBeVisible();
  await doc.getByRole("button", { name: /来源订单/ }).click();
  await expect(order.getByText(/已确认 · 已收齐/)).toBeVisible();
  await page.getByRole("button", { name: "历史采购价", exact: true }).click();
  await expect(
    page.getByRole("row").filter({ hasText: product.sku }).first(),
  ).toBeVisible();
  await page.goto("/inventory?tab=movements");
  await page
    .getByRole("row")
    .filter({ hasText: product.sku })
    .first()
    .getByRole("button")
    .click();
  await page.getByRole("link", { name: "前往采购单据" }).click();
  await expect(doc).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.waitForURL("**/login");
});

test("real purchase quantity reader has no price or mutation access", async ({
  page,
}) => {
  const script = path.resolve("../api/tests/browser_readonly_fixture.py");
  const run = (...args: string[]) =>
    execFileSync(
      "uv",
      ["run", "--project", "../api", "python", script, ...args],
      { encoding: "utf8" },
    );
  const identity = JSON.parse(run("create"));
  try {
    await page.goto("/login");
    await page.getByLabel("企业代码", { exact: true }).fill(identity.code);
    await page.getByLabel("邮箱", { exact: true }).fill("viewer@example.test");
    await page
      .getByLabel("密码", { exact: true })
      .fill("browser-fixture-password-8472");
    await page.getByRole("button", { name: "进入工作空间" }).click();
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).toBeVisible();
    await page.goto("/purchase");
    await expect(
      page.getByRole("button", { name: "采购订单", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "订单金额" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "新建采购订单" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "历史采购价" })).toHaveCount(
      0,
    );
    const r = await page.request.post("/api/v1/purchasing/orders", {
      headers: {
        Origin: "http://localhost:3100",
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: {
        supplier_id: crypto.randomUUID(),
        warehouse_id: crypto.randomUUID(),
        reason: "unauthorized",
        lines: [
          {
            product_id: crypto.randomUUID(),
            unit_id: crypto.randomUUID(),
            qty: "1",
            unit_price: "1",
          },
        ],
      },
    });
    expect(r.status()).toBe(403);
    await page.getByRole("button", { name: "退出登录" }).click();
    await page.waitForURL("**/login");
  } finally {
    run("cleanup", identity.id);
  }
});
