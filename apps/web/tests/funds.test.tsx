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
import { FundsWorkspace } from "@/features/funds/workspace";
import { OrderFundsSummary } from "@/features/funds/order-summary";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
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

const PARTY = "11111111-1111-4111-8111-111111111111";
const PARTY_B = "22222222-2222-4222-8222-222222222222";
const read = ["funds.ar.read"];
const operator = [
  ...read,
  "funds.ap.read",
  "funds.receive",
  "funds.pay",
  "funds.customer_refund",
  "funds.supplier_refund",
  "funds.opening",
  "funds.adjust",
  "funds.activate",
  "funds.reverse",
  "sales.read",
  "purchase.read",
];
const ok = (data: unknown) => ({ data, response: new Response() }) as never;
const failure = (status: number, detail: string) =>
  ({ error: { detail }, response: new Response(null, { status }) }) as never;
const page = (items: unknown[]) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 25,
});
type Side = "AR" | "AP";
const source = (id = "A", side: Side = "AR") => ({
  id,
  number: "FR-" + id,
  side,
  party_id: PARTY,
  party_name: "往来甲",
  kind: side === "AR" ? "SHIPMENT" : "RECEIPT",
  status: "OPEN",
  source_document_id: "business-" + id,
  source_document_number: "DOC-" + id,
  business_date: "2026-09-06",
  created_at: "2026-09-06T00:00:00Z",
  amount: "700.1234",
  commercial_amount: "700.1234",
  historically_settled_amount: "0.0000",
  settled_amount: "200.1000",
  refunded_amount: "0.0000",
  balance: "500.0234",
  settlement_amount: "500.0234",
  refund_amount: "0.0000",
  reason: "来源说明" + id,
  entries: [],
  cash: [],
});
const cash = (id = "cash-1", side: Side = "AR") => ({
  id,
  number: "FC-" + id,
  side,
  party_id: PARTY,
  party_name: "往来甲",
  kind: "SETTLEMENT",
  status: "POSTED",
  amount: "123.4501",
  business_date: "2026-09-06",
  method: "CASH",
  external_reference: "",
  reason: "收付说明",
  created_at: "2026-09-06T00:00:00Z",
  reversed_at: null,
  reversal_reason: null,
  allocations: [
    {
      cash_id: id,
      cash_number: "FC-" + id,
      cash_kind: "SETTLEMENT",
      cash_status: "POSTED",
      source_id: "A",
      source_number: "FR-A",
      amount: "123.4501",
    },
  ],
});
type Options = {
  params?: {
    path?: { id: string };
    query?: {
      side?: Side;
      status?: string;
      party_id?: string;
      page?: number;
      q?: string;
    };
  };
  body?: Record<string, unknown>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};
