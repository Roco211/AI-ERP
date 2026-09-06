import { expect, test as base, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import type { components } from "../generated/api/schema";

type Identity = { id: string; code: string };
const fixture = (...args: string[]) => execFileSync("uv", [
  "run", "--project", "../api", "python",
  path.resolve("../api/tests/browser_assistant_fixture.py"), ...args,
], { encoding: "utf8" });

// Reuse the existing disposable browser identity. These presentation checks do
// not alter the demo company's records or call a configured model service.
const test = base.extend<{ beuiIdentity: Identity }>({
  page: async ({ page }, providePage) => {
    const errors: string[] = [];
    const record = (error: Error) => errors.push(error.message);
    page.on("pageerror", record);
    try { await providePage(page); }
    finally {
      page.off("pageerror", record);
      expect(errors, "UI interactions must not produce uncaught browser errors").toEqual([]);
    }
  },
  beuiIdentity: async ({}, provideIdentity) => {
    const identity = JSON.parse(fixture("create")) as Identity;
    try { await provideIdentity(identity); }
    finally { fixture("cleanup", identity.id); }
  },
});

async function login(page: Page, identity: Identity) {
  await page.goto("/login");
  await page.getByLabel("企业代码", { exact: true }).fill(identity.code);
  await page.getByLabel("邮箱", { exact: true }).fill("admin@assistant-browser.example.test");
  await page.getByLabel("密码", { exact: true }).fill("assistant-browser-fixture-only-4827");
  await page.getByRole("button", { name: "进入工作空间", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("button", { name: "切换导航", exact: true })).toBeVisible();
}

async function focusWithin(container: Locator) {
  await expect.poll(() => container.evaluate((element) => element.contains(document.activeElement))).toBe(true);
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

async function visibleKeyboardFocus(control: Locator) {
  await expect(control).toBeFocused();
  expect(await control.evaluate((element) => {
    const style = getComputedStyle(element);
    return element.matches(":focus-visible") && (
      style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0 ||
      style.boxShadow !== "none"
    );
  })).toBe(true);
}

test("beUI desktop sidebar collapses without losing names, current route or keyboard navigation", async ({ page, beuiIdentity }) => {
  await login(page, beuiIdentity);
  // These are slots emitted by the imported animated-sidebar component. Its
  // presence is paired with real keyboard, geometry and navigation assertions.
  const sidebar = page.locator('[data-slot="sidebar"]');
  await expect(page.locator('[data-slot="sidebar-wrapper"]')).toBeVisible();
  await expect(sidebar).toHaveAccessibleName("工作空间侧栏");
  await expect(page.getByRole("main")).toHaveCount(1);
  await page.getByRole("link", { name: "跳到主要内容", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#workspace")).toBeFocused();
  const toggle = page.getByRole("button", { name: "切换导航", exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const expandedWidth = (await sidebar.boundingBox())!.width;
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeLessThan(expandedWidth * 0.7);
  const navigation = page.getByRole("navigation", { name: "主导航" });
  await expect(navigation.getByRole("link", { name: "工作台", exact: true })).toHaveAttribute("aria-current", "page");
  const inventory = navigation.getByRole("link", { name: "库存", exact: true });
  await inventory.focus();
  await visibleKeyboardFocus(inventory);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inventory$/);
  await expect(page.getByRole("heading", { name: "库存", exact: true })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "库存", exact: true })).toHaveAttribute("aria-current", "page");
  const previous = await toggle.getAttribute("aria-expanded");
  await page.keyboard.press("Control+b");
  await expect(toggle).toHaveAttribute("aria-expanded", previous === "true" ? "false" : "true");
  await noOverflow(page);
});

test("beUI mobile sidebar contains focus, restores its trigger and closes after choosing a page", async ({ page, beuiIdentity }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, beuiIdentity);
  const toggle = page.getByRole("button", { name: "切换导航", exact: true });
  const dialog = page.getByRole("dialog", { name: "工作空间侧栏", exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("navigation", { name: "主导航" })).not.toBeVisible();
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(dialog.getByRole("button", { name: "关闭导航", exact: true })).toBeVisible();
  await focusWithin(dialog);
  for (const key of ["Tab", "Shift+Tab"]) {
    for (let step = 0; step < 18; step += 1) {
      await page.keyboard.press(key);
      await focusWithin(dialog);
    }
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(toggle).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("link", { name: "库存", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inventory$/);
  await expect(dialog).not.toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("heading", { name: "库存", exact: true })).toBeVisible();
  await noOverflow(page);
});

test("beUI command palette keeps modal focus and supports searching and choosing with the keyboard", async ({ page, beuiIdentity }) => {
  await login(page, beuiIdentity);
  const trigger = page.getByRole("button", { name: "快捷导航", exact: true });
  await trigger.focus();
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "快捷导航", exact: true });
  const search = dialog.getByRole("combobox", { name: "搜索页面", exact: true });
  await expect(dialog).toBeVisible();
  await expect(search).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await focusWithin(dialog);
  await page.keyboard.press("Tab");
  await focusWithin(dialog);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await visibleKeyboardFocus(trigger);
  await page.keyboard.press("Enter");
  await expect(search).toBeFocused();
  await search.fill("库存");
  await page.keyboard.press("ArrowDown");
  const activeId = await search.getAttribute("aria-activedescendant");
  expect(activeId).toBeTruthy();
  await expect(page.locator(`[id="${activeId}"]`)).toContainText("库存");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inventory$/);
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("heading", { name: "库存", exact: true })).toBeVisible();
});

test("beUI catalog tabs have one tab stop and switch named panels with arrows, Home and End", async ({ page, beuiIdentity }) => {
  await login(page, beuiIdentity);
  await page.goto("/products");
  const list = page.getByRole("tablist", { name: "资料分类", exact: true });
  const tabs = list.getByRole("tab");
  await expect(tabs).toHaveCount(4);
  const first = tabs.first();
  await first.focus();
  await expect(first).toHaveAttribute("aria-selected", "true");
  expect(await tabs.evaluateAll((elements) => elements.filter((element) => element.getAttribute("tabindex") === "0").length)).toBe(1);
  await page.keyboard.press("ArrowRight");
  await expect(tabs.nth(1)).toBeFocused();
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  const picker = list.getByRole("tab", { name: "快捷选品", exact: true });
  await visibleKeyboardFocus(picker);
  await expect(picker).toHaveAttribute("aria-selected", "true");
  const panel = page.getByRole("tabpanel", { name: "快捷选品", exact: true });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("combobox", { name: "搜索商品", exact: true })).toBeVisible();
  const panelId = await panel.getAttribute("id");
  expect(panelId).toBeTruthy();
  await expect(picker).toHaveAttribute("aria-controls", panelId!);
  await page.keyboard.press("Home");
  await expect(first).toBeFocused();
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "新建商品", exact: true })).toBeVisible();
  await expect(panel).not.toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(picker).toBeFocused();
  await expect(picker).toHaveAttribute("aria-selected", "true");
  expect(await tabs.evaluateAll((elements) => elements.filter((element) => element.getAttribute("tabindex") === "0").length)).toBe(1);
});

