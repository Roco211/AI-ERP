import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { ProductPicker } from "@/features/catalog/picker";
import { api } from "@/lib/api";
vi.mock("@/lib/api", () => ({ api: { GET: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
test("first ArrowDown chooses the first similar product and changing search clears selection", async () => {
  const selected = vi.fn();
  vi.mocked(api.GET).mockImplementation(async (path) => {
    let data: unknown = { items: [] };
    if (path === "/api/v1/catalog/search")
      data = {
        total: 2,
        items: [
          {
            id: "first",
            name: "第一件螺栓",
            sku: "BOLT-1",
            default_sales_unit_id: "unit",
          },
          {
            id: "second",
            name: "第二件螺栓",
            sku: "BOLT-2",
            default_sales_unit_id: "unit",
          },
        ],
      };
    if (path === "/api/v1/product-units")
      data = {
        items: [
          { id: "conversion", unit_id: "unit", unit_to_base_factor: "1" },
        ],
      };
    if (path === "/api/v1/units")
      data = { items: [{ id: "unit", name: "个" }] };
    if (path === "/api/v1/catalog/conversion")
      data = {
        product_id: "first",
        unit_id: "unit",
        qty: "1",
        base_qty: "1",
        unit_to_base_factor: "1",
        conversion_version: 1,
      };
    return { data, response: new Response() } as never;
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProductPicker permissions={["catalog.read"]} onSelect={selected} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: /BOLT-1/ });
  const search = screen.getByRole("combobox", { name: "搜索商品" });
  fireEvent.keyDown(search, { key: "ArrowDown" });
  fireEvent.keyDown(search, { key: "Enter" });
  await screen.findByRole("heading", { name: "第一件螺栓" });
  expect(
    screen.queryByRole("heading", { name: "第二件螺栓" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "确认选择" }));
  await waitFor(() =>
    expect(selected).toHaveBeenCalledWith(
      expect.objectContaining({
        product: expect.objectContaining({ id: "first" }),
      }),
    ),
  );
  fireEvent.change(search, { target: { value: "另一个商品" } });
  expect(
    screen.queryByRole("button", { name: "确认选择" }),
  ).not.toBeInTheDocument();
});

async function pendingSelection() {
  const selected = vi.fn();
  let resolve!: (value: never) => void;
  const response = new Promise<never>((done) => {
    resolve = done;
  });
  vi.mocked(api.GET).mockImplementation(async (path) => {
    if (path === "/api/v1/catalog/conversion") return response;
    let data: unknown = { items: [] };
    if (path === "/api/v1/catalog/search")
      data = {
        total: 1,
        items: [
          {
            id: "first",
            name: "螺栓",
            sku: "BOLT-1",
            default_sales_unit_id: "unit",
          },
        ],
      };
    if (path === "/api/v1/product-units")
      data = {
        items: [
          { id: "each", unit_id: "unit", unit_to_base_factor: "1" },
          { id: "box", unit_id: "box", unit_to_base_factor: "100" },
        ],
      };
    if (path === "/api/v1/units")
      data = {
        items: [
          { id: "unit", name: "个" },
          { id: "box", name: "箱" },
        ],
      };
    return { data, response: new Response() } as never;
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ProductPicker permissions={["catalog.read"]} onSelect={selected} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /BOLT-1/ }));
  await screen.findByRole("option", { name: /100/ });
  fireEvent.keyDown(screen.getByLabelText("选品数量"), { key: "Enter" });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "正在换算…" })).toBeDisabled(),
  );
  async function finish() {
    await act(async () => {
      resolve({
        data: {
          product_id: "first",
          unit_id: "unit",
          qty: "1",
          base_qty: "1",
          unit_to_base_factor: "1",
          conversion_version: 1,
        },
        response: new Response(),
      } as never);
      await response;
    });
  }
  return { selected, finish, unmount: view.unmount };
}

test.each(["quantity", "unit", "search", "unmount"])(
  "ignores a conversion arriving after %s changed",
  async (change) => {
    const pending = await pendingSelection();
    if (change === "quantity")
      fireEvent.change(screen.getByLabelText("选品数量"), {
        target: { value: "3" },
      });
    if (change === "unit")
      fireEvent.change(screen.getByLabelText("选品单位"), {
        target: { value: "box" },
      });
    if (change === "search")
      fireEvent.change(screen.getByLabelText("搜索商品"), {
        target: { value: "其他" },
      });
    if (change === "unmount") pending.unmount();
    await pending.finish();
    expect(pending.selected).not.toHaveBeenCalled();
    expect(screen.queryByText(/已选择/)).not.toBeInTheDocument();
  },
);

test("repeated Enter during conversion only captures one selection", async () => {
  const pending = await pendingSelection();
  fireEvent.keyDown(screen.getByLabelText("选品数量"), { key: "Enter" });
  fireEvent.keyDown(screen.getByLabelText("选品数量"), { key: "Enter" });
  expect(
    vi
      .mocked(api.GET)
      .mock.calls.filter((call) => call[0] === "/api/v1/catalog/conversion"),
  ).toHaveLength(1);
  await pending.finish();
  expect(pending.selected).toHaveBeenCalledTimes(1);
});
