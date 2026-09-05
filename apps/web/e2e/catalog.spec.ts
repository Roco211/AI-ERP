import { test, expect } from "@playwright/test";

test("catalog forms, Decimal price and keyboard product selection", async ({
  page,
}) => {
  test.setTimeout(90000);
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
    const result = await page.request.post(`/api/v1/${resource}`, {
      data: body,
      headers: {
        Origin: "http://localhost:3100",
        "Idempotency-Key": crypto.randomUUID(),
      },
    });
    expect(result.status(), await result.text()).toBe(201);
    return result.json();
  }
  await page.goto("/settings/categories");
  await page.getByRole("button", { name: "新建分类" }).click();
  await page.getByLabel("编码", { exact: true }).fill(`E2E-${suffix}`);
  await page.getByLabel("名称", { exact: true }).fill(`浏览器分类-${suffix}`);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  const unit = await create("units", {
    code: `U-${suffix}`,
    name: `个-${suffix}`,
  });
  await page.goto("/products");
  await page.getByRole("button", { name: "新建商品", exact: true }).click();
  await page.getByLabel("商品编码", { exact: true }).fill(`E2E-BOLT-${suffix}`);
  await page.getByLabel("商品名称", { exact: true }).fill(`测试螺栓-${suffix}`);
  await page
    .getByLabel("商品分类", { exact: true })
    .selectOption({ label: `浏览器分类-${suffix} · E2E-${suffix}` });
  await page.getByLabel("基础单位", { exact: true }).selectOption(unit.id);
  await page.getByLabel("规格", { exact: true }).fill("M8×30");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.getByLabel("搜索资料").fill(`E2E-BOLT-${suffix}`);
  await expect(
    page.getByRole("cell", { name: `测试螺栓-${suffix}`, exact: true }),
  ).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: `测试螺栓-${suffix}` })
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  await page
    .getByLabel("商品名称", { exact: true })
    .fill(`已编辑螺栓-${suffix}`);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.getByRole("button", { name: "商品价格", exact: true }).click();
  await page.getByRole("button", { name: "新建价格", exact: true }).click();
  await page.getByLabel("搜索商品", { exact: true }).fill(`E2E-BOLT-${suffix}`);
  await page
    .getByLabel("商品", { exact: true })
    .selectOption({ label: `已编辑螺栓-${suffix}` });
  await page.getByLabel("价格类型", { exact: true }).selectOption("standard");
  await page.getByLabel("单价", { exact: true }).fill("1.234567");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: `已编辑螺栓-${suffix}` })
      .getByRole("cell", { name: "1.234567", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "快捷选品", exact: true }).click();
  const search = page.getByRole("combobox", { name: "搜索商品", exact: true });
  await search.fill(`E2E-BOLT-${suffix}`);
  await expect(
    page.getByRole("option", { name: new RegExp(`已编辑螺栓-${suffix}`) }),
  ).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await page.getByLabel("选品数量", { exact: true }).fill("2.5");
  await page.getByRole("button", { name: "确认选择", exact: true }).click();
  await expect(
    page.getByText(/已选择 2.5，合计 2.5000000 基础单位/),
  ).toBeVisible();
});