type MockRequest = (
  path: string,
  options?: Options,
) => Promise<{ data?: unknown; error?: unknown; response: Response }>;
const getMock = api.GET as unknown as Mock<MockRequest>;
const postMock = api.POST as unknown as Mock<MockRequest>;
function mocks({ enabled = true, cashRows = false } = {}) {
  getMock.mockImplementation(((path: string, options: Options = {}) => {
    const side = options.params?.query?.side ?? "AR";
    if (path === "/api/v1/funds/settings")
      return Promise.resolve(
        ok({
          enabled,
          business_date: enabled ? "2026-09-06" : null,
          currency: "CNY",
          business_today: "2026-09-06",
          business_timezone: "Asia/Shanghai",
        }),
      );
    if (path === "/api/v1/funds/summary")
      return Promise.resolve(
        ok({
          side,
          currency: "CNY",
          balance: "500.0234",
          settlement_amount: "500.0234",
          refund_amount: "0.0000",
          source_amount: "700.1234",
          settled_amount: "200.1000",
          refunded_amount: "0.0000",
          party_count: 2,
        }),
      );
    if (path === "/api/v1/funds/parties")
      return Promise.resolve(
        ok(
          page([
            {
              id: PARTY,
              code: "P01",
              name: "往来甲",
              is_active: true,
              balance: "500.0234",
              settlement_amount: "500.0234",
              refund_amount: "0.0000",
            },
            {
              id: PARTY_B,
              code: "P02",
              name: "往来乙",
              is_active: true,
              balance: "80.0000",
              settlement_amount: "80.0000",
              refund_amount: "0.0000",
            },
          ]),
        ),
      );
    if (path === "/api/v1/funds/sources/{id}")
      return Promise.resolve(ok(source(options.params?.path?.id)));
    if (path === "/api/v1/funds/cash/{id}")
      return Promise.resolve(ok(cash(options.params?.path?.id)));
    if (path === "/api/v1/funds/cash")
      return Promise.resolve(ok(page(cashRows ? [cash("cash-1", side)] : [])));
    if (path === "/api/v1/funds/legacy-documents")
      return Promise.resolve(
        ok(
          page([
            {
              id: "old-doc",
              number: "OLD-1",
              side,
              party_id: PARTY,
              party_name: "往来甲",
              posted_at: "2026-09-01T00:00:00Z",
              commercial_amount: "900.0000",
              returned_amount: "0.0000",
              effective_amount: "900.0000",
            },
          ]),
        ),
      );
    const rows = [source("A", side), source("B", side)];
    if (options.params?.query?.status === "REFUND")
      rows.forEach((row) => {
        row.status = "REFUND";
        row.settlement_amount = "0.0000";
        row.refund_amount = "22.1234";
      });
    return Promise.resolve(ok(page(rows)));
  }) as never);
  postMock.mockImplementation(((path: string, options: Options = {}) => {
    if (path === "/api/v1/funds/cash/preview")
      return Promise.resolve(
        ok({
          side: options.body?.side ?? "AR",
          party_id: options.body?.party_id ?? PARTY,
          party_name: "往来甲",
          kind: options.body?.kind ?? "SETTLEMENT",
          amount: "123.4501",
          allocations: [
            {
              source_id: "A",
              source_number: "FR-A",
              amount: "123.4501",
              available_amount: "500.0234",
            },
          ],
        }),
      );
    return Promise.resolve(
      ok({
        id: path.includes("cash")
          ? "cash-1"
          : path.includes("activate")
            ? "activation"
            : "A",
        status: "POSTED",
        request_id: "test-request",
      }),
    );
  }) as never);
}
function show(permissions = operator) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const content = (current: string[]) => (
    <QueryClientProvider client={client}>
      <FundsWorkspace permissions={current} />
    </QueryClientProvider>
  );
  const view = render(content(permissions));
  return {
    ...view,
    setPermissions: (current: string[]) => view.rerender(content(current)),
  };
}
async function openCash(kind = "登记收款") {
  await screen.findByText("FR-A");
  fireEvent.click(screen.getByRole("button", { name: kind }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(
    within(dialog).getByRole("combobox", { name: "资金往来对象" }),
    { target: { value: PARTY } },
  );
  fireEvent.change(within(dialog).getByLabelText("资金操作说明"), {
    target: { value: "实收核对" },
  });
  fireEvent.click(
    await within(dialog).findByRole("checkbox", { name: "选择来源 FR-A" }),
  );
  return dialog;
}
async function preview(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByRole("button", { name: "预览核销金额" }));
  return within(dialog).findByRole("region", { name: "收付款金额预览" });
}
const cashRequests = () =>
  postMock.mock.calls.filter(([path]) => path === "/api/v1/funds/cash");

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.resetAllMocks();
});

