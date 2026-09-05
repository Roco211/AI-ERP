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
import { ImportsWorkspace } from "@/features/imports/workspace";
import { api } from "@/lib/api";
import type { Batch, ImportRow } from "@/features/imports/client";
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
type Options = {
  signal?: AbortSignal;
  body?: unknown;
  bodySerializer?: () => FormData;
  parseAs?: string;
  params?: {
    path?: { id?: string; record_id?: string };
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
const all = [
  "catalog.import.read",
  "catalog.import.write",
  "catalog.read",
  "catalog.write",
];
const ok = (data: unknown): Result => ({ data, response: new Response() });
const page = (items: unknown[]) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 25,
});
function batch(values: Partial<Batch> = {}): Batch {
  return {
    id: "batch-A",
    resource: "brands",
    mode: "CREATE_ONLY",
    filename: "品牌数据.xlsx",
    worksheet: "品牌",
    preview_hash: "a".repeat(64),
    version: 1,
    status: "PREVIEW_READY",
    created_by: "actor",
    created_at: "2026-09-06T00:00:00Z",
    expires_at: "2026-09-13T00:00:00Z",
    confirmed_at: null,
    total: 2,
    ready: 2,
    invalid: 0,
    pending: 0,
    succeeded: 0,
    failed: 0,
    body_available: true,
    is_creator: true,
    can_confirm: true,
    can_retry: false,
    ...values,
  };
}
function row(values: Partial<ImportRow> = {}): ImportRow {
  return {
    id: "row-A",
    row_no: 2,
    action: "CREATE",
    status: "READY",
    raw_values: { 编码: "0001", 名称: "  原名  " },
    cleaned_values: { code: "0001", name: "原名" },
    errors: [],
    error_code: null,
    retryable: false,
    expected_target_id: null,
    expected_version: null,
    target_id: null,
    target_version: null,
    attempts: 0,
    completed_at: null,
    ...values,
  };
}
function mock(current = batch(), rows = [row()]) {
  get.mockImplementation(async (path) =>
    path.endsWith("/rows")
      ? ok(page(rows))
      : path.endsWith("/{id}")
        ? ok(current)
        : path.includes("/brands/")
          ? ok({
              id: "saved-A",
              name: "已保存品牌",
              code: "0001",
              active: true,
              version: 1,
            })
          : ok(page([current])),
  );
  return {
    setBatch(value: Batch) {
      current = value;
    },
    setRows(value: ImportRow[]) {
      rows = value;
    },
  };
}
function show(permissions = all) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const element = (value: string[]) => (
    <QueryClientProvider client={client}>
      <ImportsWorkspace permissions={value} />
    </QueryClientProvider>
  );
  const view = render(element(permissions));
  return {
    ...view,
    client,
    permissions: (value: string[]) => view.rerender(element(value)),
  };
}
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/imports");
});
async function openBatch() {
  fireEvent.click(await screen.findByRole("button", { name: "查看批次" }));
  return screen.findByRole("region", { name: "导入批次详情" });
}

