import { test, expect } from "@playwright/test";

test("same-origin login, shell, profile, navigation and logout", async ({
  page,
  context,
}) => {
  const apiOrigins = new Set<string>();
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/"))
      apiOrigins.add(new URL(request.url()).origin);
  });
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("企业代码", { exact: true }).fill("DEMO");
  await page
    .getByLabel("邮箱", { exact: true })
    .fill(process.env.SEED_ADMIN_EMAIL!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.SEED_ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  const cookies = await context.cookies();
  expect(
    cookies.find((cookie) => cookie.name === "forge_session")?.httpOnly,
  ).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "forge_session",
  );
  await page.getByRole("link", { name: "查看我的账户" }).click();
  await expect(page.getByRole("heading", { name: "个人信息" })).toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("option", { name: "库存", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "库存", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "库存总览", exact: true }),
  ).toBeVisible();
  expect([...apiOrigins]).toEqual(["http://localhost:3100"]);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/profile");
  await expect(page).toHaveURL(/\/login$/);
});

test("mobile login has no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "登录工作空间" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
