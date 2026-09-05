"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { useSearchParams } from "next/navigation";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { z } from "zod";
import { Dialog } from "@base-ui/react/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { ProductPicker } from "@/features/catalog/picker";
import {
  unwrap,
  changeDocument,
  saveDraft,
  labels,
  documentLabels,
  permissionsByKind,
  type Document,
  type Draft,
  type Kind,
} from "./client";

const statuses = { DRAFT: "草稿", POSTED: "已过账", REVERSED: "已冲销" };
const movementNames: Record<string, string> = {
  RECEIVE: "入库",
  ISSUE: "出库",
  TRANSFER_IN: "调拨入",
  TRANSFER_OUT: "调拨出",
  RESERVE: "占用",
  RELEASE: "释放",
  REVERSE: "冲销",
};
const tabs = [
  { id: "balances", label: "库存总览" },
  { id: "movements", label: "库存流水" },
  { id: "low", label: "低库存" },
  ...Object.entries(labels).map(([id, label]) => ({ id, label })),
];
type EditableLine = Draft["lines"][number] & {
  label: string;
  unitLabel: string;
};
const selectClass =
  "h-9 max-w-full rounded-lg border border-border bg-white px-2 text-sm";
const number = (value: string | number | undefined | null) =>
  value == null ? "—" : String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");

type GridRow = { id: string; cells: ReactNode[] };
function Grid({
  headers,
  rows,
  page = 1,
}: {
  headers: string[];
  rows: GridRow[];
  page?: number;
}) {
  const columns = headers.map<ColumnDef<GridRow>>((label, index) => ({
    id: String(index),
    header: label,
    cell: ({ row }) => row.original.cells[index],
  }));
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    manualPagination: true,
    state: { pagination: { pageIndex: page - 1, pageSize: 25 } },
  });
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-[#f5f7f2] text-muted-foreground">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th
                  key={header.id}
                  className="whitespace-nowrap px-4 py-3 font-medium"
                >
                  {flexRender(
                    header.column.columnDef.header,
                    header.getContext(),
                  )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-t border-border">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-4 py-3 tabular-nums">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <p className="p-8 text-center text-sm text-muted-foreground">
          暂无记录。可先录入期初库存。
        </p>
      )}
    </div>
  );
}
const draftFormSchema = z.object({
  from: z.string().uuid(),
  reason: z.string().trim().min(1),
  lines: z.array(z.unknown()).min(1).max(200),
});
type EditorFields = {
  reason: string;
  from: string;
  to: string;
  lines: EditableLine[];
  reverseReason: string;
};

