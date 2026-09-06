import { expect, test as base, type Page } from "@playwright/test";
import type { components } from "../generated/api/schema";

type Conversation = components["schemas"]["ConversationRead"];
type Turn = components["schemas"]["TurnRead"];
type Proposal = components["schemas"]["ProposalRead"];
type Write = { path: string; body: unknown; key: string | undefined };
const uid = (value: number) => `30000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const stamp = "2026-09-06T10:00:00Z";
const expires = "2099-09-13T10:00:00Z";
const permissions = ["profile.read", "ai.use", "ai.draft.create", "sales.order.write", "product.price.read",
  "sales.read", "purchase.order.write", "supplier.read", "purchase.read", "inventory.read", "customer.read", "warehouse.read", "catalog.read", "dashboard.read"];

function turn(value: number, answer: string): Turn {
  return { id: uid(value), prompt: `查询记录 ${value}`, answer, state: "COMPLETED", created_at: stamp,
    evidence: [], proposal: null, error_code: null, error_message: null, can_retry: false, model_calls: 1, tool_calls: 1,
    guided: false, interaction: "business" };
}
function evidence(value: number, title: string, quantity: string): components["schemas"]["Evidence"] {
  return { id: uid(value), title, tool: "get_inventory", as_of: stamp, scope: "该轮查询的服务器库存",
    summary: [{ label: "可用数量", value: quantity, unit: "个" }], columns: ["商品", "可用数量"],
    rows: [{ 商品: "M8 螺栓", 可用数量: quantity }], links: [{ label: "库存来源", href: "/inventory" }], truncated: false };
}
function proposal(): Proposal {
  return { id: uid(90), turn_id: uid(40), kind: "SALES", status: "PENDING", revision: 1,
    expires_at: expires, receipt: null, preview: {
      kind: "SALES", confirmation_hash: "a".repeat(64), total_amount: "45.0000",
      party_name: "待复核客户", warehouse_name: "主仓", warnings: [],
      order: { customer_id: uid(70), warehouse_id: uid(71), reason: "需要人工复核",
        lines: [{ product_id: uid(72), unit_id: uid(73), qty: "3", pricing_mode: "AUTO" }] },
      lines: [{ product_id: uid(72), unit_id: uid(73), product_label: "M8 · 螺栓", unit_label: "个",
        qty: "3", unit_price: "15.000000", amount: "45.0000", unit_to_base_factor: "1.000000",
        base_qty: "3.000000", conversion_version: 1, pricing_mode: "AUTO", price_source: { source: "standard" } }],
    } };
}
function history(): Conversation[] {
  return [{ id: uid(1), title: "库存与开单", created_at: stamp, expires_at: expires, older_turns_omitted: false,
    turns: [
      ...Array.from({ length: 24 }, (_, index) => turn(100 + index, `此前的服务器答复 ${index}。\n请按照实际记录复核。`)),
      { ...turn(40, "销售草稿已预览，请核对。"), state: "WAITING", proposal: proposal(),
        evidence: [evidence(80, "开单时的库存", "12.000001")] },
      { ...turn(41, "另一条库存查询已经完成。"), evidence: [evidence(81, "较新的库存依据", "17.000002")] },
    ] },
  { id: uid(2), title: "另一位客户", created_at: stamp, expires_at: expires, older_turns_omitted: false, turns: [] }];
}

// These tests exercise the production React/Motion layout with bounded HTTP
// fixtures. All API routes are intercepted: no database or model service calls.
const test = base.extend<{ experience: { conversations: Conversation[]; writes: Write[] } }>({
  experience: async ({ page }, provideExperience) => {
    const conversations = history();
    const writes: Write[] = [];
    const unexpected: string[] = [];
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (request.method() === "GET") {
        if (pathname === "/api/v1/auth/me") return route.fulfill({ json: {
          user_id: uid(10), organization_id: uid(11), organization_code: "UI_EXPERIENCE",
          organization_name: "助手体验测试企业", display_name: "体验用户", email: "ui@example.test", permissions,
        } satisfies components["schemas"]["Profile"] });
        if (pathname === "/api/v1/ai/status") return route.fulfill({ json: {
          configured: true, can_manage_provider: false, provider_name: "固定测试响应", model: "no-network",
        } satisfies components["schemas"]["AssistantStatus"] });
        if (pathname === "/api/v1/ai/conversations") return route.fulfill({ json: {
          items: conversations, total: conversations.length, page: 1, page_size: 20,
        } satisfies components["schemas"]["ConversationsPage"] });
        const conversation = conversations.find((item) => pathname === `/api/v1/ai/conversations/${item.id}`);
        if (conversation) return route.fulfill({ json: conversation });
        if (["/api/v1/customers", "/api/v1/warehouses", "/api/v1/suppliers"].includes(pathname)) {
          return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 25 } });
        }
      } else {
        writes.push({ path: pathname, body: request.postDataJSON(), key: request.headers()["idempotency-key"] });
        if (request.method() === "POST" && pathname === "/api/v1/ai/conversations") {
          const created: Conversation = { id: uid(3), title: "新对话", created_at: stamp,
            expires_at: expires, turns: [], older_turns_omitted: false };
          conversations.unshift(created);
          return route.fulfill({ json: created });
        }
      }
      unexpected.push(request.method() + " " + pathname);
      await route.fulfill({ status: 418, json: { detail: "Unexpected UI fixture request" } });
    });
    try { await provideExperience({ conversations, writes }); }
    finally {
      expect(unexpected, "This layout fixture must never send a business/model request").toEqual([]);
      expect(errors, "No uncaught browser errors during panel interaction").toEqual([]);
    }
  },
});

async function open(page: Page, conversationId = uid(1)) {
  await page.goto(`/ai?conversation=${conversationId}`);
  await expect(page.getByRole("region", { name: "助手对话", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "你的问题", exact: true })).toBeEnabled();
}
async function composerVisible(page: Page) {
  expect(await page.locator('textarea[aria-label="你的问题"]').count()).toBe(1);
  const composer = page.getByRole("region", { name: "发送消息", exact: true });
  await expect(composer).toBeInViewport({ ratio: 1 });
  await expect(composer.getByRole("button", { name: "发送问题", exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test("assistant opens from two desktop columns into review and preserves a dirty draft across panels and resizing", async ({ page, experience }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await open(page);
  const historyPanel = page.getByRole("complementary", { name: "历史会话", exact: true });
  const chat = page.getByRole("region", { name: "助手对话", exact: true });
  const context = page.getByRole("complementary", { name: "业务依据与草稿", exact: true });
  for (const panel of [historyPanel, chat]) await expect(panel).toBeVisible();
  await expect(context).not.toBeVisible();
  const reviewToggle = page.getByRole("button", { name: "切换业务依据与草稿", exact: true });
  await expect(reviewToggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator('[data-assistant-panel="context"]')).toHaveCount(1);
  await composerVisible(page);
  await reviewToggle.click();
  await expect(reviewToggle).toHaveAttribute("aria-expanded", "true");
  await expect(context).toBeVisible();
  const [left, middle, right] = await Promise.all([historyPanel.boundingBox(), chat.boundingBox(), context.boundingBox()]);
  expect(left!.x + left!.width).toBeLessThanOrEqual(middle!.x + 1);
  expect(middle!.x + middle!.width).toBeLessThanOrEqual(right!.x + 1);
  await composerVisible(page);
  const transcript = page.getByRole("region", { name: "对话记录", exact: true });
  await transcript.evaluate((element) => { element.scrollTop = 0; });
  await expect.poll(() => transcript.evaluate((element) => element.scrollTop)).toBe(0);
  await composerVisible(page);
  await expect(context.getByRole("region", { name: "较新的库存依据", exact: true })).toContainText("17.000002");
  await expect(chat.getByRole("region", { name: "较新的库存依据", exact: true })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "较新的库存依据结果摘要", exact: true })).toContainText("17.000002");
  const draftMessage = transcript.getByRole("article", { name: "经营助手回复", exact: true }).filter({ hasText: "销售草稿已预览，请核对。" });
  const queryMessage = transcript.getByRole("article", { name: "经营助手回复", exact: true }).filter({ hasText: "另一条库存查询已经完成。" });
  await draftMessage.getByRole("button", { name: "复核草稿", exact: true }).click();
  const editor = context.getByRole("region", { name: "开单复核", exact: true });
  await expect(editor).toContainText("45.0000");
  await editor.getByLabel("第 1 行数量", { exact: true }).fill("2.000001");
  await expect(editor.getByRole("button", { name: "确认创建草稿", exact: true })).toBeDisabled();
  await expect(editor).not.toContainText("服务器核算合计");
  const originalInput = await editor.getByLabel("第 1 行数量", { exact: true }).elementHandle();
  await reviewToggle.click();
  await expect(context).not.toBeVisible();
  await expect(reviewToggle).toHaveAttribute("aria-expanded", "false");
  expect(await originalInput!.evaluate((element) => element.isConnected)).toBe(true);
  await reviewToggle.click();
  await expect(editor.getByLabel("第 1 行数量", { exact: true })).toHaveValue("2.000001");
  await queryMessage.getByRole("button", { name: /^查看业务依据/ }).click();
  await expect(context.getByRole("region", { name: "较新的库存依据", exact: true })).toContainText("17.000002");
  await expect(editor).not.toBeVisible();
  expect(await originalInput!.evaluate((element) => element.isConnected)).toBe(true);
  await draftMessage.getByRole("button", { name: "复核草稿", exact: true }).click();
  await expect(editor.getByLabel("第 1 行数量", { exact: true })).toHaveValue("2.000001");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "返回对话", exact: true }).click();
  await expect(chat).toBeVisible();
  await expect(historyPanel).not.toBeVisible();
  await expect(context).not.toBeVisible();
  await composerVisible(page);
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await expect(historyPanel).toBeVisible();
  await expect(chat).not.toBeVisible();
  await expect(context).not.toBeVisible();
  await page.getByRole("button", { name: "业务依据与草稿", exact: true }).click();
  await expect(context).toBeVisible();
  await expect(historyPanel).not.toBeVisible();
  await expect(editor.getByLabel("第 1 行数量", { exact: true })).toHaveValue("2.000001");
  await expect(editor.getByRole("button", { name: "确认创建草稿", exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 1600, height: 1000 });
  for (const panel of [historyPanel, chat, context]) await expect(panel).toBeVisible();
  await expect(editor.getByLabel("第 1 行数量", { exact: true })).toHaveValue("2.000001");
  expect(await originalInput!.evaluate((element) => element.isConnected)).toBe(true);
  await composerVisible(page);
  expect(experience.writes).toEqual([]);
});

test("mobile conversation inputs stay separate and new-conversation quick prompts only fill the single composer", async ({ page, experience }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const input = page.getByRole("textbox", { name: "你的问题", exact: true });
  await input.fill("第一个会话专用的未发送内容");
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  const historyPanel = page.getByRole("complementary", { name: "历史会话", exact: true });
  await historyPanel.getByRole("button", { name: /另一位客户/ }).click();
  await expect(page).toHaveURL(new RegExp(`conversation=${uid(2)}$`));
  await expect(input).toHaveValue("");
  await input.fill("第二个会话独立内容");
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await historyPanel.getByRole("button", { name: /库存与开单/ }).click();
  await expect(input).toHaveValue("第一个会话专用的未发送内容");
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await historyPanel.getByRole("button", { name: /另一位客户/ }).click();
  await expect(input).toHaveValue("第二个会话独立内容");
  expect(experience.writes).toEqual([]);
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`conversation=${uid(3)}$`));
  await expect(input).toHaveValue("");
  expect(experience.writes).toHaveLength(1);
  expect(experience.writes[0]).toMatchObject({ path: "/api/v1/ai/conversations", body: { title: "新对话" } });
  expect(experience.writes[0].key).toBeTruthy();
  for (const [label, value] of [["查询库存", /库存/], ["了解经营情况", /经营/], ["准备销售草稿", /销售/], ["准备采购草稿", /采购/]] as const) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(input).toHaveValue(value);
    await composerVisible(page);
    expect(experience.writes).toHaveLength(1);
  }
});