test("no funds permissions causes no data requests", () => {
  mocks();
  show([]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看资金");
  expect(api.GET).not.toHaveBeenCalled();
  expect(api.POST).not.toHaveBeenCalled();
});

test("AR read-only renders exact server strings without AP or cash actions", async () => {
  mocks();
  show(read);
  expect(await screen.findByText("FR-A")).toBeVisible();
  expect(screen.getAllByText("500.0234").length).toBeGreaterThan(0);
  expect(
    screen.queryByRole("button", { name: "应付与付款" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "登记收款" }),
  ).not.toBeInTheDocument();
  expect(
    getMock.mock.calls.every(
      ([, value]) => (value as Options)?.params?.query?.side !== "AP",
    ),
  ).toBe(true);
});

test("source detail and cash allocation navigate using stable source IDs", async () => {
  mocks({ cashRows: true });
  show();
  await screen.findByText("FR-A");
  fireEvent.click(screen.getAllByRole("button", { name: "查看来源" })[0]);
  const sourceRegion = await screen.findByRole("region", {
    name: "资金来源详情",
  });
  expect(
    within(sourceRegion).getByRole("link", { name: "查看原业务单据" }),
  ).toHaveAttribute("href", "/sales?document=business-A");
  fireEvent.click(screen.getByRole("tab", { name: "收款与退款" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看收付款" }));
  const cashRegion = await screen.findByRole("region", { name: "收付款详情" });
  expect(within(cashRegion).getByText("本次收款：123.4501")).toBeVisible();
  fireEvent.click(within(cashRegion).getByRole("button", { name: "查看来源" }));
  expect(
    await screen.findByRole("region", { name: "资金来源详情" }),
  ).toHaveTextContent("FR-A");
});

test("multiple allocations require server preview, and editing invalidates it", async () => {
  mocks();
  show();
  const dialog = await openCash();
  fireEvent.click(
    within(dialog).getByRole("checkbox", { name: "选择来源 FR-B" }),
  );
  fireEvent.change(within(dialog).getByLabelText("核销金额 FR-A"), {
    target: { value: "1.0001" },
  });
  fireEvent.change(within(dialog).getByLabelText("核销金额 FR-B"), {
    target: { value: "2.0000" },
  });
  expect(
    within(dialog).getByRole("button", { name: "确认收款" }),
  ).toBeDisabled();
  const region = await preview(dialog);
  expect(region).toHaveTextContent("本次收款总额：123.4501");
  expect(region).not.toHaveTextContent("3.0001");
  expect(
    within(dialog).getByRole("button", { name: "确认收款" }),
  ).toBeEnabled();
  fireEvent.change(within(dialog).getByLabelText("核销金额 FR-A"), {
    target: { value: "4.0000" },
  });
  expect(
    within(dialog).queryByRole("region", { name: "收付款金额预览" }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).getByRole("button", { name: "确认收款" }),
  ).toBeDisabled();
  expect(cashRequests()).toHaveLength(0);
});

test("late preview cannot restore old amounts after a party change", async () => {
  mocks();
  let resolve!: (value: never) => void;
  const prior = postMock.getMockImplementation()!;
  postMock.mockImplementation(((path: string, options: Options) =>
    path.endsWith("/preview")
      ? new Promise((done) => {
          resolve = done;
        })
      : prior(path as never, options as never)) as never);
  show();
  const dialog = await openCash();
  fireEvent.click(within(dialog).getByRole("button", { name: "预览核销金额" }));
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  const call = postMock.mock.calls.find(([path]) => path.endsWith("/preview"))!;
  fireEvent.change(
    within(dialog).getByRole("combobox", { name: "资金往来对象" }),
    { target: { value: PARTY_B } },
  );
  expect((call[1] as Options).signal?.aborted).toBe(true);
  resolve(
    ok({
      side: "AR",
      party_id: PARTY,
      party_name: "过期客户",
      kind: "SETTLEMENT",
      amount: "999.9999",
      allocations: [],
    }),
  );
  await waitFor(() =>
    expect(
      within(dialog).getByRole("button", { name: "确认收款" }),
    ).toBeDisabled(),
  );
  expect(
    within(dialog).queryByText(/999\.9999|过期客户/),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByLabelText("核销金额 FR-A"),
  ).not.toBeInTheDocument();
});

test("lost cash response locks fields and retries the same key/body after a later 403", async () => {
  mocks();
  const prior = postMock.getMockImplementation()!;
  let attempts = 0;
  postMock.mockImplementation(((path: string, options: Options) => {
    if (path !== "/api/v1/funds/cash")
      return prior(path as never, options as never);
    attempts += 1;
    if (attempts === 1) return Promise.reject(new TypeError("回应丢失"));
    if (attempts === 2) return Promise.resolve(failure(403, "没有收款权限"));
    return Promise.resolve(
      ok({ id: "cash-1", status: "POSTED", request_id: "cash-request" }),
    );
  }) as never);
  show();
  const dialog = await openCash();
  await preview(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "确认收款" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "确认收款" }));
  await within(dialog).findByText(/提交结果待确认/);
  expect(cashRequests()).toHaveLength(1);
  expect(within(dialog).getByLabelText("资金操作说明")).toBeDisabled();
  expect(within(dialog).getByLabelText("核销金额 FR-A")).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "重试原提交" }));
  await within(dialog).findByText(/没有收款权限/);
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "重试原提交" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(cashRequests()).toHaveLength(3);
  const options = cashRequests().map(([, value]) => value as Options);
  expect(options[1].headers).toEqual(options[0].headers);
  expect(options[2].headers).toEqual(options[0].headers);
  expect(options[1].body).toEqual(options[0].body);
  expect(options[2].body).toEqual(options[0].body);
  expect(
    await screen.findByRole("region", { name: "收付款详情" }),
  ).toHaveTextContent("123.4501");
});

