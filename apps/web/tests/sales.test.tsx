import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { SalesWorkspace } from "@/features/sales/workspace";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() },
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));
vi.mock("@/features/catalog/picker", () => ({
  ProductPicker: ({ onSelect }: { onSelect: (selection: unknown) => void }) => (
    <button
      onClick={() =>
        onSelect({
          product: {
            id: PRODUCT,
            sku: "TEST-BOLT",
            name: "测试螺栓",
            base_unit_id: UNIT,
          },
          snapshot: {
            product_id: PRODUCT,
            unit_id: UNIT,
            qty: "2",
            base_qty: "2",
            unit_to_base_factor: "1",
            conversion_version: 1,
          },
          unitLabel: "个",
        })
      }
    >
      选入测试商品
    </button>
  ),
}));

const CUSTOMER_A = "11111111-1111-4111-8111-111111111111";
const CUSTOMER_B = "22222222-2222-4222-8222-222222222222";
const WAREHOUSE = "33333333-3333-4333-8333-333333333333";
const PRODUCT = "44444444-4444-4444-8444-444444444444";
const UNIT = "55555555-5555-4555-8555-555555555555";
const LINE = "66666666-6666-4666-8666-666666666666";
const read = ["sales.read"];
const seller = [
  ...read,
  "product.price.read",
  "sales.order.write",
  "sales.order.confirm",
  "sales.order.close",
  "customer.read",
  "warehouse.read",
  "catalog.read",
];
const ok = (data: unknown) => ({ data, response: new Response() }) as never;
const order = (id = "A") => ({
  id,
  number: "SO-" + id,
  status: "DRAFT",
  fulfillment_status: "UNFULFILLED",
  version: 1,
  customer_id: CUSTOMER_A,
  customer_name: "客户甲",
  warehouse_id: WAREHOUSE,
  warehouse_name: "主仓",
  reason: "订单原因" + id,
  amount: "777.7700",
  created_at: "2026-09-05T00:00:00Z",
  shipment_amount: "777.7700",
  shipment_cost: "456.7800",
  return_amount: "0.0000",
  return_cost: "0.0000",
  net_sales_amount: "777.7700",
  net_cost: "456.7800",
  gross_margin: "320.9900",
  lines: [
    {
      id: LINE,
      product_id: PRODUCT,
      product_label: "TEST-BOLT 测试螺栓",
      unit_id: UNIT,
      unit_label: "个",
      qty: "2.000000",
      base_qty: "2.000000",
      unit_to_base_factor: "1.000000",
      conversion_version: 1,
      pricing_mode: "AUTO",
      price_source: {
        source: "standard",
        target_unit_id: UNIT,
        target_factor: "1",
      },
      unit_price: "388.885000",
      amount: "777.7700",
      shipped_base_qty: "0.000000",
      returned_base_qty: "0.000000",
      remaining_base_qty: "2.000000",
      executable_base_qty: "0.000000",
      reserved_base_qty: "0.000000",
      remaining_qty: "2.000000",
      executable_qty: "0.000000",
    },
  ],
});
const quote = (customer: string, price: string) => ({
  customer_id: customer,
  product_id: PRODUCT,
  unit_price: price,
  price_source: {
    source: "customer",
    source_id: customer,
    original_price: price,
    original_unit_id: UNIT,
    original_factor: "1",
    target_unit_id: UNIT,
    target_factor: "1",
  },
});
type GetOptions = {
  params?: { path?: { id: string }; query?: { customer_id?: string } };
};
function requests(rows: unknown[] = [order()]) {
  vi.mocked(api.GET).mockImplementation(((
    path: string,
    options: GetOptions = {},
  ) => {
    if (path === "/api/v1/customers")
      return Promise.resolve(
        ok({
          items: [
            { id: CUSTOMER_A, name: "客户甲", active: true },
            { id: CUSTOMER_B, name: "客户乙", active: true },
          ],
          total: 2,
        }),
      );
    if (path === "/api/v1/warehouses")
      return Promise.resolve(
        ok({
          items: [{ id: WAREHOUSE, name: "主仓", active: true }],
          total: 1,
        }),
      );
    if (path === "/api/v1/sales/price-quote")
      return Promise.resolve(
        ok(quote(options.params?.query?.customer_id ?? CUSTOMER_A, "15")),
      );
    if (path.endsWith("/{id}"))
      return Promise.resolve(ok(order(options.params?.path?.id)));
    return Promise.resolve(
      ok({ items: rows, total: rows.length, page: 1, page_size: 25 }),
    );
  }) as never);
}
function show(permissions = read) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const content = (current: string[]) => (
    <QueryClientProvider client={client}>
      <SalesWorkspace permissions={current} />
    </QueryClientProvider>
  );
  const view = render(content(permissions));
  return {
    ...view,
    setPermissions: (current: string[]) => view.rerender(content(current)),
  };
}
async function editNew() {
  fireEvent.click(screen.getByRole("button", { name: "新建销售订单" }));
  const customer = await screen.findByRole("combobox", { name: "销售客户" });
  await waitFor(() =>
    expect(
      within(customer).getByRole("option", { name: /客户甲/ }),
    ).toBeInTheDocument(),
  );
  fireEvent.change(customer, { target: { value: CUSTOMER_A } });
  fireEvent.change(screen.getByRole("combobox", { name: "出库仓库" }), {
    target: { value: WAREHOUSE },
  });
  fireEvent.change(screen.getByLabelText("销售单据原因"), {
    target: { value: "测试开单" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加销售商品" }));
  fireEvent.click(screen.getByRole("button", { name: "选入测试商品" }));
}
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.resetAllMocks();
});

test("sales without read permission makes no requests", () => {
  show([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看销售");
  expect(api.GET).not.toHaveBeenCalled();
});

test("quantity reader hides commercial and cost fields even if a response contains them", async () => {
  requests();
  show();
  expect(await screen.findByText("SO-A")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "查看" }));
  expect(
    await screen.findByRole("region", { name: "销售订单详情" }),
  ).toBeVisible();
  expect(
    screen.queryByText(/777\.77|456\.78|320\.99|388\.885/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "新建销售订单" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("tab", { name: "客户成交历史" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "确认订单" }),
  ).not.toBeInTheDocument();
});

test("price-only sales reader cannot see inventory cost or gross margin", async () => {
  requests();
  show([...read, "product.price.read"]);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  const detail = await screen.findByRole("region", { name: "销售订单详情" });
  expect(within(detail).getAllByText(/777\.77/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/456\.78|320\.99/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: /成本|毛利/ }),
  ).not.toBeInTheDocument();
});

test("sales loading and empty states remain distinct", async () => {
  let resolve!: (value: never) => void;
  vi.mocked(api.GET).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  show();
  expect(screen.getByText(/正在加载/)).toBeVisible();
  resolve(ok({ items: [], total: 0, page: 1, page_size: 25 }));
  expect(await screen.findByText(/暂无销售记录/)).toBeVisible();
});

test("sales API forbidden response is explicit", async () => {
  vi.mocked(api.GET).mockResolvedValue({
    error: { detail: "销售读取权限已撤销" },
    response: new Response(null, { status: 403 }),
  } as never);
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "销售读取权限已撤销",
  );
});

