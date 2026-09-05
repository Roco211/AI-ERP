import {
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