test("reduced motion keeps beUI actions usable without press scaling or sidebar travel", async ({ page, beuiIdentity }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await login(page, beuiIdentity);
  const trigger = page.getByRole("button", { name: "快捷导航", exact: true });
  const box = (await trigger.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  const transforms = await trigger.evaluate(async (element) => {
    const samples: boolean[] = [];
    for (let frame = 0; frame < 8; frame += 1) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const transform = getComputedStyle(element).transform;
      samples.push(transform === "none" || new DOMMatrixReadOnly(transform).isIdentity);
    }
    return samples;
  });
  await page.mouse.up();
  expect(transforms.every(Boolean)).toBe(true);
  await expect(page.getByRole("dialog", { name: "快捷导航", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  const toggle = page.getByRole("button", { name: "切换导航", exact: true });
  const widths = await toggle.evaluate(async (element) => {
    const sidebar = document.querySelector('[data-slot="sidebar"]')!;
    const samples = [sidebar.getBoundingClientRect().width];
    (element as HTMLButtonElement).click();
    for (let frame = 0; frame < 10; frame += 1) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      samples.push(sidebar.getBoundingClientRect().width);
    }
    return samples;
  });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(widths.at(-1)!).toBeLessThan(widths[0] * 0.7);
  // Instant expanded/collapsed states are allowed; intermediate movement is not.
  expect(widths.every((width) => Math.abs(width - widths[0]) < 1 || Math.abs(width - widths.at(-1)!) < 1)).toBe(true);
  const products = page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "商品", exact: true });
  await products.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/products$/);
  await expect(page.getByRole("tablist", { name: "资料分类", exact: true })).toBeVisible();
});

