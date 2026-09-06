import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationHistory, type ConversationHistoryProps } from "@/features/ai/conversation-history";

const items = [
  { id: "history-1", title: "M8 螺栓库存查询", created_at: "2026-09-06T01:20:00Z", expires_at: "2026-09-13T01:20:00Z" },
  { id: "history-2", title: "客户销售草稿 <script>不执行</script>", created_at: "2026-09-05T02:30:00Z", expires_at: "2026-09-12T02:30:00Z" },
];

function props(changes: Partial<ConversationHistoryProps> = {}): ConversationHistoryProps {
  return { items, selectedId: items[0].id, page: 1, total: items.length,
    loading: false, error: false, locked: false,
    onSelect: vi.fn(), onNew: vi.fn(), onPage: vi.fn(), ...changes };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("history uses real titles, selected semantics, full title and full timestamp", () => {
  const value = props();
  render(<ConversationHistory {...value} />);
  const nav = screen.getByRole("navigation", { name: "对话列表" });
  const selected = within(nav).getByRole("button", { name: items[0].title });
  expect(selected).toHaveAttribute("aria-current", "page");
  expect(selected).toHaveAttribute("title", items[0].title);
  const timestamp = selected.querySelector("time");
  expect(timestamp).toHaveAttribute("datetime", items[0].created_at);
  expect(timestamp?.getAttribute("title")).toContain("2026");
  expect(timestamp?.textContent).not.toBe(items[0].created_at);
  expect(within(nav).getByRole("button", { name: items[1].title })).not.toHaveAttribute("aria-current");
  expect(nav.querySelector("script")).toBeNull();
  expect(screen.getByText(/仅自己可见/)).toBeVisible();
  expect(screen.getByText(/对话正文保留 7 天/)).toBeVisible();
  fireEvent.click(within(nav).getByRole("button", { name: items[1].title }));
  expect(value.onSelect).toHaveBeenCalledExactlyOnceWith(items[1].id);
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  expect(value.onNew).toHaveBeenCalledOnce();
});

test("search explicitly filters only this page and does not trigger requests or navigation", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  const value = props();
  render(<ConversationHistory {...value} />);
  const input = screen.getByRole("searchbox", { name: "搜索本页对话" });
  expect(input).toHaveAccessibleDescription("仅搜索当前页对话");
  fireEvent.change(input, { target: { value: " m8 " } });
  expect(screen.getByRole("button", { name: items[0].title })).toBeVisible();
  expect(screen.queryByRole("button", { name: items[1].title })).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: "本页不存在的标题" } });
  expect(screen.getByRole("status")).toHaveTextContent("本页没有匹配的对话");
  fireEvent.change(input, { target: { value: "" } });
  expect(screen.getByRole("button", { name: items[1].title })).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
  expect(value.onSelect).not.toHaveBeenCalled();
  expect(value.onNew).not.toHaveBeenCalled();
  expect(value.onPage).not.toHaveBeenCalled();
});

test.each([
  { loading: true, error: false, role: "status", message: "正在读取对话" },
  { loading: false, error: true, role: "alert", message: "暂时无法读取对话列表" },
])("$role state is explicit and does not keep displaying old titles", ({ loading, error, role, message }) => {
  render(<ConversationHistory {...props({ loading, error })} />);
  expect(screen.getByRole(role)).toHaveTextContent(message);
  expect(screen.queryByRole("button", { name: items[0].title })).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "搜索本页对话" })).toBeDisabled();
});

test("empty history offers the existing new conversation action without fabricated entries", () => {
  const value = props({ items: [], total: 0 });
  render(<ConversationHistory {...value} />);
  expect(screen.getByRole("status")).toHaveTextContent("还没有对话");
  expect(within(screen.getByRole("navigation", { name: "对话列表" })).queryAllByRole("button")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  expect(value.onNew).toHaveBeenCalledOnce();
});

test("pagination respects the server page size and clears a page-local filter", () => {
  const value = props({ total: 21 });
  const view = render(<ConversationHistory {...value} />);
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索本页对话" }), { target: { value: "m8" } });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(value.onPage).toHaveBeenCalledExactlyOnceWith(2);
  expect(screen.getByRole("searchbox", { name: "搜索本页对话" })).toHaveValue("");
  view.rerender(<ConversationHistory {...value} page={2} />);
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  expect(screen.getByText("第 2 页")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  expect(value.onPage).toHaveBeenLastCalledWith(1);
});

test("pending work locks new conversation, selection and paging while a local search stays read-only", () => {
  const value = props({ locked: true, total: 45, page: 2 });
  render(<ConversationHistory {...value} />);
  for (const name of ["新对话", "上一页", "下一页", ...items.map((item) => item.title)]) {
    const button = screen.getByRole("button", { name });
    expect(button).toBeDisabled();
    fireEvent.click(button);
  }
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索本页对话" }), { target: { value: "m8" } });
  expect(screen.getByRole("button", { name: items[0].title })).toBeDisabled();
  expect(value.onNew).not.toHaveBeenCalled();
  expect(value.onSelect).not.toHaveBeenCalled();
  expect(value.onPage).not.toHaveBeenCalled();
});