test("import combines resource and operation permissions and hides privileged batch data on revoke", async () => {
  mock();
  const view = show(["catalog.import.read"]);
  expect(get).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent("没有查看");
  view.permissions(all);
  await openBatch();
  fireEvent.click(screen.getByRole("button", { name: "确认执行导入" }));
  await screen.findByRole("dialog", { name: "确认导入批次" });
  view.permissions(["catalog.import.read"]);
  expect(screen.queryByText("品牌数据.xlsx · 品牌")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

test("upload captures multipart file/mode/worksheet and retains original key and bytes after lost response", async () => {
  mock();
  post.mockRejectedValueOnce(new TypeError("连接中断"));
  post.mockResolvedValueOnce(
    ok({ id: "batch-A", status: "PREVIEW_READY", version: 1, request_id: "r" }),
  );
  show();
  fireEvent.click(await screen.findByRole("button", { name: "上传资料" }));
  const dialog = await screen.findByRole("dialog", { name: "上传并预览资料" });
  fireEvent.change(within(dialog).getByLabelText("上传资料类型"), {
    target: { value: "brands" },
  });
  fireEvent.change(within(dialog).getByLabelText("资料工作表名称"), {
    target: { value: "品牌" },
  });
  const file = new File(["xlsx original bytes"], "品牌.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  fireEvent.change(within(dialog).getByLabelText("资料Excel文件"), {
    target: { files: [file] },
  });
  const submit = within(dialog).getByRole("button", { name: "生成导入预览" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  await screen.findByText(/提交结果待确认/);
  expect(post).toHaveBeenCalledTimes(1);
  expect(within(dialog).getByLabelText("资料Excel文件")).toBeDisabled();
  expect(within(dialog).getByLabelText("资料工作表名称")).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  const first = post.mock.calls[0][1];
  expect(first.bodySerializer!().get("file")).toBe(file);
  expect(first.bodySerializer!().get("worksheet")).toBe("品牌");
  fireEvent.click(within(dialog).getByRole("button", { name: "重试原提交" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].params?.header).toEqual(first.params?.header);
  expect(post.mock.calls[1][1].body).toEqual(first.body);
  await screen.findByRole("region", { name: "导入批次详情" });
  expect(window.location.search).toContain("batch=batch-A");
});

test("invalid preview and another creator cannot confirm; original/cleaned/error rows remain readable", async () => {
  mock(
    batch({
      status: "PREVIEW_INVALID",
      invalid: 1,
      ready: 0,
      can_confirm: false,
      is_creator: false,
    }),
    [
      row({
        status: "INVALID",
        errors: [
          {
            column: "编码",
            code: "UNSAFE_IDENTIFIER_CELL",
            message: "编码必须填写为文本",
          },
        ],
      }),
    ],
  );
  show();
  const region = await openBatch();
  expect(
    within(region).queryByRole("button", { name: "确认执行导入" }),
  ).not.toBeInTheDocument();
  expect(
    within(region).getByText("只有批次创建者可确认和发起重试。"),
  ).toBeVisible();
  expect(
    await within(region).findByText("编码：编码必须填写为文本"),
  ).toBeVisible();
  expect(within(region).getAllByText("0001")).toHaveLength(2);
  expect(
    within(region).getByRole("button", { name: "下载失败行" }),
  ).toBeEnabled();
});

test("only retryable FAILED rows can be selected; known success links to exact saved record", async () => {
  mock(
    batch({
      status: "PARTIAL_FAILED",
      version: 3,
      succeeded: 1,
      failed: 2,
      can_confirm: false,
      can_retry: true,
    }),
    [
      row({ status: "SUCCEEDED", target_id: "saved-A" }),
      row({ id: "failed", row_no: 3, status: "FAILED", retryable: true }),
      row({ id: "conflict", row_no: 4, status: "FAILED", retryable: false }),
    ],
  );
  post.mockResolvedValue(
    ok({ id: "batch-A", status: "QUEUED", version: 4, request_id: "r" }),
  );
  show();
  await openBatch();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看已保存资料" }),
  );
  expect(await screen.findByText("已保存品牌")).toBeVisible();
  expect(
    get.mock.calls.some(
      ([path, options]) =>
        path === "/api/v1/brands/{record_id}" &&
        options?.params?.path?.record_id === "saved-A",
    ),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "关闭资料" }));
  expect(screen.queryByLabelText("重试第2行")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("重试第4行")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("重试第3行"));
  fireEvent.click(screen.getByRole("button", { name: "重试所选失败行" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认执行" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1].body).toEqual({
    expected_version: 3,
    row_ids: ["failed"],
  });
});

test("changed persistent batch clears stale failed selection before another retry", async () => {
  const initial = batch({
    version: 3,
    status: "PARTIAL_FAILED",
    can_confirm: false,
    can_retry: true,
    failed: 1,
  });
  mock(initial, [row({ id: "failed", status: "FAILED", retryable: true })]);
  const view = show();
  await openBatch();
  fireEvent.click(await screen.findByLabelText("重试第2行"));
  expect(screen.getByRole("button", { name: "重试所选失败行" })).toBeEnabled();
  view.client.setQueryData(["imports", "batch", "batch-A", all.join("|")], {
    ...initial,
    version: 4,
    succeeded: 1,
    failed: 0,
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "重试所选失败行" }),
    ).toBeDisabled(),
  );
  expect(post).not.toHaveBeenCalled();
});

test("refresh restores persisted batch by URL and expired body cannot be downloaded or executed", async () => {
  window.history.replaceState({}, "", "/imports?batch=batch-A");
  mock(
    batch({ status: "EXPIRED", body_available: false, can_confirm: false }),
    [
      row({
        status: "SUCCEEDED",
        raw_values: null,
        cleaned_values: null,
        target_id: "saved-A",
      }),
    ],
  );
  show();
  const region = await screen.findByRole("region", { name: "导入批次详情" });
  expect(
    within(region).getByText("预览正文已到期，成功资料和来源仍然保留。"),
  ).toBeVisible();
  expect(
    within(region).queryByRole("button", { name: "下载完整结果" }),
  ).not.toBeInTheDocument();
  expect(
    within(region).queryByRole("button", { name: "确认执行导入" }),
  ).not.toBeInTheDocument();
  expect(
    await within(region).findByRole("button", { name: "查看已保存资料" }),
  ).toBeVisible();
});

test("price imports require independent price permission and hide stored values immediately when it is revoked", async () => {
  mock(batch({ resource: "product-prices", filename: "价格.xlsx" }), [
    row({
      raw_values: { 价格: "999.9876" },
      cleaned_values: { price: "999.9876" },
    }),
  ]);
  const view = show([...all, "product.price.read", "product.price.write"]);
  await openBatch();
  expect(await screen.findAllByText("999.9876")).toHaveLength(2);
  view.permissions(all);
  expect(screen.queryByText("999.9876")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "导入批次详情" }),
  ).not.toBeInTheDocument();
});

test("a rejected upload stays editable and reports the server worksheet choice without creating a batch", async () => {
  mock();
  post.mockResolvedValue({
    error: { detail: "请选择工作表：品牌甲、品牌乙" },
    response: new Response(null, { status: 422 }),
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "上传资料" }));
  const dialog = await screen.findByRole("dialog", { name: "上传并预览资料" });
  fireEvent.change(within(dialog).getByLabelText("资料Excel文件"), {
    target: { files: [new File(["xlsx"], "多表.xlsx")] },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "生成导入预览" }));
  expect(await screen.findByText("请选择工作表：品牌甲、品牌乙")).toBeVisible();
  expect(within(dialog).getByLabelText("资料工作表名称")).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "重试原提交" }),
  ).not.toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
});
