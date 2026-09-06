import { test, expect } from "@playwright/test";
test("inventory opening, adjustment, transfer, stale stocktake and reversal", async ({
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
  async function create(path: string, body: unknown) {
    const r = await page.request.post("/api/v1/" + path, {
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
    code: "IU-" + suffix,
    name: "个-" + suffix,
  });
  const category = await create("categories", {
    code: "IC-" + suffix,
    name: "库存测试分类-" + suffix,
  });
  const product = await create("products", {
    sku: "INV-E2E-" + suffix,
    name: "库存测试螺栓-" + suffix,
    base_unit_id: unit.id,
    category_id: category.id,
  });
  const wh = await create("warehouses", {
    code: "IW-" + suffix,
    name: "测试主仓-" + suffix,
  });
  const target = await create("warehouses", {
    code: "IT-" + suffix,
    name: "测试分仓-" + suffix,
  });
  await page.goto("/inventory");
  await page.getByRole("tab", { name: "期初库存", exact: true }).click();
  await page.getByRole("button", { name: "新建期初库存" }).click();
  await page
    .getByRole("combobox", { name: "单据仓库", exact: true })
    .selectOption(wh.id);
  await page.getByLabel("单据原因").fill("浏览器期初验收");
  await page.getByRole("button", { name: "添加商品" }).click();
  const search = page.getByRole("combobox", { name: "搜索商品", exact: true });
  await search.fill(product.sku);
  await expect(
    page.getByRole("option", { name: new RegExp(product.sku) }),
  ).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await page.getByLabel("选品数量", { exact: true }).fill("100");
  await page.getByRole("button", { name: "确认选择", exact: true }).click();
  await page.getByLabel("基本单位成本", { exact: true }).fill("10");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  const detail = page.getByRole("region", { name: "库存单据详情" });
  await expect(detail.getByText(/草稿 ·/)).toBeVisible();
  async function postDetail(simulateLostResponse = false) {
    if (simulateLostResponse) {
      await page.route(
        "**/api/v1/inventory/documents/*/post",
        async (route) => {
          await route.fetch();
          await route.abort("failed");
        },
        { times: 1 },
      );
    }
    await detail.getByRole("button", { name: "过账", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "确认过账", exact: true })
      .click();
    if (simulateLostResponse) {
      await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
        "提交结果待确认",
      );
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "重试原提交", exact: true })
        .click();
    }
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(detail.getByText(/已过账 ·/)).toBeVisible();
  }
  await postDetail(true);
  await page.getByRole("tab", { name: "库存总览", exact: true }).click();
  await page.getByLabel("搜索库存商品").fill(product.sku);
  const balanceRow = page.getByRole("row").filter({ hasText: product.name });
  await expect(
    balanceRow.getByRole("cell", { name: "100", exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "库存流水", exact: true }).click();
  await page.getByRole("row").filter({ hasText: product.sku }).getByRole("button").click();
  await expect(detail.getByText(/已过账 ·/)).toBeVisible();
  const line = {
    product_id: product.id,
    unit_id: unit.id,
    qty: "5",
    input_unit_cost: "12",
  };
  const adjust = await create("inventory/adjustments", {
    warehouse_id: wh.id,
    reason: "浏览器调整-" + suffix,
    lines: [line],
  });
  await page.getByRole("tab", { name: "库存调整", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "浏览器调整-" + suffix })
    .getByRole("button", { name: "查看" })
    .click();
  await postDetail();
  const transfer = await create("inventory/transfers", {
    warehouse_id: wh.id,
    target_warehouse_id: target.id,
    reason: "浏览器调拨-" + suffix,
    lines: [{ ...line, qty: "10" }],
  });
  await page.getByRole("tab", { name: "仓库调拨", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "浏览器调拨-" + suffix })
    .getByRole("button", { name: "查看" })
    .click();
  await postDetail();
  await detail.getByRole("button", { name: "冲销", exact: true }).click();
  await page.getByLabel("冲销原因").fill("浏览器整单冲销");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认冲销", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(detail.getByText(/已冲销 ·/)).toBeVisible();
  const count = await create("inventory/stocktakes", {
    warehouse_id: wh.id,
    reason: "浏览器盘点-" + suffix,
    lines: [{ ...line, qty: "103" }],
  });
  const change = await create("inventory/adjustments", {
    warehouse_id: wh.id,
    reason: "盘点期间变动",
    lines: [{ ...line, qty: "1" }],
  });
  await create("inventory/documents/" + change.id + "/post", {
    expected_version: change.version,
  });
  await page.getByRole("tab", { name: "库存盘点", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "浏览器盘点-" + suffix })
    .getByRole("button", { name: "查看" })
    .click();
  await detail.getByRole("button", { name: "过账", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认过账", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "盘点期间库存已变化",
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  await detail.getByRole("button", { name: "刷新盘点基准" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认刷新" })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await postDetail();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "库存流水", exact: true }).click();
  await page.getByLabel("搜索仓库", { exact: true }).fill(wh.code);
  await page.getByLabel("筛选仓库", { exact: true }).selectOption(wh.id);
  await expect(
    page.getByRole("cell", { name: "盘点", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /STO-/ }).first(),
  ).toBeVisible();
  const finalStock = await page.request.get("/api/v1/inventory/balances", {
    params: { warehouse_id: wh.id, product_id: product.id },
  });
  expect((await finalStock.json()).items[0].on_hand_qty).toBe("103.000000");
  expect(adjust.id && transfer.id && count.id).toBeTruthy();
});

test("real quantity-only user cannot see costs or create inventory documents", async ({
  page,
}) => {
  const { execFileSync } = await import("node:child_process");
  const { resolve } = await import("node:path");
  const script = resolve("../api/tests/browser_readonly_fixture.py");
  const run = (args: string[]) =>
    execFileSync(
      "uv",
      ["run", "--project", "../api", "python", script, ...args],
      { encoding: "utf8", timeout: 30000 },
    );
  const fixture = JSON.parse(run(["create"]));
  try {
    await page.goto("/login");
    await page.getByLabel("企业代码", { exact: true }).fill(fixture.code);
    await page.getByLabel("邮箱", { exact: true }).fill("viewer@example.test");
    await page
      .getByLabel("密码", { exact: true })
      .fill("browser-fixture-password-8472");
    await page.getByRole("button", { name: "进入工作空间" }).click();
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).toBeVisible();
    await page.goto("/inventory");
    await expect(
      page.getByRole("columnheader", { name: "可用量", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "库存金额", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("tab", { name: "期初库存", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "新建期初库存" }),
    ).toHaveCount(0);
    const response = await page.request.post("/api/v1/inventory/openings", {
      headers: {
        Origin: "http://localhost:3100",
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: {
        warehouse_id: crypto.randomUUID(),
        reason: "unauthorized",
        lines: [
          {
            product_id: crypto.randomUUID(),
            unit_id: crypto.randomUUID(),
            qty: "1",
            input_unit_cost: "1",
          },
        ],
      },
    });
    expect(response.status()).toBe(403);
    await page.getByRole("button", { name: "退出登录" }).click();
    await page.waitForURL("**/login");
  } finally {
    run(["cleanup", fixture.id]);
  }
});
