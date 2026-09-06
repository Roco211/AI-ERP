import {
  render,
  within,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, expect, test, afterEach } from "vitest";
import { PurchasingWorkspace } from "@/features/purchasing/workspace";
import { api } from "@/lib/api";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));
vi.mock("@/features/catalog/picker", () => ({
  ProductPicker: () => <p>采购选品</p>,
}));
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.clearAllMocks();
});
function show(permissions: string[]) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PurchasingWorkspace permissions={permissions} />
    </QueryClientProvider>,
  );
}
test("no purchase access means no data request", () => {
  show([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看采购");
  expect(api.GET).not.toHaveBeenCalled();
});
test("quantity-only purchase readers see no prices or mutation controls", async () => {
  vi.mocked(api.GET).mockResolvedValue({
    data: {
      items: [
        {
          id: "o",
          number: "PO-TEST",
          status: "CONFIRMED",
          receiving_status: "PARTIAL",
          supplier_name: "供应商",
          warehouse_name: "主仓",
          amount: "123.45",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    },
    response: new Response(),
  } as never);
  show(["purchase.read"]);
  expect(await screen.findByText("PO-TEST")).toBeVisible();
  expect(screen.queryByText("123.45")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("tab", { name: "历史采购价" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "新建采购订单" }),
  ).not.toBeInTheDocument();
});
test("incomplete order draft is rejected before submission", async () => {
  vi.mocked(api.GET).mockResolvedValue({
    data: { items: [], total: 0, page: 1, page_size: 25 },
    response: new Response(),
  } as never);
  show(["purchase.read", "purchase.order.write", "product.cost.read"]);
  fireEvent.click(screen.getByRole("button", { name: "新建采购订单" }));
  fireEvent.click(screen.getByRole("button", { name: "保存采购草稿" }));
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("alert")
        .some((el) => el.textContent?.includes("请填写原因")),
    ).toBe(true),
  );
  expect(api.POST).not.toHaveBeenCalled();
});

const orderRow = (id: string) => ({
  id,
  number: "PO-" + id,
  status: "DRAFT",
  receiving_status: "UNRECEIVED",
  supplier_name: "供应商",
  warehouse_name: "仓库",
  reason: "订单" + id,
  version: 1,
  lines: [],
});
const ok = (data: unknown) => ({ data, response: new Response() }) as never;
test("late detail response cannot replace the selected order", async () => {
  let resolveFirst!: (value: never) => void;
  vi.mocked(api.GET).mockImplementation(((
    path: string,
    options: { params?: { path?: { id: string } } },
  ) => {
    if (path.endsWith("/{id}")) {
      if (options.params?.path?.id === "A")
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      return Promise.resolve(ok(orderRow("B")));
    }
    return Promise.resolve(
      ok({ items: [orderRow("A"), orderRow("B")], total: 2 }),
    );
  }) as never);
  show(["purchase.read"]);
  fireEvent.click((await screen.findAllByRole("button", { name: "查看" }))[0]);
  expect(await screen.findByText("正在加载单据详情…")).toBeVisible();
  fireEvent.click(screen.getAllByRole("button", { name: "查看" })[1]);
  expect(await screen.findByText("原因：订单B")).toBeVisible();
  resolveFirst(ok(orderRow("A")));
  await waitFor(() =>
    expect(screen.queryByText("原因：订单A")).not.toBeInTheDocument(),
  );
  expect(screen.getByText("原因：订单B")).toBeVisible();
});

test("network failure freezes the original key and body until retry", async () => {
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    Promise.resolve(
      ok(
        path.endsWith("/{id}")
          ? orderRow("A")
          : { items: [orderRow("A")], total: 1 },
      ),
    )) as never);
  let reject!: (reason: Error) => void;
  vi.mocked(api.POST).mockImplementationOnce(
    () =>
      new Promise((_, r) => {
        reject = r;
      }),
  );
  show(["purchase.read", "purchase.order.confirm", "product.cost.read"]);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认订单" }));
  const submit = screen.getByRole("button", { name: "确认执行" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(api.POST).toHaveBeenCalledTimes(1);
  reject(new Error("offline"));
  expect(
    await within(screen.getByRole("dialog")).findByText(/提交结果待确认/),
  ).toBeVisible();
  vi.mocked(api.POST).mockResolvedValueOnce(
    ok({ id: "A", status: "CONFIRMED", version: 2, request_id: "test" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.POST).mock.calls[1]).toEqual(
    vi.mocked(api.POST).mock.calls[0],
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});

test("version conflict stays explicit and allows closing to reload", async () => {
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    Promise.resolve(
      ok(
        path.endsWith("/{id}")
          ? orderRow("A")
          : { items: [orderRow("A")], total: 1 },
      ),
    )) as never);
  vi.mocked(api.POST).mockResolvedValueOnce({
    error: { detail: "单据版本已变化，请重新加载" },
    response: new Response(null, { status: 409 }),
  } as never);
  show(["purchase.read", "purchase.order.confirm", "product.cost.read"]);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认订单" }));
  fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
  expect(
    await within(screen.getByRole("dialog")).findByText(
      "单据版本已变化，请重新加载",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "重试原提交" }),
  ).not.toBeInTheDocument();
  expect(api.POST).toHaveBeenCalledTimes(1);
});