test("beUI login fields expose validation and disabled submit blocks repeated keyboard requests", async ({ page }) => {
  let requests = 0;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/auth/login", async (route) => {
    requests += 1;
    await pending;
    await route.fulfill({ status: 401, json: {
      type: "about:blank", title: "Unauthorized", status: 401,
      detail: "Controlled UI-only login failure", request_id: "beui-login-test",
    } });
  });
  try {
    await page.goto("/login");
    const company = page.getByLabel("企业代码", { exact: true });
    const email = page.getByLabel("邮箱", { exact: true });
    const password = page.getByLabel("密码", { exact: true });
    await page.getByRole("button", { name: "进入工作空间", exact: true }).click();
    await expect(company).toHaveAttribute("aria-invalid", "true");
    await expect(company).toHaveAccessibleDescription("请输入企业代码");
    expect(requests).toBe(0);
    await company.fill("UI-QA");
    await email.fill("qa@example.test");
    await password.fill("ui-test-only-no-real-account");
    await password.press("Enter");
    await expect.poll(() => requests).toBe(1);
    await expect(page.getByRole("button", { name: "正在登录…", exact: true })).toBeDisabled();
    await password.press("Enter");
    await password.press("Enter");
    expect(requests).toBe(1);
    release();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("企业代码、邮箱或密码不正确");
    expect(requests).toBe(1);
    await expect(page.getByRole("button", { name: "进入工作空间", exact: true })).toBeEnabled();
    await expect(password).toHaveAttribute("type", "password");
    await expect(page).toHaveURL(/\/login$/);
  } finally { release(); }
});

test("beUI theme switching preserves the active workspace and input on desktop and mobile", async ({ page, beuiIdentity }) => {
  await login(page, beuiIdentity);
  await page.goto("/products");
  const picker = page.getByRole("tab", { name: "快捷选品", exact: true });
  await picker.click();
  const search = page.getByRole("combobox", { name: "搜索商品", exact: true });
  await search.fill("THEME-QA-保留输入");
  const theme = page.getByRole("button", { name: "切换明暗主题", exact: true });
  const isDark = () => page.evaluate(() => document.documentElement.classList.contains("dark"));
  await expect.poll(isDark).toBe(true);
  await expect(theme).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await theme.focus();
  await page.keyboard.press("Enter");
  await expect.poll(isDark).toBe(false);
  await expect(theme).toHaveAttribute("aria-pressed", "false");
  await expect(picker).toHaveAttribute("aria-selected", "true");
  await expect(search).toHaveValue("THEME-QA-保留输入");
  await expect(page).toHaveURL(/\/products$/);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(theme).toBeVisible();
  await theme.click();
  await expect.poll(isDark).toBe(true);
  await expect(theme).toHaveAttribute("aria-pressed", "true");
  await expect(picker).toHaveAttribute("aria-selected", "true");
  await expect(search).toHaveValue("THEME-QA-保留输入");
  await expect(page).toHaveURL(/\/products$/);
  await noOverflow(page);
});