test("incomplete sales draft is blocked before any write", async () => {
  requests([]);
  show(seller);
  fireEvent.click(screen.getByRole("button", { name: "新建销售订单" }));
  fireEvent.click(screen.getByRole("button", { name: "保存销售草稿" }));
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("alert")
        .some((item) => /请/.test(item.textContent ?? "")),
    ).toBe(true),
  );
  expect(api.POST).not.toHaveBeenCalled();
});

test("late sales detail cannot replace a newly selected order", async () => {
  let resolveFirst!: (value: never) => void;
  vi.mocked(api.GET).mockImplementation(((
    path: string,
    options: GetOptions,
  ) => {
    if (path.endsWith("/{id}")) {
      if (options.params?.path?.id === "A")
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      return Promise.resolve(ok(order("B")));
    }
    return Promise.resolve(ok({ items: [order("A"), order("B")], total: 2 }));
  }) as never);
  show();
  fireEvent.click((await screen.findAllByRole("button", { name: "查看" }))[0]);
  expect(await screen.findByText(/正在加载单据详情/)).toBeVisible();
  fireEvent.click(screen.getAllByRole("button", { name: "查看" })[1]);
  expect(await screen.findByText(/订单原因B/)).toBeVisible();
  resolveFirst(ok(order("A")));
  await waitFor(() =>
    expect(screen.queryByText(/订单原因A/)).not.toBeInTheDocument(),
  );
  expect(screen.getByText(/订单原因B/)).toBeVisible();
});

