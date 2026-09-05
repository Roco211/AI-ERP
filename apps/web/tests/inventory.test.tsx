import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  within,
  act,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, expect, test, afterEach } from "vitest";
import { InventoryWorkspace } from "@/features/inventory/workspace";
import { api } from "@/lib/api";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn() },
  ApiError: class extends Error {
    status = 409;
  },
}));
vi.mock("@/features/catalog/picker", () => ({
  ProductPicker: () => <p>商品选择器</p>,
}));
const get = vi.mocked(api.GET);
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.clearAllMocks();
});
function renderInventory(permissions: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InventoryWorkspace permissions={permissions} />
    </QueryClientProvider>,
  );
}
test("inventory permission is required before loading stock", () => {
  get.mockClear();
  renderInventory([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看库存");
  expect(get).not.toHaveBeenCalled();
});
test("quantity readers do not see costs or mutation controls", async () => {
  get.mockResolvedValue({
    data: {
      items: [
        {
          warehouse_id: "w",
          product_id: "p",
          product_name: "螺栓",
          sku: "B1",
          unit_name: "个",
          warehouse_name: "主仓",
          on_hand_qty: "100",
          reserved_qty: "20",
          available_qty: "80",
          version: 1,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    },
    response: new Response(),
    error: undefined,
  } as never);
  renderInventory(["inventory.read"]);
  expect(await screen.findByText("螺栓")).toBeVisible();
  expect(screen.queryByText("库存金额")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "期初库存" }));
  expect(
    screen.queryByRole("button", { name: "新建期初库存" }),
  ).not.toBeInTheDocument();
});
test("an incomplete opening draft cannot be saved", async () => {
  get.mockResolvedValue({
    data: { items: [], total: 0, page: 1, page_size: 25 },
    response: new Response(),
    error: undefined,
  } as never);
  renderInventory(["inventory.read", "inventory.opening", "product.cost.read"]);
  fireEvent.click(screen.getByRole("button", { name: "期初库存" }));
  fireEvent.click(screen.getByRole("button", { name: "新建期初库存" }));
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("alert")
        .some((x) => x.textContent?.includes("请选择仓库")),
    ).toBe(true),
  );
});


test("late document responses cannot replace the latest selection", async () => {
  const docs = ["A", "B", "C"].map((id) => ({
    id, number: id, type: "OPENING", status: "DRAFT", version: 1,
    reason: "reason-" + id, warehouse_name: "主仓", lines: [],
    created_at: "2026-09-05T00:00:00Z",
  }));
  let finishB!: (value: unknown) => void;
  const delayed = new Promise((resolve) => { finishB = resolve; });
  const response = (data: unknown) => ({ data, error: undefined, response: new Response() });
  get.mockImplementation((path, options) => {
    if (path === "/api/v1/inventory/documents/{id}") {
      const id = (options as { params: { path: { id: string } } }).params.path.id;
      return (id === "B" ? delayed : Promise.resolve(response(docs.find(d => d.id === id)))) as never;
    }
    return Promise.resolve(response({ items: path === "/api/v1/inventory/documents" ? docs : [], total: 3, page: 1, page_size: 25 })) as never;
  });
  renderInventory(["inventory.read"]);
  fireEvent.click(screen.getByRole("button", { name: "期初库存" }));
  await screen.findByText("reason-A");
  const view = (reason: string) => within(screen.getByText(reason).closest("tr")!).getByRole("button", { name: "查看" });
  fireEvent.click(view("reason-A"));
  expect(await screen.findByRole("region", { name: "库存单据详情" })).toHaveTextContent("reason-A");
  fireEvent.click(view("reason-B"));
  expect(screen.queryByRole("region", { name: "库存单据详情" })).not.toBeInTheDocument();
  fireEvent.click(view("reason-C"));
  await waitFor(() => expect(screen.getByRole("region", { name: "库存单据详情" })).toHaveTextContent("reason-C"));
  await act(async () => { finishB(response(docs[1])); await delayed; });
  expect(screen.getByRole("region", { name: "库存单据详情" })).toHaveTextContent("reason-C");
  expect(screen.getByRole("region", { name: "库存单据详情" })).not.toHaveTextContent("reason-B");
});