test("beUI prompt preserves multiline and IME input and blocks unconfigured or pending submissions", async ({ page, beuiIdentity }) => {
  await login(page, beuiIdentity);
  const id = "fabd1e18-e7ea-415b-974e-29743042ba17";
  const conversation: components["schemas"]["ConversationRead"] = {
    id, title: "组件键盘测试", created_at: "2026-09-06T00:00:00Z",
    expires_at: "2099-09-13T00:00:00Z", turns: [], older_turns_omitted: false,
  };
  let configured = false;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const messages: { message: string; selected_ids: string[] }[] = [];
  const keys: (string | undefined)[] = [];
  // Only transport fixtures: there is no real conversation, order or provider
  // request. Existing assistant.spec.ts covers the actual server workflow.
  await page.route("**/api/v1/ai/status", (route) => route.fulfill({ json: {
    configured, can_manage_provider: true, provider_name: "UI fixture", model: "no-network",
  } satisfies components["schemas"]["AssistantStatus"] }));
  await page.route("**/api/v1/ai/conversations?*", (route) => route.fulfill({ json: {
    items: [conversation], total: 1, page: 1, page_size: 20,
  } satisfies components["schemas"]["ConversationsPage"] }));
  await page.route(`**/api/v1/ai/conversations/${id}`, (route) => route.fulfill({ json: conversation }));
  await page.route(`**/api/v1/ai/conversations/${id}/messages`, async (route) => {
    messages.push(route.request().postDataJSON());
    keys.push(route.request().headers()["idempotency-key"]);
    const attempt = messages.length;
    await pending;
    if (attempt === 1) await route.fulfill({ status: 503, json: {
      type: "about:blank", title: "Service Unavailable", status: 503,
      detail: "模型暂时无法响应，请稍后重试。", code: "AI_PROVIDER_UNAVAILABLE", request_id: "beui-prompt-test",
    } });
    else await route.fulfill({ status: 422, json: {
      type: "about:blank", title: "Unprocessable Content", status: 422,
      detail: "本次请求未执行，请调整问题。", code: "AI_INVALID_REQUEST", request_id: "beui-prompt-retry",
    } });
  });
  try {
    await page.goto(`/ai?conversation=${id}`);
    const prompt = page.getByRole("textbox", { name: "你的问题", exact: true });
    const send = page.getByRole("button", { name: "发送问题", exact: true });
    await expect(prompt).toBeDisabled();
    await expect(send).toBeDisabled();
    await page.keyboard.press("Enter");
    expect(messages).toHaveLength(0);
    configured = true;
    await page.getByRole("button", { name: "重新读取记录", exact: true }).click();
    await expect(prompt).toBeEnabled();
    await prompt.fill("查询库存");
    await prompt.press("Shift+Enter");
    await prompt.pressSequentially("保留这一行");
    await expect(prompt).toHaveValue("查询库存\n保留这一行");
    await prompt.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true });
    expect(messages).toHaveLength(0);
    await prompt.press("Enter");
    await expect.poll(() => messages.length).toBe(1);
    await expect(prompt).toBeDisabled();
    await expect(send).toBeDisabled();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    expect(messages).toEqual([{ message: "查询库存\n保留这一行", selected_ids: [] }]);
    release();
    const assistant = page.getByRole("region", { name: "AI 助手", exact: true });
    await expect(assistant.getByRole("alert")).toContainText("模型暂时无法响应");
    expect(messages).toHaveLength(1);
    // A lost/unknown response must keep the form locked. Only retrying the
    // original operation is offered, preserving its payload and idempotency key.
    await expect(prompt).toBeDisabled();
    await expect(send).toBeDisabled();
    await expect(prompt).toHaveValue("查询库存\n保留这一行");
    await assistant.getByRole("button", { name: "重试原提交", exact: true }).click();
    await expect.poll(() => messages.length).toBe(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(messages[1]).toEqual(messages[0]);
    await expect(assistant.getByRole("alert")).toContainText("本次请求未执行");
    // A later rejection cannot prove that the original request did not commit.
    // The existing submission contract requires a successful receipt to unlock.
    await expect(prompt).toBeDisabled();
    await expect(send).toBeDisabled();
    await expect(assistant.getByRole("button", { name: "重试原提交", exact: true })).toBeEnabled();
    await expect(prompt).toHaveValue("查询库存\n保留这一行");
  } finally { release(); }
});