test("duplicate command clicks and response loss preserve the exact retry key and body", async () => {
  requests();
  let reject!: (error: Error) => void;
  vi.mocked(api.POST).mockImplementationOnce(
    () =>
      new Promise((_, failure) => {
        reject = failure;
      }),
  );
  show(seller);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认订单" }));
  const submit = screen.getByRole("button", { name: "确认执行" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(api.POST).toHaveBeenCalledTimes(1);
  reject(new Error("lost response"));
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

test.each([
  ["DOCUMENT_VERSION_CONFLICT", "单据版本已变化，请重新加载"],
  ["INSUFFICIENT_STOCK", "可用库存不足，请复核数量"],
  ["RETURN_QUOTE_CHANGED", "可退金额已变化，请重新保存草稿核对"],
])(
  "business conflict %s does not silently retry or dismiss the review",
  async (code, detail) => {
    requests();
    vi.mocked(api.POST).mockResolvedValueOnce({
      error: { code, detail },
      response: new Response(null, { status: 409 }),
    } as never);
    show(seller);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认订单" }));
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    expect(
      await within(screen.getByRole("dialog")).findByText(detail),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试原提交" }),
    ).not.toBeInTheDocument();
    expect(api.POST).toHaveBeenCalledTimes(1);
  },
);

test("customer change clears manual price and ignores an older customer's pending quote", async () => {
  requests([]);
  const base = vi.mocked(api.GET).getMockImplementation()! as unknown as (
    path: string,
    options?: GetOptions,
  ) => Promise<unknown>;
  let resolveOld!: (value: never) => void;
  vi.mocked(api.GET).mockImplementation(((
    path: string,
    options: GetOptions,
  ) => {
    if (path === "/api/v1/sales/price-quote") {
      if (options.params?.query?.customer_id === CUSTOMER_A)
        return new Promise((resolve) => {
          resolveOld = resolve;
        });
      return Promise.resolve(ok(quote(CUSTOMER_B, "27.25")));
    }
    return base(path, options);
  }) as never);
  show(seller);
  await editNew();
  await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
  fireEvent.change(screen.getByRole("combobox", { name: "定价方式" }), {
    target: { value: "MANUAL" },
  });
  fireEvent.change(screen.getByLabelText("销售单位单价", { exact: true }), {
    target: { value: "19" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "销售客户" }), {
    target: { value: CUSTOMER_B },
  });
  expect(
    screen.getByLabelText("销售单位单价", { exact: true }),
  ).not.toHaveValue("19");
  resolveOld(ok(quote(CUSTOMER_A, "88")));
  await waitFor(() =>
    expect(
      screen.getByLabelText("销售单位单价", { exact: true }),
    ).not.toHaveValue("88"),
  );
  expect(
    screen.getAllByText(
      "客户已更换，所有商品价格均需重新复核；请获取建议价或明确填写手动单价。",
    ).length,
  ).toBeGreaterThan(0);
  expect(api.POST).not.toHaveBeenCalled();
});

test("lost draft response locks editable fields and repeats the original serialized draft", async () => {
  requests([]);
  let reject!: (error: Error) => void;
  vi.mocked(api.POST).mockImplementationOnce(
    () =>
      new Promise((_, failure) => {
        reject = failure;
      }),
  );
  show(seller);
  await editNew();
  await waitFor(() =>
    expect(screen.getByLabelText("销售单位单价", { exact: true })).toHaveValue(
      "15",
    ),
  );
  const save = screen.getByRole("button", { name: "保存销售草稿" });
  fireEvent.click(save);
  fireEvent.click(save);
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
  reject(new Error("server response lost"));
  expect(await screen.findByText(/提交结果待确认/)).toBeVisible();
  expect(screen.getByLabelText("销售单据原因")).toBeDisabled();
  expect(screen.getByLabelText("销售数量", { exact: true })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "销售客户" })).toBeDisabled();
  vi.mocked(api.POST).mockResolvedValueOnce(
    ok({ id: "A", status: "DRAFT", version: 1, request_id: "saved" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.POST).mock.calls[1]).toEqual(
    vi.mocked(api.POST).mock.calls[0],
  );
});

test("revoking price permission removes already loaded quote sources from the editor", async () => {
  requests([]);
  const view = show(seller);
  await editNew();
  await waitFor(() =>
    expect(screen.getByLabelText("销售单位单价", { exact: true })).toHaveValue(
      "15",
    ),
  );
  expect(screen.getByText("价格来源：客户专属价")).toBeInTheDocument();
  expect(screen.getByText(/原单位价 15/)).toBeInTheDocument();
  view.setPermissions(
    seller.filter((permission) => permission !== "product.price.read"),
  );
  expect(
    screen.queryByLabelText("销售单位单价", { exact: true }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/价格来源：|原单位价 15|所选单位换算率/),
  ).not.toBeInTheDocument();
  expect(api.POST).not.toHaveBeenCalled();
});

test.each([
  { status: "DRAFT", cost: null, visible: "11", absent: "12.34" },
  { status: "POSTED", cost: "12.3400", visible: "12.34", absent: "11" },
])(
  "return $status shows draft cost quote or the actual posted movement cost",
  async ({ status, cost, visible, absent }) => {
    window.history.replaceState({}, "", "/sales?tab=RETURN&document=R");
    const document = {
      id: "R",
      number: "SR-TEST",
      kind: "RETURN",
      original_document_id: "S",
      status,
      version: 1,
      order_id: "A",
      order_number: "SO-A",
      customer_id: CUSTOMER_A,
      customer_name: "客户甲",
      warehouse_id: WAREHOUSE,
      warehouse_name: "主仓",
      reason: "退货成本复核",
      amount: "15.0000",
      actual_cost: cost,
      lines: [
        {
          id: LINE,
          order_line_id: "OL",
          shipment_line_id: "SL",
          original_line_id: "SL",
          product_id: PRODUCT,
          product_label: "TEST-BOLT 测试螺栓",
          unit_id: UNIT,
          unit_label: "个",
          qty: "1.000000",
          base_qty: "1.000000",
          unit_to_base_factor: "1.000000",
          conversion_version: 1,
          unit_price: "15.000000",
          amount: "15.0000",
          actual_cost: cost,
          return_cost: "11.0000",
          returned_qty: "0.000000",
          returned_base_qty: "0.000000",
          returnable_qty: "0.000000",
        },
      ],
    };
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      Promise.resolve(
        ok(path.endsWith("/{id}") ? document : { items: [], total: 0 }),
      )) as never);
    show([...read, "product.price.read", "product.cost.read"]);
    const region = await screen.findByRole("region", {
      name: "销售库存单据详情",
    });
    expect(within(region).getAllByText(visible).length).toBeGreaterThan(0);
    expect(within(region).queryByText(absent)).not.toBeInTheDocument();
    if (status === "DRAFT")
      expect(
        within(region).getByRole("columnheader", { name: "原出库成本回收" }),
      ).toBeVisible();
  },
);

test("uncertain write retains original key and locked draft through a revoked-permission retry", async () => {
  requests([]);
  let reject!: (error: Error) => void;
  vi.mocked(api.POST).mockImplementationOnce(
    () =>
      new Promise((_, failure) => {
        reject = failure;
      }),
  );
  const view = show(seller);
  await editNew();
  await waitFor(() =>
    expect(screen.getByLabelText("销售单位单价", { exact: true })).toHaveValue(
      "15",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存销售草稿" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
  reject(new Error("response lost after commit"));
  expect(await screen.findByText(/提交结果待确认/)).toBeVisible();
  vi.mocked(api.POST).mockResolvedValueOnce({
    error: { detail: "当前保存权限已撤销" },
    response: new Response(null, { status: 403 }),
  } as never);
  // The server has revoked permission while this browser still has its prior profile.
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
  expect(await screen.findByText(/当前保存权限已撤销/)).toBeVisible();
  view.setPermissions(
    seller.filter((permission) => permission !== "sales.order.write"),
  );
  expect(screen.getByLabelText("销售单据原因")).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "销售客户" })).toBeDisabled();
  expect(screen.getByLabelText("销售数量", { exact: true })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重试原提交" })).toBeEnabled();
  view.setPermissions(seller);
  vi.mocked(api.POST).mockResolvedValueOnce(
    ok({
      id: "A",
      status: "DRAFT",
      version: 1,
      request_id: "original-receipt",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(3));
  expect(vi.mocked(api.POST).mock.calls[1]).toEqual(
    vi.mocked(api.POST).mock.calls[0],
  );
  expect(vi.mocked(api.POST).mock.calls[2]).toEqual(
    vi.mocked(api.POST).mock.calls[0],
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
