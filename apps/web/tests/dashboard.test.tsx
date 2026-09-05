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
import { DashboardWorkspace } from "@/features/dashboard/workspace";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn() },
  ApiError: class extends Error {},
}));
type Options = {
  signal?: AbortSignal;
  params?: { query?: Record<string, unknown> };
};
const get = api.GET as unknown as Mock<
  (
    path: string,
    options?: Options,
  ) => Promise<{ data?: unknown; error?: unknown; response: Response }>
>;
const all = [
  "dashboard.read",
  "sales.read",
  "product.price.read",
  "product.cost.read",
  "inventory.read",
  "funds.ar.read",
  "funds.ap.read",
  "replenishment.read",
  "catalog.read",
  "purchase.read",
  "supplier.read",
];
const scope = {
  as_of: "2026-09-06T10:00:00Z",
  business_today: "2026-09-06",
  business_timezone: "Asia/Shanghai",
  date_from: "2026-08-08",
  date_to: "2026-09-06",
  restatement_notice: "按当前有效已过账事实重算，不是会计利润。",
};
const overview = () => ({
  ...scope,
  sales: {
    shipment_count: 3,
    return_count: 1,
    shipment_amount: "150.0001",
    return_amount: "50.0000",
    net_sales_amount: "100.0001",
    cost_status: "AVAILABLE",
    net_cost: "70.0000",
    gross_margin: "30.0001",
    daily: [
      {
        date: "2026-09-05",
        shipment_count: 3,
        return_count: 1,
        net_sales_amount: "100.0001",
        cost_status: "AVAILABLE",
        net_cost: "70.0000",
        gross_margin: "30.0001",
      },
    ],
  },
  cash_ar: {
    integration_status: "ACTIVE",
    unmapped_document_count: 0,
    settlement_amount: "80.1234",
    refund_amount: "20.0000",
    net_cash_amount: "60.1234",
    daily: [
      {
        date: "2026-09-05",
        settlement_amount: "80.1234",
        refund_amount: "20.0000",
        net_cash_amount: "60.1234",
      },
    ],
  },
  cash_ap: {
    integration_status: "ACTIVE",
    unmapped_document_count: 0,
    settlement_amount: "900.0000",
    refund_amount: "100.0000",
    net_cash_amount: "800.0000",
    daily: [],
  },
  current_ar: {
    integration_status: "ACTIVE",
    unmapped_document_count: 0,
    source_count: 2,
    balance: "401.1234",
    settlement_amount: "411.1234",
    refund_amount: "10.0000",
  },
  current_ap: {
    integration_status: "ACTIVE",
    unmapped_document_count: 0,
    source_count: 1,
    balance: "99.0000",
    settlement_amount: "99.0000",
    refund_amount: "0",
  },
  inventory: { product_count: 5, low_stock_count: 2, valuation: "12345.6789" },
  replenishment: { candidate_count: 2, suggested_count: 1 },
});
const ok = (data: unknown) => ({ data, response: new Response() });
function mocks(value = overview()) {
  get.mockImplementation(async (path, options) =>
    path.endsWith("/sources")
      ? ok({
          ...scope,
          metric: options?.params?.query?.metric,
          items: [
            {
              id: "line-1",
              number: "SO-001",
              kind: "SHIPMENT",
              label: "五金螺丝",
              party_name: "客户甲",
              document_id: "shipment-id",
              qty: "10.000000",
              unit_name: "个",
              amount: "150.0001",
              signed_amount: "150.0001",
              cost_status: "AVAILABLE",
              signed_cost: "120.0000",
              gross_margin: "30.0001",
            },
          ],
          total: 1,
          page: 1,
          page_size: 25,
        })
      : ok(value),
  );
}
function show(permissions = all) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const element = (current: string[]) => (
    <QueryClientProvider client={client}>
      <DashboardWorkspace permissions={current} />
    </QueryClientProvider>
  );
  const view = render(element(permissions));
  return {
    ...view,
    permissions: (current: string[]) => view.rerender(element(current)),
  };
}
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

