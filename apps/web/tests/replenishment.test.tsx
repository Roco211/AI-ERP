import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi, type Mock } from "vitest";
import { ReplenishmentWorkspace } from "@/features/replenishment/workspace";
import {
  replenishmentReadPermissions,
  replenishmentCreatePermissions,
} from "@/features/operations/permissions";
import { api } from "@/lib/api";
import type { Preview, Suggestion } from "@/features/replenishment/client";
vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
  ApiError: class extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));
type Options = {
  signal?: AbortSignal;
  body?: unknown;
  params?: {
    query?: Record<string, unknown>;
    header?: { "Idempotency-Key": string };
  };
};
type Result = { data?: unknown; error?: unknown; response: Response };
const get = api.GET as unknown as Mock<
  (path: string, options?: Options) => Promise<Result>
>;
const post = api.POST as unknown as Mock<
  (path: string, options: Options) => Promise<Result>
>;
const read = [...replenishmentReadPermissions],
  all = [...replenishmentCreatePermissions];
const ok = (data: unknown): Result => ({ data, response: new Response() });
const scope = {
  as_of: "2026-09-06T00:00:00Z",
  business_timezone: "Asia/Shanghai",
  window_start: "2026-08-06T16:00:00Z",
  window_end: "2026-09-05T16:00:00Z",
  algorithm_version: "replenishment-v1",
};
const suggestion: Suggestion = {
  product_id: "product-A",
  sku: "REPL-001",
  name: "补货螺丝",
  product_version: 1,
  category_id: "category",
  base_unit_id: "unit",
  base_unit_name: "个",
  default_purchase_unit_id: "box",
  preferred_supplier_id: "supplier",
  preferred_supplier_name: "供应商甲",
  lead_days: null,
  available_qty: "3.123456",
  open_purchase_qty: "1.234567",
  shipped_qty: "7.111111",
  returned_qty: "2.000000",
  net_sales_qty: "5.111111",
  daily_sales_qty: "0.17037036666666666667",
  safety_stock_qty: "10.000000",
  minimum_reorder_qty: "2.000000",
  reorder_point: "10.000000",
  target_stock: "10.000000",
  inventory_position: "4.358023",
  gap: "5.641977",
  candidate: true,
  suggested_base_qty: "5.641977",
  days_of_stock: "18.3333",
  reasons: ["MISSING_LEAD_DAYS"],
  basis_hash: "a".repeat(64),
};
function preview(values: Partial<Preview> = {}): Preview {
  return {
    ...scope,
    supplier_id: "supplier",
    supplier_name: "供应商甲",
    warehouse_id: "warehouse",
    warehouse_name: "仓库甲",
    reason: "门店补货",
    can_create: true,
    blocking_reasons: [],
    total_amount: "123.4567",
    confirmation_hash: "b".repeat(64),
    lines: [
      {
        product_id: "product-A",
        product_label: "补货螺丝",
        basis: suggestion,
        unit_id: "unit",
        unit_name: "个",
        unit_to_base_factor: "1.000000",
        conversion_version: 1,
        qty: "5.641977",
        base_qty: "5.641977",
        quantity_source: "SUGGESTION",
        quantity_reason: null,
        price_source: "MANUAL",
        unit_price: "21.8819",
        historical_price: null,
        amount: "123.4567",
        blocking_reasons: [],
        warnings: [],
      },
    ],
    ...values,
  };
}
function mock() {
  get.mockImplementation(async (path) =>
    path.endsWith("/suggestions")
      ? ok({ ...scope, items: [suggestion], total: 1, page: 1, page_size: 25 })
      : ok({
          items: path.endsWith("suppliers")
            ? [
                { id: "supplier", code: "SUP", name: "供应商甲" },
                { id: "supplier-B", code: "OTHER", name: "供应商乙" },
              ]
            : path.endsWith("warehouses")
              ? [{ id: "warehouse", code: "WH", name: "仓库甲" }]
              : path.endsWith("units")
                ? [
                    { id: "unit", code: "EA", name: "个" },
                    { id: "box", code: "BOX", name: "盒" },
                  ]
                : [{ id: "category", code: "CAT", name: "五金" }],
          total: 1,
          page: 1,
          page_size: 100,
        }),
  );
  post.mockImplementation(async (path) =>
    path.endsWith("purchase-preview")
      ? ok(preview())
      : ok({
          id: "order-A",
          status: "DRAFT",
          version: 1,
          creation_id: "creation-A",
          request_id: "r",
        }),
  );
}
function show(permissions: string[] = all) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const element = (value: string[]) => (
    <QueryClientProvider client={client}>
      <ReplenishmentWorkspace permissions={value} />
    </QueryClientProvider>
  );
  const view = render(element(permissions));
  return {
    ...view,
    permissions: (value: string[]) => view.rerender(element(value)),
  };
}
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
async function edit() {
  fireEvent.click(await screen.findByLabelText("选择补货商品 REPL-001"));
  fireEvent.click(screen.getByRole("button", { name: "复核采购草稿（1）" }));
  const dialog = await screen.findByRole("dialog", {
    name: "补货采购草稿复核",
  });
  await within(dialog).findByRole("option", { name: "WH · 仓库甲" });
  fireEvent.change(within(dialog).getByLabelText("补货采购供应商"), {
    target: { value: "supplier" },
  });
  fireEvent.change(within(dialog).getByLabelText("补货收货仓库"), {
    target: { value: "warehouse" },
  });
  fireEvent.change(within(dialog).getByLabelText("补货采购原因"), {
    target: { value: "门店补货" },
  });
  return dialog;
}

