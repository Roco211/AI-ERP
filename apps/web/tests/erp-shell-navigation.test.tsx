import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ERPShell } from "@/components/erp-shell";
import { usePendingNavigationGuard } from "@/features/sales/navigation";
import { ApiError, getProfile } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn() },
  post: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  usePathname: () => "/ai",
}));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  getProfile: vi.fn(),
  api: { POST: mocks.post },
}));
vi.mock("@/components/motion/theme-toggle", () => ({ ThemeToggle: () => null }));
vi.mock("@/features/sales/workspace", () => ({ SalesWorkspace: () => null }));
vi.mock("@/features/funds/workspace", () => ({ FundsWorkspace: () => null }));
vi.mock("@/features/replenishment/workspace", () => ({ ReplenishmentWorkspace: () => null }));
vi.mock("@/features/imports/workspace", () => ({ ImportsWorkspace: () => null }));
vi.mock("@/features/dashboard/workspace", () => ({ DashboardWorkspace: () => null }));
vi.mock("@/features/ai/workspace", () => ({ AssistantWorkspace: () => <div>当前对话与原提交仍保留</div> }));
vi.mock("@/features/ai/provider-settings", () => ({ ProviderSettings: () => null }));
vi.mock("@/features/purchasing/workspace", () => ({ PurchasingWorkspace: () => null }));
vi.mock("@/features/inventory/workspace", () => ({ InventoryWorkspace: () => null }));
vi.mock("@/features/catalog/workspace", () => ({ CatalogWorkspace: () => null }));
vi.mock("@/features/catalog/manager", () => ({ CatalogManager: () => null }));

const profile = {
  organization_id: "org-one", organization_name: "测试企业", organization_code: "TEST",
  user_id: "owner-one", display_name: "测试用户", email: "test@example.invalid",
  permissions: ["ai.use"],
};
const clients: QueryClient[] = [];
const scrollIntoView = HTMLElement.prototype.scrollIntoView;
beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  mocks.post.mockResolvedValue({ response: { ok: true } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.resetAllMocks();
  HTMLElement.prototype.scrollIntoView = scrollIntoView;
});
function PendingOwner({ locked }: { locked: boolean }) {
  usePendingNavigationGuard(locked);
  return null;
}
function mount(locked = true, authenticated = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  clients.push(client);
  if (authenticated) client.setQueryData(["me"], profile);
  const tree = (pending: boolean) => (
    <QueryClientProvider client={client}>
      <PendingOwner locked={pending} />
      <ERPShell section="ai" />
    </QueryClientProvider>
  );
  const view = render(tree(locked));
  return { ...view, settle: () => view.rerender(tree(false)) };
}

test.each(["keyboard", "pointer"] as const)(
  "the real quick-navigation palette cannot bypass an unknown submission via %s",
  async (method) => {
    const view = mount();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox", { name: "搜索页面" });
    fireEvent.change(input, { target: { value: "库存" } });
    if (method === "keyboard") fireEvent.keyDown(input, { key: "Enter" });
    else fireEvent.click(screen.getByRole("option", { name: "库存" }));
    expect(mocks.router.push).not.toHaveBeenCalled();
    expect(mocks.router.replace).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("当前提交的结果尚未确认");
    expect(screen.getByText("当前对话与原提交仍保留")).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    view.settle();
    fireEvent.click(screen.getByRole("button", { name: "快捷导航" }));
    fireEvent.click(await screen.findByRole("option", { name: "库存" }));
    expect(mocks.router.push).toHaveBeenCalledExactlyOnceWith("/inventory");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  },
);

test("logout waits for the pending receipt, then retains the normal logout flow", async () => {
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  expect(mocks.post).not.toHaveBeenCalled();
  expect(mocks.router.replace).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent("重试原提交");
  fireEvent.click(screen.getByRole("button", { name: "关闭导航提示" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  view.settle();
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/login"));
  expect(mocks.post).toHaveBeenCalledExactlyOnceWith("/api/v1/auth/logout");
});

test("sidebar links stay blocked while pending and navigate once the receipt settles", () => {
  const view = mount();
  fireEvent.click(screen.getByRole("link", { name: "库存" }));
  expect(mocks.router.push).not.toHaveBeenCalled();
  view.settle();
  fireEvent.click(screen.getByRole("link", { name: "库存" }));
  expect(mocks.router.push).toHaveBeenCalledExactlyOnceWith("/inventory");
});

test("a current 401 still clears the session and redirects even with a pending owner", async () => {
  vi.mocked(getProfile).mockRejectedValue(new ApiError(401, "登录已失效"));
  mount(true, false);
  await waitFor(() => expect(mocks.router.replace).toHaveBeenCalledWith("/login"));
  expect(mocks.router.push).not.toHaveBeenCalled();
  expect(mocks.post).not.toHaveBeenCalled();
  expect(screen.queryByText("当前对话与原提交仍保留")).not.toBeInTheDocument();
});

test("an AI logout failure stays visible outside the fixed-height conversation layout", async () => {
  mocks.post.mockResolvedValue({ response: { ok: false } });
  mount(false);
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("退出失败，请重试。");
  expect(document.getElementById("workspace")).not.toContainElement(alert);
  expect(mocks.router.replace).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "关闭退出提示" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