test("revoking funds read permission hides an uncertain editor's cached money", async () => {
  mocks();
  const prior = postMock.getMockImplementation()!;
  postMock.mockImplementation(((path: string, options: Options) =>
    path === "/api/v1/funds/cash"
      ? Promise.reject(new TypeError("丢响应"))
      : prior(path as never, options as never)) as never);
  const view = show();
  const dialog = await openCash();
  await preview(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "确认收款" }));
  await within(dialog).findByText(/提交结果待确认/);
  view.setPermissions([]);
  await waitFor(() =>
    expect(screen.queryByLabelText("核销金额 FR-A")).not.toBeInTheDocument(),
  );
  expect(
    screen.queryByText(/500\.0234|123\.4501|700\.1234/),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试原提交" })).toBeEnabled();
});

test("first balance conflict unlocks fields and offers explicit refresh", async () => {
  mocks();
  const prior = postMock.getMockImplementation()!;
  postMock.mockImplementation(((path: string, options: Options) =>
    path === "/api/v1/funds/cash"
      ? Promise.resolve(failure(409, "来源可用余额已变更"))
      : prior(path as never, options as never)) as never);
  show();
  const dialog = await openCash();
  await preview(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "确认收款" }));
  await within(dialog).findByText("来源可用余额已变更");
  expect(within(dialog).getByLabelText("资金操作说明")).toBeEnabled();
  expect(
    within(dialog).getByRole("button", { name: "刷新余额并重新复核" }),
  ).toBeEnabled();
  expect(
    within(dialog).queryByRole("button", { name: "重试原提交" }),
  ).not.toBeInTheDocument();
});