test("read requires every underlying permission; quantity-only can inspect full basis but cannot purchase or read costs", async () => {
  mock();
  const view = show(["replenishment.read"]);
  expect(get).not.toHaveBeenCalled();
  view.permissions(read);
  fireEvent.click(await screen.findByRole("button", { name: "查看补货依据" }));
  const dialog = await screen.findByRole("dialog", { name: "补货计算依据" });
  expect(within(dialog).getByText("0.17037036666666666667")).toBeVisible();
  expect(within(dialog).getByText("未配置")).toBeVisible();
  expect(
    screen.queryByLabelText("选择补货商品 REPL-001"),
  ).not.toBeInTheDocument();
  expect(get.mock.calls.some(([path]) => path.endsWith("/warehouses"))).toBe(
    false,
  );
  expect(post).not.toHaveBeenCalled();
});

test("server preview preserves blank suggested quantity, explicit zero price and exact amounts, then creates only captured DRAFT", async () => {
  mock();
  show();
  const dialog = await edit();
  fireEvent.change(within(dialog).getByLabelText("补货采购单价1"), {
    target: { value: "0" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  const review = await screen.findByRole("region", { name: "补货采购预览" });
  expect(within(review).getAllByText("123.4567")).toHaveLength(2);
  const body = post.mock.calls[0][1].body;
  expect(body).toEqual({
    supplier_id: "supplier",
    warehouse_id: "warehouse",
    reason: "门店补货",
    lines: [
      {
        product_id: "product-A",
        basis_hash: suggestion.basis_hash,
        unit_id: "box",
        qty: null,
        quantity_reason: null,
        price_source: "MANUAL",
        unit_price: "0",
      },
    ],
  });
  fireEvent.click(
    within(review).getByRole("button", { name: "确认创建采购草稿" }),
  );
  const link = await screen.findByRole("link", { name: "查看采购草稿" });
  expect(link).toHaveAttribute("href", "/purchase?order=order-A");
  expect(post.mock.calls[1][0]).toBe("/api/v1/replenishment/purchase-orders");
  expect(post.mock.calls[1][1].body).toEqual({
    ...(body as object),
    confirmation_hash: "b".repeat(64),
  });
});

test("missing price or inexact conversion blocks create and historical selection sends no stale manual price", async () => {
  mock();
  post.mockResolvedValue(
    ok(
      preview({
        can_create: false,
        total_amount: null,
        blocking_reasons: ["QUANTITY_CONVERSION_REQUIRED"],
      }),
    ),
  );
  show();
  const dialog = await edit();
  fireEvent.change(within(dialog).getByLabelText("补货采购单价1"), {
    target: { value: "88.8888" },
  });
  fireEvent.change(within(dialog).getByLabelText("补货单价依据1"), {
    target: { value: "HISTORY" },
  });
  expect(
    within(dialog).queryByLabelText("补货采购单价1"),
  ).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  expect(
    await screen.findByText(
      "建议量无法精确换算为所选采购单位，请改选基础单位，或明确填写数量与调整原因。",
    ),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "确认创建采购草稿" }),
  ).toBeDisabled();
  expect(post.mock.calls[0][1].body).toMatchObject({
    lines: [{ price_source: "HISTORY", unit_price: null }],
  });
});

test("changing supplier aborts a late quote and invalidates an already reviewed draft", async () => {
  mock();
  let finish!: (result: Result) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  show();
  const dialog = await edit();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  fireEvent.change(within(dialog).getByLabelText("补货采购供应商"), {
    target: { value: "supplier-B" },
  });
  finish(ok(preview()));
  await waitFor(() => expect(post.mock.calls[0][1].signal?.aborted).toBe(true));
  expect(
    screen.queryByRole("region", { name: "补货采购预览" }),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      within(dialog).getByRole("button", { name: "预览采购草稿" }),
    ).toBeEnabled(),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  await screen.findByRole("region", { name: "补货采购预览" });
  fireEvent.change(within(dialog).getByLabelText("补货采购数量1"), {
    target: { value: "2.000001" },
  });
  expect(
    screen.queryByRole("button", { name: "确认创建采购草稿" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText("选择或依据已变化，请重新预览并复核。"),
  ).toBeVisible();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  await screen.findByText(/手工数量须填写调整原因/);
  expect(post).toHaveBeenCalledTimes(2);
});

test("lost create response then revoked permissions preserve exact original submission and keep all fields locked", async () => {
  mock();
  let creationAttempts = 0;
  post.mockImplementation(async (path) => {
    if (path.endsWith("purchase-preview")) return ok(preview());
    creationAttempts++;
    if (creationAttempts === 1) throw new TypeError("响应中断");
    if (creationAttempts === 2)
      return {
        error: { detail: "当前没有权限" },
        response: new Response(null, { status: 403 }),
      };
    return ok({
      id: "order-A",
      status: "DRAFT",
      version: 1,
      creation_id: "creation-A",
      request_id: "r",
    });
  });
  const view = show();
  const dialog = await edit();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  const save = await screen.findByRole("button", { name: "确认创建采购草稿" });
  fireEvent.click(save);
  fireEvent.click(save);
  await screen.findByText(/提交结果待确认/);
  expect(creationAttempts).toBe(1);
  expect(within(dialog).getByLabelText("补货采购原因")).toBeDisabled();
  expect(
    within(dialog).getByRole("button", { name: "关闭采购复核" }),
  ).toBeDisabled();
  view.permissions(read);
  expect(screen.queryByText("123.4567")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await screen.findByText(/当前没有权限.*提交结果待确认/);
  view.permissions(all);
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await screen.findByRole("link", { name: "查看采购草稿" });
  const calls = post.mock.calls.filter(([path]) =>
    path.endsWith("purchase-orders"),
  );
  expect(calls).toHaveLength(3);
  expect(calls[1][1].params?.header).toEqual(calls[0][1].params?.header);
  expect(calls[2][1].body).toEqual(calls[0][1].body);
});

test("basis conflict requires refresh and never silently regenerates confirmation", async () => {
  mock();
  post.mockImplementation(async (path) =>
    path.endsWith("purchase-preview")
      ? ok(preview())
      : {
          error: { detail: "补货依据已变化，请重新复核" },
          response: new Response(null, { status: 409 }),
        },
  );
  show();
  const dialog = await edit();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "确认创建采购草稿" }),
  );
  expect(await screen.findByText("补货依据已变化，请重新复核")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "重试原提交" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "刷新后重新复核" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(
    screen.getByRole("button", { name: "复核采购草稿（0）" }),
  ).toBeDisabled();
  expect(post).toHaveBeenCalledTimes(2);
});

test("history review exposes its exact receipt, frozen conversion and selected-unit quote without browser arithmetic", async () => {
  mock();
  const value = preview();
  value.lines[0].price_source = "HISTORY";
  value.lines[0].historical_price = {
    document_id: "historical-receipt",
    document_number: "GR-001",
    line_id: "historical-line",
    posted_at: "2026-09-01T04:00:00Z",
    unit_id: "box",
    unit_label: "盒",
    unit_price: "88.123456",
    unit_to_base_factor: "4.000000",
    conversion_version: 7,
    selected_unit_price: "22.030864",
  };
  value.lines[0].warnings = ["SUPPLIER_DIFFERS_FROM_BASIS"];
  post.mockResolvedValue(ok(value));
  show();
  const dialog = await edit();
  fireEvent.change(within(dialog).getByLabelText("补货单价依据1"), {
    target: { value: "HISTORY" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "预览采购草稿" }));
  const review = await screen.findByRole("region", { name: "补货采购预览" });
  expect(within(review).getByRole("link", { name: "GR-001" })).toHaveAttribute(
    "href",
    "/purchase?document=historical-receipt",
  );
  expect(within(review).getByText("88.123456")).toBeVisible();
  expect(within(review).getByText("22.030864")).toBeVisible();
  expect(
    within(review).getByText(
      "所选供应商与计算依据中的首选供应商不同，请核对供货提前期和采购条件。",
    ),
  ).toBeVisible();
  expect(
    within(review).queryByText("SUPPLIER_DIFFERS_FROM_BASIS"),
  ).not.toBeInTheDocument();
});