export function InventoryWorkspace({ permissions }: { permissions: string[] }) {
  const qc = useQueryClient();
  const cost = permissions.includes("product.cost.read");
  const searchParams = useSearchParams();
  const [tab, setTab] = useState(
      tabs.some((t) => t.id === searchParams.get("tab"))
        ? searchParams.get("tab")!
        : "balances",
    ),
    [page, setPage] = useState(
      Math.max(
        1,
        Math.min(
          1000000,
          Number.parseInt(searchParams.get("page") ?? "1", 10) || 1,
        ),
      ),
    ),
    [q, setQ] = useState(searchParams.get("q") ?? ""),
    [warehouse, setWarehouse] = useState(searchParams.get("warehouse") ?? "");
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries({
      tab: tab === "balances" ? "" : tab,
      page: page === 1 ? "" : String(page),
      q,
      warehouse,
    })) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const next =
      window.location.pathname + (params.size ? "?" + params.toString() : "");
    if (next !== window.location.pathname + window.location.search)
      window.history.replaceState(window.history.state, "", next);
  }, [tab, page, q, warehouse]);
  const [warehouseSearch, setWarehouseSearch] = useState("");
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [detail, setDetail] = useState<Document | null>(null),
    [editor, setEditor] = useState(false),
    [editing, setEditing] = useState<Document>();
  const [kind, setKind] = useState<Kind>("OPENING"),
    [showPicker, setShowPicker] = useState(false);
  const [confirmation, setConfirmation] = useState<
    "post" | "reverse" | "refresh" | null
  >(null);
  const form = useForm<EditorFields>({
    defaultValues: {
      reason: "",
      from: "",
      to: "",
      lines: [],
      reverseReason: "",
    },
  });
  const reason = useWatch({ control: form.control, name: "reason" });
  const from = useWatch({ control: form.control, name: "from" });
  const to = useWatch({ control: form.control, name: "to" });
  const lines = useWatch({ control: form.control, name: "lines" });
  const reverseReason = useWatch({
    control: form.control,
    name: "reverseReason",
  });
  const setReason = (value: string) =>
    form.setValue("reason", value, { shouldDirty: true });
  const setFrom = (value: string) =>
    form.setValue("from", value, { shouldDirty: true });
  const setTo = (value: string) =>
    form.setValue("to", value, { shouldDirty: true });
  const setReverseReason = (value: string) =>
    form.setValue("reverseReason", value, { shouldDirty: true });
  const setLines = (
    value: EditableLine[] | ((previous: EditableLine[]) => EditableLine[]),
  ) =>
    form.setValue(
      "lines",
      typeof value === "function" ? value(form.getValues("lines")) : value,
      { shouldDirty: true },
    );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const inFlight = useRef(false),
    pending = useRef<{ signature: string; key: string } | null>(null);
  const allowed = permissions.includes("inventory.read");
  const wh = useQuery({
    queryKey: ["catalog", "warehouses", "inventory", warehouseSearch],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/warehouses", {
          params: { query: { q: warehouseSearch, page_size: 100 } },
        }),
      ),
    enabled: allowed && permissions.includes("warehouse.read"),
  });
  const stocks = useQuery({
    queryKey: ["inventory", "balances", warehouse, q, page],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/inventory/balances", {
          params: {
            query: {
              warehouse_id: warehouse || undefined,
              q,
              page,
              page_size: 25,
            },
          },
        }),
      ),
    enabled: allowed && tab === "balances",
  });
  const documents = useQuery({
    queryKey: ["inventory", "documents", tab, page],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/inventory/documents", {
          params: { query: { kind: tab as Kind, page, page_size: 25 } },
        }),
      ),
    enabled: allowed && tab in labels,
  });
  const movement = useQuery({
    queryKey: ["inventory", "movements", warehouse, cursors.at(-1)],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/inventory/movements", {
          params: {
            query: {
              warehouse_id: warehouse || undefined,
              cursor: cursors.at(-1),
              page_size: 25,
            },
          },
        }),
      ),
    enabled: allowed && tab === "movements",
  });
  const low = useQuery({
    queryKey: ["inventory", "low", page],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/inventory/low-stock", {
          params: { query: { page, page_size: 25 } },
        }),
      ),
    enabled: allowed && tab === "low",
  });
  const active =
    tab === "balances"
      ? stocks
      : tab === "movements"
        ? movement
        : tab === "low"
          ? low
          : documents;
  const detailRequest = useRef(0);
  async function openDetail(id: string) {
    const request = ++detailRequest.current;
    setDetail(null);
    try {
      const loaded = unwrap(
        await api.GET("/api/v1/inventory/documents/{id}", {
          params: { path: { id } },
        }),
      );
      if (request !== detailRequest.current) return false;
      setDetail(loaded);
      setError("");
      return true;
    } catch (e) {
      if (request === detailRequest.current) setError(String(e));
      return false;
    }
  }
  async function write(
    signature: string,
    operation: (key: string) => Promise<{ id: string }>,
    done: () => void,
  ) {
    if (inFlight.current) return;
    if (pending.current && pending.current.signature !== signature) {
      setError("上次提交结果尚未确认，请先使用原内容重试。");
      return;
    }
    pending.current ??= { signature, key: crypto.randomUUID() };
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const receipt = await operation(pending.current.key);
      pending.current = null;
      setUncertain(false);
      await qc.invalidateQueries({ queryKey: ["inventory"] });
      const refreshed = await openDetail(receipt.id);
      done();
      setNotice(
        refreshed
          ? "操作已完成，库存数据已刷新。"
          : "操作已提交，详情加载失败，请刷新。",
      );
    } catch (e) {
      // Known HTTP failures roll back; network ambiguity retains the original key and content.
      const message = e instanceof Error ? e.message : String(e);
      if (
        e instanceof Error &&
        "status" in e &&
        typeof e.status === "number" &&
        e.status >= 400 &&
        e.status < 500
      )
        pending.current = null;
      setUncertain(!!pending.current);
      setError(
        message +
          (pending.current
            ? " 提交结果待确认，请保留当前内容并再次提交。"
            : ""),
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function newDocument(next: Kind, doc?: Document) {
    if (pending.current) {
      setError("请先重试上次结果待确认的提交。");
      return;
    }
    setKind(next);
    setEditing(doc);
    setReason(doc?.reason ?? "");
    setFrom(doc?.warehouse_id ?? warehouse);
    setTo(doc?.target_warehouse_id ?? "");
    setLines(
      doc?.lines?.map((line) => ({
        product_id: line.product_id,
        unit_id: line.unit_id,
        qty: String(line.qty),
        direction: line.direction,
        input_unit_cost:
          line.input_unit_cost == null ? null : String(line.input_unit_cost),
        label: line.product_label,
        unitLabel: line.unit_label,
      })) ?? [],
    );
    setShowPicker(false);
    setEditor(true);
    setError("");
  }
  async function save() {
    if (!draftFormSchema.safeParse(form.getValues()).success) {
      setError("请选择仓库、填写原因并添加商品。");
      return;
    }
    if (kind === "TRANSFER" && (!to || to === from)) {
      setError("请选择不同的目标仓库。");
      return;
    }
    const body: Draft = {
      warehouse_id: from,
      target_warehouse_id: kind === "TRANSFER" ? to : null,
      reason,
      lines: lines.map(
        ({ product_id, unit_id, qty, direction, input_unit_cost }) => ({
          product_id,
          unit_id,
          qty,
          direction,
          input_unit_cost: input_unit_cost || null,
        }),
      ),
    };
    await write(
      JSON.stringify({ action: "save", id: editing?.id, kind, body }),
      (key) => saveDraft(kind, body, key, editing),
      () => setEditor(false),
    );
  }
  async function confirm() {
    if (!detail || !confirmation) return;
    if (confirmation === "reverse" && !reverseReason.trim()) {
      setError("请填写冲销原因。");
      return;
    }
    await write(
      JSON.stringify({
        action: confirmation,
        id: detail.id,
        version: detail.version,
        reason: reverseReason,
      }),
      (key) => changeDocument(detail, confirmation, key, reverseReason),
      () => setConfirmation(null),
    );
  }
  if (!allowed)
    return (
      <p role="alert" className="mt-6">
        当前账户没有查看库存的权限。
      </p>
    );
  const warehouses = wh.data?.items ?? [];
  const canWrite = (k: Document["type"]) =>
    k in permissionsByKind &&
    cost &&
    permissions.includes(permissionsByKind[k as Kind]);
  const warehouseOptions = (
    <>
      <option value="">请选择仓库</option>
      {warehouses
        .filter((w) => w.active || w.id === from || w.id === to)
        .map((w) => (
          <option key={w.id} value={w.id}>
            {w.name} · {w.code}
          </option>
        ))}
    </>
  );
  return (
    <div className="mt-7 space-y-5">
      <div className="flex flex-wrap gap-2 border-b border-border pb-4">
        {tabs.map((t) => (
          <Button
            key={t.id}
            variant={tab === t.id ? "default" : "outline"}
            onClick={() => {
              setTab(t.id);
              setPage(1);
              setCursors([undefined]);
            }}
          >
            {t.label}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {(tab === "balances" || tab === "movements") && (
          <>
            <Input
              aria-label="搜索仓库"
              className="max-w-40"
              placeholder="搜索仓库"
              value={warehouseSearch}
              onChange={(e) => setWarehouseSearch(e.target.value)}
            />
            <select
              aria-label="筛选仓库"
              className={selectClass}
              value={warehouse}
              onChange={(e) => {
                setWarehouse(e.target.value);
                setPage(1);
                setCursors([undefined]);
              }}
            >
              <option value="">全部仓库</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </>
        )}
        {tab === "balances" && (
          <Input
            aria-label="搜索库存商品"
            className="max-w-72"
            placeholder="商品编码或名称"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        )}
        {tab in labels && canWrite(tab as Kind) && (
          <Button onClick={() => newDocument(tab as Kind)}>
            新建{labels[tab as Kind]}
          </Button>
        )}
        <Button
          variant="outline"
          onClick={() => {
            setCursors([undefined]);
            void qc.invalidateQueries({ queryKey: ["inventory"] });
          }}
        >
          刷新数据
        </Button>
      </div>
      {notice && (
        <p
          role="status"
          className="rounded-lg bg-green-50 p-3 text-sm text-green-800"
        >
          {notice}
        </p>
      )}
      {(error || active.error || wh.error) && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-destructive"
        >
          {error || String(active.error ?? wh.error)}
        </p>
      )}
      {active.isFetching && (
        <p role="status" className="text-sm text-muted-foreground">
          正在读取库存…
        </p>
      )}
      {tab === "balances" && (
        <Grid
          page={page}
          headers={[
            "商品",
            "仓库",
            "现有量",
            "占用",
            "可用量",
            ...(cost ? ["平均成本 / 基本单位", "库存金额"] : []),
          ]}
          rows={(stocks.data?.items ?? []).map((r) => ({
            id: r.warehouse_id + r.product_id,
            cells: [
              <div key="product">
                <p className="font-medium">{r.product_name}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {r.sku} · {r.unit_name}
                </p>
              </div>,
              r.warehouse_name,
              number(r.on_hand_qty),
              number(r.reserved_qty),
              <strong key="available" className="text-primary">
                {number(r.available_qty)}
              </strong>,
              ...(cost
                ? [number(r.avg_unit_cost), number(r.inventory_value)]
                : []),
            ],
          }))}
        />
      )}
      {tab === "movements" && (
        <>
          <p className="text-xs text-muted-foreground">
            本轮翻页保持同一查询范围；查看最新变动请刷新。
          </p>
          <Grid
            page={page}
            headers={[
              "时间",
              "商品 / 仓库",
              "变动",
              "数量",
              "占用变化",
              ...(cost ? ["金额变化"] : []),
              "来源",
            ]}
            rows={(movement.data?.items ?? []).map((r) => ({
              id: r.id,
              cells: [
                new Date(r.created_at).toLocaleString(),
                <div key="product">
                  {r.product_label}
                  <p className="text-xs text-muted-foreground">
                    {r.warehouse_name}
                  </p>
                </div>,
                movementNames[r.kind] ?? r.kind,
                number(r.base_qty),
                number(r.reserved_qty_delta),
                ...(cost ? [number(r.value_delta)] : []),
                <Button
                  key="source"
                  disabled={busy}
                  variant="link"
                  onClick={() => void openDetail(r.document_id)}
                >
                  {r.document_number}
                </Button>,
              ],
            }))}
          />
        </>
      )}
      {tab === "low" && (
        <>
          <p className="text-sm text-muted-foreground">
            按企业全部仓库的可用量合计，与商品最低库存比较。这里只提示缺货候选。
          </p>
          <Grid
            page={page}
            headers={["商品", "编码", "可用量", "最低库存", "单位"]}
            rows={(low.data?.items ?? []).map((r) => ({
              id: r.product_id,
              cells: [
                r.product_name,
                r.sku,
                number(r.available_qty),
                number(r.min_stock_qty),
                r.unit_name,
              ],
            }))}
          />
        </>
      )}
      {tab in labels && (
        <Grid
          page={page}
          headers={["单据号", "仓库", "状态", "原因", "创建时间", "操作"]}
          rows={(documents.data?.items ?? []).map((r) => ({
            id: r.id,
            cells: [
              r.number,
              r.warehouse_name +
                (r.target_warehouse_name
                  ? " → " + r.target_warehouse_name
                  : ""),
              <span
                key="status"
                className="rounded bg-[#edf1e8] px-2 py-1 text-xs"
              >
                {statuses[r.status]}
              </span>,
              r.reason,
              new Date(r.created_at).toLocaleString(),
              <Button
                key="view"
                disabled={busy}
                variant="outline"
                onClick={() => void openDetail(r.id)}
              >
                查看
              </Button>,
            ],
          }))}
        />
      )}
      <div className="flex items-center justify-end gap-3 text-sm">
        <Button
          variant="outline"
          disabled={tab === "movements" ? cursors.length === 1 : page === 1}
          onClick={() =>
            tab === "movements"
              ? setCursors((x) => x.slice(0, -1))
              : setPage((x) => x - 1)
          }
        >
          上一页
        </Button>
        <span>第 {tab === "movements" ? cursors.length : page} 页</span>
        <Button
          variant="outline"
          disabled={
            tab === "movements"
              ? !movement.data?.next_cursor
              : page * 25 >=
                (tab === "balances"
                  ? (stocks.data?.total ?? 0)
                  : tab === "low"
                    ? (low.data?.total ?? 0)
                    : (documents.data?.total ?? 0))
          }
          onClick={() =>
            tab === "movements"
              ? setCursors((x) => [
                  ...x,
                  movement.data?.next_cursor ?? undefined,
                ])
              : setPage((x) => x + 1)
          }
        >
          下一页
        </Button>
      </div>
      {detail && (
        <section
          aria-label="库存单据详情"
          className="space-y-4 rounded-xl border border-border bg-white p-5"
        >
          <div className="flex flex-wrap justify-between gap-3">
            <div>
              <h2 className="font-semibold">
                {documentLabels[detail.type]} · {detail.number}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {statuses[detail.status]} · {detail.warehouse_name}
                {detail.target_warehouse_name
                  ? " → " + detail.target_warehouse_name
                  : ""}
              </p>
            </div>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                detailRequest.current++;
                setDetail(null);
              }}
            >
              关闭详情
            </Button>
          </div>
          <p className="text-sm">原因：{detail.reason}</p>
          {detail.type.startsWith("PURCHASE_") && (
            <a
              className="text-sm text-primary"
              href={"/purchase?document=" + detail.id}
            >
              前往采购单据
            </a>
          )}
          <Grid
            page={page}
            headers={[
              "商品",
              "输入数量",
              "单位",
              "基础数量",
              ...(detail.type === "STOCKTAKE" ? ["盘点基准"] : []),
              ...(cost ? ["基本单位成本"] : []),
            ]}
            rows={(detail.lines ?? []).map((r) => ({
              id: r.id,
              cells: [
                r.product_label,
                number(r.qty),
                r.unit_label,
                number(r.base_qty),
                ...(detail.type === "STOCKTAKE"
                  ? [number(r.baseline_qty)]
                  : []),
                ...(cost ? [number(r.input_unit_cost)] : []),
              ],
            }))}
          />
          <div className="flex flex-wrap gap-2">
            {detail.status === "DRAFT" && canWrite(detail.type) && (
              <>
                <Button
                  variant="outline"
                  onClick={() => newDocument(detail.type as Kind, detail)}
                >
                  编辑草稿
                </Button>
                <Button
                  onClick={() => {
                    setReverseReason("");
                    setConfirmation("post");
                  }}
                >
                  过账
                </Button>
                {detail.type === "STOCKTAKE" && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setReverseReason("");
                      setConfirmation("refresh");
                    }}
                  >
                    刷新盘点基准
                  </Button>
                )}
              </>
            )}
            {detail.status === "POSTED" &&
              canWrite(detail.type) &&
              permissions.includes("inventory.reverse") && (
                <Button
                  variant="destructive"
                  onClick={() => {
                    setReverseReason("");
                    setConfirmation("reverse");
                  }}
                >
                  冲销
                </Button>
              )}
          </div>
          {detail.status !== "DRAFT" && (
            <p className="text-xs text-muted-foreground">
              已过账内容不能修改。冲销仅适用于相关商品仓库均没有后续变动的整张单据。
            </p>
          )}
        </section>
      )}
      <Dialog.Root
        open={editor}
        onOpenChange={(open) => {
          if (!busy && !pending.current) setEditor(open);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/30" />
          <Dialog.Popup className="fixed top-[4vh] left-1/2 z-50 max-h-[92vh] w-[min(96vw,1000px)] -translate-x-1/2 overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
            <Dialog.Title className="text-xl font-semibold">
              {editing ? "编辑" : "新建"}
              {labels[kind]}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              先保存草稿，再核对并过账。成本以商品基本单位填写，零成本需明确填
              0。
            </Dialog.Description>
            {error && (
              <p role="alert" className="my-3 text-sm text-destructive">
                {error}
              </p>
            )}
            <fieldset disabled={busy || uncertain} className="mt-5 space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="grid gap-1 text-sm">
                  搜索仓库
                  <Input
                    value={warehouseSearch}
                    onChange={(e) => setWarehouseSearch(e.target.value)}
                    placeholder="按仓库名称或编码查找"
                  />
                </label>
                <label className="grid gap-1 text-sm">
                  单据仓库
                  <select
                    className={selectClass}
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                  >
                    {warehouseOptions}
                  </select>
                </label>
                {kind === "TRANSFER" && (
                  <label className="grid gap-1 text-sm">
                    目标仓库
                    <select
                      className={selectClass}
                      value={to}
                      onChange={(e) => setTo(e.target.value)}
                    >
                      {warehouseOptions}
                    </select>
                  </label>
                )}
              </div>
              <label className="grid gap-1 text-sm">
                单据原因
                <Input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={2000}
                />
              </label>
              <Button
                variant="outline"
                disabled={lines.length >= 200}
                onClick={() => setShowPicker((x) => !x)}
              >
                {showPicker ? "收起选品" : "添加商品"}
              </Button>
              {showPicker && (
                <div className="rounded-xl border border-border p-4">
                  <ProductPicker
                    permissions={permissions}
                    onSelect={({ product, snapshot, unitLabel }) => {
                      if (lines.some((x) => x.product_id === product.id)) {
                        setError("该商品已在单据中，请修改现有行。");
                        return;
                      }
                      setLines((x) => [
                        ...x,
                        {
                          product_id: product.id,
                          unit_id: snapshot.unit_id,
                          qty: String(snapshot.qty),
                          input_unit_cost: null,
                          direction: "IN",
                          label: product.sku + " · " + product.name,
                          unitLabel,
                        },
                      ]);
                      setShowPicker(false);
                      setError("");
                    }}
                  />
                </div>
              )}
              {lines.map((line, i) => (
                <div
                  key={line.product_id}
                  className="space-y-3 rounded-lg border border-border p-4"
                >
                  <div className="flex justify-between gap-3">
                    <p className="text-sm font-medium">{line.label}</p>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        setLines((x) => x.filter((_, n) => n !== i))
                      }
                    >
                      移除
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className="grid gap-1 text-sm">
                      {kind === "STOCKTAKE" ? "实盘数量" : "数量"}
                      <Input
                        inputMode="decimal"
                        value={String(line.qty)}
                        onChange={(e) =>
                          setLines((x) =>
                            x.map((r, n) =>
                              n === i ? { ...r, qty: e.target.value } : r,
                            ),
                          )
                        }
                      />
                    </label>
                    {kind === "ADJUSTMENT" && (
                      <label className="grid gap-1 text-sm">
                        调整方向
                        <select
                          className={selectClass}
                          value={line.direction}
                          onChange={(e) =>
                            setLines((x) =>
                              x.map((r, n) =>
                                n === i
                                  ? {
                                      ...r,
                                      direction: e.target.value as "IN" | "OUT",
                                    }
                                  : r,
                              ),
                            )
                          }
                        >
                          <option value="IN">增加库存</option>
                          <option value="OUT">减少库存</option>
                        </select>
                      </label>
                    )}
                    {kind !== "TRANSFER" && (
                      <label className="grid gap-1 text-sm">
                        基本单位成本
                        <Input
                          inputMode="decimal"
                          placeholder="入库或盘盈时必填"
                          value={line.input_unit_cost ?? ""}
                          onChange={(e) =>
                            setLines((x) =>
                              x.map((r, n) =>
                                n === i
                                  ? { ...r, input_unit_cost: e.target.value }
                                  : r,
                              ),
                            )
                          }
                        />
                      </label>
                    )}
                  </div>
                </div>
              ))}
            </fieldset>
            <div className="mt-5 flex justify-end gap-3">
              <Button
                variant="outline"
                disabled={busy || uncertain}
                onClick={() => setEditor(false)}
              >
                取消
              </Button>
              <Button
                disabled={busy}
                onClick={() => void form.handleSubmit(save)()}
              >
                {busy ? "正在保存…" : uncertain ? "重试原提交" : "保存草稿"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open && !busy && !pending.current) setConfirmation(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/30" />
          <Dialog.Popup className="fixed top-[20vh] left-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 rounded-xl bg-white p-6 shadow-xl">
            <Dialog.Title className="text-lg font-semibold">
              {confirmation === "reverse"
                ? "确认冲销"
                : confirmation === "refresh"
                  ? "刷新并重新复核盘点"
                  : "确认过账"}
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm leading-6 text-muted-foreground">
              {confirmation === "reverse"
                ? "将整单恢复到原操作前的库存。已有后续变动时会拒绝冲销。"
                : confirmation === "refresh"
                  ? "更新盘点基准，保留实盘输入。刷新后请重新核对或复盘，再过账。"
                  : "请核对单据的商品、数量、单位、成本和仓库。过账会立即改变库存，单据内容随后不能修改。"}
            </Dialog.Description>
            <p className="mt-3 text-sm">
              {detail?.number} · {detail?.warehouse_name}
            </p>
            {confirmation === "reverse" && (
              <label className="mt-3 grid gap-1 text-sm">
                冲销原因
                <Input
                  disabled={busy || uncertain}
                  value={reverseReason}
                  onChange={(e) => setReverseReason(e.target.value)}
                />
              </label>
            )}
            {error && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-3">
              <Button
                variant="outline"
                disabled={busy || uncertain}
                onClick={() => setConfirmation(null)}
              >
                取消
              </Button>
              <Button disabled={busy} onClick={() => void confirm()}>
                {busy
                  ? "正在处理…"
                  : uncertain
                    ? "重试原提交"
                    : confirmation === "reverse"
                      ? "确认冲销"
                      : confirmation === "refresh"
                        ? "确认刷新"
                        : "确认过账"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