test("negative opening requires an explicit refund balance confirmation", async () => {
  mocks();
  show();
  await screen.findByText("FR-A");
  fireEvent.click(screen.getByRole("button", { name: "录入期初往来" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(
    within(dialog).getByRole("combobox", { name: "资金往来对象" }),
    { target: { value: PARTY } },
  );
  fireEvent.change(within(dialog).getByLabelText("资金往来金额"), {
    target: { value: "-12.1234" },
  });
  fireEvent.change(within(dialog).getByLabelText("资金操作说明"), {
    target: { value: "已核对旧退款" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "保存资金记录" }));
  await within(dialog).findByText(/负数金额表示待退款/);
  expect(api.POST).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("checkbox", {
      name: "我已核实负数金额对应真实待退款余额",
    }),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "保存资金记录" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalled());
  expect(postMock.mock.calls[0][0]).toBe("/api/v1/funds/openings");
  expect((postMock.mock.calls[0][1] as Options).body).toMatchObject({
    side: "AR",
    party_id: PARTY,
    amount: "-12.1234",
    refund_balance_confirmed: true,
  });
});

test("a fully settled legacy document can bind explicit zero without an opening", async () => {
  mocks();
  show();
  await screen.findByText("FR-A");
  fireEvent.click(screen.getByRole("tab", { name: "历史单据绑定" }));
  fireEvent.click(await screen.findByRole("button", { name: "绑定期初" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("资金操作说明"), {
    target: { value: "旧单已全部结清" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认绑定" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalled());
  const [path, options] = postMock.mock.calls[0];
  expect(path).toBe("/api/v1/funds/legacy-bindings");
  expect((options as Options).body).toMatchObject({
    source_document_id: "old-doc",
    opening_source_id: null,
    amount: "0.0000",
  });
});

test("activation permission is independent and does not load financial lists", async () => {
  mocks({ enabled: false });
  show(["funds.activate"]);
  fireEvent.click(await screen.findByRole("button", { name: "启用资金管理" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByLabelText("资金启用日期")).toHaveValue(
    "2026-09-06",
  );
  fireEvent.change(within(dialog).getByLabelText("资金操作说明"), {
    target: { value: "切换已核对" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认启用" }));
  await waitFor(() => expect(api.POST).toHaveBeenCalled());
  expect(
    getMock.mock.calls.every(([path]) => path === "/api/v1/funds/settings"),
  ).toBe(true);
  expect(postMock.mock.calls[0][0]).toBe("/api/v1/funds/activate");
});

test("negative legacy binding preserves the signed server balance and requires refund confirmation", async () => {
  mocks();
  const prior = getMock.getMockImplementation()!;
  getMock.mockImplementation((path, options) => {
    if (
      path === "/api/v1/funds/sources" &&
      options?.params?.query?.party_id === PARTY
    )
      return Promise.resolve(
        ok(
          page([
            {
              ...source("negative-opening"),
              number: "NEG-OPENING",
              kind: "OPENING",
              status: "REFUND",
              balance: "-20.0000",
              settlement_amount: "0.0000",
              refund_amount: "20.0000",
            },
          ]),
        ),
      );
    return prior(path, options);
  });
  show();
  await screen.findByText("FR-A");
  fireEvent.click(screen.getByRole("tab", { name: "历史单据绑定" }));
  fireEvent.click(await screen.findByRole("button", { name: "绑定期初" }));
  const dialog = screen.getByRole("dialog");
  await within(dialog).findByRole("option", {
    name: "NEG-OPENING · 余额 -20.0000",
  });
  fireEvent.change(
    within(dialog).getByRole("combobox", { name: "绑定期初来源" }),
    { target: { value: "negative-opening" } },
  );
  expect(within(dialog).getByLabelText("历史未结金额")).toHaveValue("-20.0000");
  fireEvent.change(within(dialog).getByLabelText("资金操作说明"), {
    target: { value: "启用前旧单已经收款，退货款尚未退回" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认绑定" }));
  await within(dialog).findByText(/负数历史余额表示已结算但尚待退款/);
  expect(postMock).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("checkbox", {
      name: "我已核实负数历史余额为已结算但尚待退款",
    }),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "确认绑定" }));
  await waitFor(() => expect(postMock).toHaveBeenCalled());
  expect(postMock.mock.calls[0][1]?.body).toMatchObject({
    amount: "-20.0000",
    opening_source_id: "negative-opening",
    refund_balance_confirmed: true,
  });
});

test("financial source errors are shown without rendering stale details", async () => {
  mocks();
  const prior = getMock.getMockImplementation()!;
  getMock.mockImplementation(((path: string, options: Options) =>
    path === "/api/v1/funds/sources/{id}"
      ? Promise.resolve(failure(403, "没有读取此来源的权限"))
      : prior(path as never, options as never)) as never);
  show(read);
  await screen.findByText("FR-A");
  fireEvent.click(screen.getAllByRole("button", { name: "查看来源" })[0]);
  expect(await screen.findByText("没有读取此来源的权限")).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "资金来源详情" }),
  ).not.toBeInTheDocument();
});

test("late source A response cannot replace the selected source B", async () => {
  mocks();
  let finish!: (value: never) => void;
  const prior = getMock.getMockImplementation()!;
  getMock.mockImplementation(((path: string, options: Options) =>
    path === "/api/v1/funds/sources/{id}" && options.params?.path?.id === "A"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : prior(path as never, options as never)) as never);
  show(read);
  await screen.findByText("FR-A");
  fireEvent.click(screen.getAllByRole("button", { name: "查看来源" })[0]);
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  fireEvent.click(screen.getAllByRole("button", { name: "查看来源" })[1]);
  expect(
    await screen.findByRole("region", { name: "资金来源详情" }),
  ).toHaveTextContent("来源说明B");
  finish(ok(source("A")));
  await waitFor(() =>
    expect(
      screen.getByRole("region", { name: "资金来源详情" }),
    ).toHaveTextContent("来源说明B"),
  );
  expect(
    screen.getByRole("region", { name: "资金来源详情" }),
  ).not.toHaveTextContent("来源说明A");
});

test("order summary hides balances without the matching funds read permission", () => {
  const value = {
    integration_status: "ACTIVE" as const,
    unmapped_document_count: 0,
    settlement_status: "PARTIAL" as const,
    source_amount: "900.0000",
    historically_settled_amount: "0.0000",
    settled_amount: "600.0000",
    refunded_amount: "0.0000",
    balance: "300.0000",
    settlement_amount: "300.0000",
    refund_amount: "0.0000",
  };
  const view = render(
    <OrderFundsSummary
      value={value}
      side="AP"
      partyId={PARTY}
      permissions={["purchase.read", "product.cost.read", "funds.ar.read"]}
      locked={false}
    />,
  );
  expect(
    screen.queryByRole("region", { name: "订单资金结算概览" }),
  ).not.toBeInTheDocument();
  view.rerender(
    <OrderFundsSummary
      value={value}
      side="AP"
      partyId={PARTY}
      permissions={["funds.ap.read", "product.cost.read"]}
      locked={false}
    />,
  );
  const region = screen.getByRole("region", { name: "订单资金结算概览" });
  expect(region).toHaveTextContent("部分结算");
  expect(
    within(region).getByRole("link", { name: "查看资金往来" }),
  ).toHaveAttribute("href", `/funds?side=AP&party=${PARTY}`);
});

test.each(["AR", "AP"] as const)(
  "%s order summary separates historical settlements from actual cash and hides both after permission loss",
  (side) => {
    const value = {
      integration_status: "ACTIVE" as const,
      unmapped_document_count: 0,
      settlement_status: "PARTIAL" as const,
      source_amount: "40.0000",
      historically_settled_amount: "60.1234",
      settled_amount: "0.0000",
      refunded_amount: "0.0000",
      balance: "40.0000",
      settlement_amount: "40.0000",
      refund_amount: "0.0000",
    };
    const readPermission = side === "AR" ? "funds.ar.read" : "funds.ap.read";
    const commercialPermission =
      side === "AR" ? "product.price.read" : "product.cost.read";
    const view = render(
      <OrderFundsSummary
        value={value}
        side={side}
        partyId={PARTY}
        permissions={[readPermission, commercialPermission]}
        locked={false}
      />,
    );
    const region = screen.getByRole("region", { name: "订单资金结算概览" });
    expect(region).toHaveTextContent("部分结算");
    expect(
      within(region).getByText("期初已结金额").parentElement,
    ).toHaveTextContent("60.1234");
    expect(
      within(region).getByText("本系统已结算").parentElement,
    ).toHaveTextContent("0.0000");
    expect(region).toHaveTextContent("不计为本系统实际收付款");
    view.rerender(
      <OrderFundsSummary
        value={value}
        side={side}
        partyId={PARTY}
        permissions={[readPermission]}
        locked={false}
      />,
    );
    expect(screen.queryByText("60.1234")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "订单资金结算概览" }),
    ).not.toBeInTheDocument();
  },
);

test("source detail labels historical settlement independently from actual cash", async () => {
  mocks();
  const prior = getMock.getMockImplementation()!;
  getMock.mockImplementation((path, options) =>
    path === "/api/v1/funds/sources/{id}"
      ? Promise.resolve(
          ok({
            ...source("A"),
            kind: "LEGACY",
            amount: "40.0000",
            commercial_amount: "100.1234",
            historically_settled_amount: "60.1234",
            settled_amount: "0.0000",
            balance: "40.0000",
            settlement_amount: "40.0000",
          }),
        )
      : prior(path, options),
  );
  const view = show(read);
  await screen.findByText("FR-A");
  fireEvent.click(screen.getAllByRole("button", { name: "查看来源" })[0]);
  const region = await screen.findByRole("region", { name: "资金来源详情" });
  expect(
    within(region).getByText("期初已结金额").parentElement,
  ).toHaveTextContent("60.1234");
  expect(
    within(region).getByText("本系统已结算").parentElement,
  ).toHaveTextContent("0.0000");
  expect(region).toHaveTextContent("不计为本系统实际收付款");
  view.setPermissions([]);
  expect(screen.queryByText("60.1234")).not.toBeInTheDocument();
});

test("incomplete legacy order summary never presents a zero included balance as fully paid", () => {
  const value = {
    integration_status: "INCOMPLETE" as const,
    unmapped_document_count: 2,
    settlement_status: null,
    source_amount: "0.0000",
    historically_settled_amount: "0.0000",
    settled_amount: "0.0000",
    refunded_amount: "0.0000",
    balance: "0.0000",
    settlement_amount: "0.0000",
    refund_amount: "0.0000",
  };
  render(
    <OrderFundsSummary
      value={value}
      side="AR"
      partyId={PARTY}
      permissions={["funds.ar.read", "product.price.read", "funds.opening"]}
      locked={true}
    />,
  );
  const region = screen.getByRole("region", { name: "订单资金结算概览" });
  expect(region).toHaveTextContent("仍有 2 张历史单据需要绑定期初");
  expect(region).not.toHaveTextContent("已结清");
  expect(
    within(region).getByRole("link", { name: "查看资金往来" }),
  ).toHaveAttribute("aria-disabled", "true");
});