test("dashboard permission alone grants no underlying values, and no dashboard access performs no requests", async () => {
  mocks();
  const view = show([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看经营概览");
  expect(get).not.toHaveBeenCalled();
  view.permissions(["dashboard.read"]);
  await screen.findByText(/业务时区 Asia\/Shanghai/);
  expect(screen.getByText(/尚无可查看的业务指标权限/)).toBeVisible();
  expect(screen.queryByText("100.0001")).not.toBeInTheDocument();
  expect(screen.queryByText("12345.6789")).not.toBeInTheDocument();
});

test("quantity-only dashboard masks sales price, cost, cash and cached privileged payload", async () => {
  mocks();
  show(["dashboard.read", "sales.read"]);
  const sales = await screen.findByRole("region", { name: "期间销售" });
  expect(sales).toHaveTextContent("已过账出库单数");
  expect(within(sales).queryByText("净销售额")).not.toBeInTheDocument();
  expect(screen.queryByText("100.0001")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "期间收款" }),
  ).not.toBeInTheDocument();
  fireEvent.click(within(sales).getByRole("button", { name: "查看来源" }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("五金螺丝")).toBeVisible();
  expect(within(dialog).queryByText("150.0001")).not.toBeInTheDocument();
  expect(within(dialog).queryByText("120.0000")).not.toBeInTheDocument();
});

test("period/current sections stay separate, AP cash is outflow, and drilldown freezes server dates", async () => {
  mocks();
  show();
  const period = await screen.findByRole("region", { name: "期间经营数据" });
  expect(period).toHaveTextContent("100.0001");
  expect(period).not.toHaveTextContent("401.1234");
  const current = screen.getByRole("region", { name: "当前经营余额" });
  expect(current).toHaveTextContent("不是所选日期的历史余额");
  expect(current).toHaveTextContent("401.1234");
  const payable = screen.getByRole("region", { name: "期间付款" });
  expect(payable).toHaveTextContent("净流出800.0000");
  expect(payable).not.toHaveTextContent("净流入");
  fireEvent.click(
    within(screen.getByRole("region", { name: "期间销售" })).getByRole(
      "button",
      { name: "查看来源" },
    ),
  );
  const dialog = await screen.findByRole("dialog");
  expect(
    await within(dialog).findByRole("link", { name: "查看销售单据" }),
  ).toHaveAttribute("href", "/sales?document=shipment-id");
  expect(
    get.mock.calls.find(([path]) => path.endsWith("/sources"))?.[1]?.params
      ?.query,
  ).toMatchObject({
    metric: "sales",
    date_from: "2026-08-08",
    date_to: "2026-09-06",
    page: 1,
    page_size: 25,
  });
});

test("unenabled funds omit money and incomplete current balances disclose scope", async () => {
  const value = overview();
  value.cash_ar.integration_status = "NOT_ENABLED";
  value.current_ap.integration_status = "INCOMPLETE";
  value.current_ap.unmapped_document_count = 2;
  mocks(value);
  show();
  const cash = await screen.findByRole("region", { name: "期间收款" });
  expect(cash).toHaveTextContent("资金管理尚未启用");
  expect(cash).not.toHaveTextContent("80.1234");
  expect(screen.getByRole("region", { name: "当前应付" })).toHaveTextContent(
    "还有 2 张历史单据未绑定期初",
  );
});

test("missing cost facts never display cached cost or margin as available", async () => {
  const value = overview();
  value.sales.cost_status = "MISSING_FACTS";
  value.sales.daily[0].cost_status = "MISSING_FACTS";
  mocks(value);
  show();
  const region = await screen.findByRole("region", { name: "期间销售" });
  expect(region).toHaveTextContent("成本与毛利暂不可用");
  expect(region).not.toHaveTextContent("70.0000");
  expect(region).not.toHaveTextContent("30.0001");
});

test("revoking metric access immediately hides an open cached source drawer", async () => {
  mocks();
  const view = show();
  const region = await screen.findByRole("region", { name: "期间销售" });
  fireEvent.click(within(region).getByRole("button", { name: "查看来源" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    (await within(dialog).findAllByText("150.0001")).length,
  ).toBeGreaterThan(0);
  view.permissions(["dashboard.read"]);
  expect(within(dialog).getByRole("alert")).toHaveTextContent("已无权查看");
  expect(within(dialog).queryByText("150.0001")).not.toBeInTheDocument();
});

test("changing a date filter waits for a fresh server result instead of flashing old amounts", async () => {
  mocks();
  show();
  await screen.findByRole("region", { name: "期间销售" });
  let finish!: (value: ReturnType<typeof ok>) => void;
  get.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.change(screen.getByLabelText("概览开始日期"), {
    target: { value: "2026-09-01" },
  });
  fireEvent.change(screen.getByLabelText("概览结束日期"), {
    target: { value: "2026-09-02" },
  });
  fireEvent.click(screen.getByRole("button", { name: "更新概览" }));
  expect(screen.queryByText("100.0001")).not.toBeInTheDocument();
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  finish(ok({ ...overview(), date_from: "2026-09-01", date_to: "2026-09-02" }));
  expect(
    await screen.findByText(/统计日期 2026-09-01 至 2026-09-02/),
  ).toBeVisible();
});

test.each([
  ["期间收款", "cash_ar", "AR"],
  ["期间付款", "cash_ap", "AP"],
])(
  "%s source uses the cash record ID even without a sales document ID",
  async (label, metric, side) => {
    get.mockImplementation(async (path) =>
      path.endsWith("/sources")
        ? ok({
            ...scope,
            metric,
            items: [
              {
                id: "cash-fact",
                number: "CASH-01",
                kind: "SETTLEMENT",
                label: "往来甲",
                amount: "12.3456",
                signed_amount: "12.3456",
              },
            ],
            total: 1,
            page: 1,
            page_size: 25,
          })
        : ok(overview()),
    );
    show();
    const card = await screen.findByRole("region", { name: label });
    fireEvent.click(within(card).getByRole("button", { name: "查看来源" }));
    expect(
      await screen.findByRole("link", { name: "查看收付款" }),
    ).toHaveAttribute("href", `/funds?side=${side}&cash=cash-fact`);
  },
);

test("low-stock drilldown displays the authoritative minimum-stock threshold", async () => {
  get.mockImplementation(async (path) =>
    path.endsWith("/sources")
      ? ok({
          ...scope,
          metric: "low_stock",
          items: [
            {
              id: "product-low",
              number: "LOW-01",
              kind: "PRODUCT",
              label: "缺货商品",
              available_qty: "1.000001",
              min_stock_qty: "2.222222",
              unit_name: "个",
            },
          ],
          total: 1,
          page: 1,
          page_size: 25,
        })
      : ok(overview()),
  );
  show();
  const card = await screen.findByRole("region", { name: "当前库存" });
  fireEvent.click(within(card).getByRole("button", { name: "查看低库存商品" }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("2.222222")).toBeVisible();
  expect(within(dialog).getByText("最低库存")).toBeVisible();
});
