"use client";
import { OrderFundsSummary } from "@/features/funds/order-summary";
import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
} from "@tanstack/react-table";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { Dialog } from "@base-ui/react/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { ProductPicker } from "@/features/catalog/picker";
import { unwrap } from "@/features/inventory/client";
import {
  command,
  getDetail,
  saveDocument,
  saveOrder,
  type Action,
  type Order,
  type StockDocument,
  type Receipt,
} from "./client";

type Tab = "orders" | "RECEIPT" | "RETURN" | "prices";
type Selection = { id: string; document: boolean };
type Line = {
  product_id: string;
  unit_id: string;
  source_line_id?: string;
  label: string;
  unitLabel: string;
  qty: string;
  unit_price: string;
  limit?: string;
};
type Editor = {
  kind: "ORDER" | "RECEIPT" | "RETURN";
  source_id?: string;
  previous?: Order | StockDocument;
};
type Fields = {
  supplier: string;
  warehouse: string;
  reason: string;
  lines: Line[];
};
const numeric = z.string().regex(/^\d+(?:\.\d{1,6})?$/);
const formSchema = z.object({
  reason: z.string().trim().min(1),
  lines: z
    .array(z.object({ qty: numeric, unit_price: numeric }))
    .min(1)
    .max(200),
});
const names = {
  DRAFT: "草稿",
  CONFIRMED: "已确认",
  CLOSED: "已关闭",
  CANCELLED: "已取消",
  POSTED: "已过账",
  REVERSED: "已冲销",
  UNRECEIVED: "未收货",
  PARTIAL: "部分收货",
  RECEIVED: "已收齐",
};
const tabs: { id: Tab; label: string }[] = [
  { id: "orders", label: "采购订单" },
  { id: "RECEIPT", label: "采购收货" },
  { id: "RETURN", label: "采购退货" },
  { id: "prices", label: "历史采购价" },
];
const actionNames: Record<Action, string> = {
  confirm: "确认订单",
  cancel: "取消订单",
  close: "关闭剩余",
  post: "过账",
  reverse: "冲销",
};
const number = (v: string | null | undefined) =>
  v == null
    ? "—"
    : v.includes(".")
      ? v.replace(/0+$/, "").replace(/\.$/, "")
      : v;
const positive = (v: string) => !/^0(?:\.0*)?$/.test(v);
function Grid({
  headers,
  rows,
}: {
  headers: string[];
  rows: { id: string; cells: ReactNode[] }[];
}) {
  const columns: ColumnDef<{ id: string; cells: ReactNode[] }>[] = headers.map(
    (header, n) => ({
      id: String(n),
      header,
      cell: ({ row }) => row.original.cells[n],
    }),
  );
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    getRowId: (r) => r.id,
  });
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-[#f4f6f2] text-xs text-muted-foreground">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((h) => (
                <th
                  className="px-4 py-3 whitespace-nowrap font-normal"
                  key={h.id}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr className="border-t border-border" key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td className="px-4 py-3 tabular-nums" key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <p className="p-8 text-center text-sm text-muted-foreground">
          暂无采购记录。
        </p>
      )}
    </div>
  );
}
export function PurchasingWorkspace({
  permissions,
}: {
  permissions: string[];
}) {
  const params = useSearchParams(),
    qc = useQueryClient(),
    read = permissions.includes("purchase.read"),
    cost = permissions.includes("product.cost.read");
  const [tab, setTab] = useState<Tab>(
    tabs.some((t) => t.id === params.get("tab"))
      ? (params.get("tab") as Tab)
      : "orders",
  );
  const [page, setPage] = useState(1),
    [q, setQ] = useState(params.get("q") ?? ""),
    [referenceSearch, setReferenceSearch] = useState("");
  const [selected, setSelected] = useState<Selection | null>(
    params.get("document")
      ? { id: params.get("document")!, document: true }
      : null,
  );
  const [editor, setEditor] = useState<Editor | null>(null),
    [picker, setPicker] = useState(false);
  const [confirmation, setConfirmation] = useState<{
    id: string;
    version: number;
    action: Action;
    document: boolean;
  } | null>(null);
  const [reason, setReason] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false);
  const inFlight = useRef(false),
    pending = useRef<{
      key: string;
      operation: (key: string) => Promise<Receipt>;
      document: boolean;
      done: () => void;
    } | null>(null);
  const form = useForm<Fields>({
    defaultValues: { supplier: "", warehouse: "", reason: "", lines: [] },
  });
  const fields = useWatch({ control: form.control });
  const lines = fields.lines ?? [];
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    if (q) url.searchParams.set("q", q);
    else url.searchParams.delete("q");
    if (selected?.document) url.searchParams.set("document", selected.id);
    else url.searchParams.delete("document");
    window.history.replaceState(
      window.history.state,
      "",
      url.pathname + url.search,
    );
  }, [tab, q, selected]);
  const suppliers = useQuery({
    queryKey: ["catalog", "purchase-suppliers", referenceSearch],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/suppliers", {
          params: { query: { q: referenceSearch, page_size: 100 } },
        }),
      ),
    enabled: read && permissions.includes("supplier.read"),
  });
  const warehouses = useQuery({
    queryKey: ["catalog", "purchase-warehouses", referenceSearch],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/warehouses", {
          params: { query: { q: referenceSearch, page_size: 100 } },
        }),
      ),
    enabled: read && permissions.includes("warehouse.read"),
  });
  const orders = useQuery({
    queryKey: ["purchasing", "orders", page, q],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/purchasing/orders", {
          params: { query: { page, page_size: 25, q } },
        }),
      ),
    enabled: read && tab === "orders",
  });
  const documents = useQuery({
    queryKey: ["purchasing", "documents", page, tab],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/purchasing/documents", {
          params: {
            query: { page, page_size: 25, kind: tab as "RECEIPT" | "RETURN" },
          },
        }),
      ),
    enabled: read && (tab === "RECEIPT" || tab === "RETURN"),
  });
  const prices = useQuery({
    queryKey: ["purchasing", "prices", page],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/purchasing/price-history", {
          params: { query: { page, page_size: 25 } },
        }),
      ),
    enabled: read && cost && tab === "prices",
  });
  const detail = useQuery({
    queryKey: ["purchasing", "detail", selected?.document, selected?.id],
    queryFn: () => getDetail(selected!.id, selected!.document),
    enabled: read && !!selected,
  });
  const order =
    selected && !selected.document
      ? (detail.data as Order | undefined)
      : undefined;
  const document = selected?.document
    ? (detail.data as StockDocument | undefined)
    : undefined;
  const active =
    tab === "orders" ? orders : tab === "prices" ? prices : documents;
  const can = (permission: string) => cost && permissions.includes(permission);
  const locked = busy || uncertain;
  async function execute(
    operation?: (key: string) => Promise<Receipt>,
    isDocument = false,
    done = () => {},
  ) {
    if (inFlight.current) return;
    if (!pending.current) {
      if (!operation) return;
      pending.current = {
        key: crypto.randomUUID(),
        operation,
        document: isDocument,
        done,
      };
    }
    const request = pending.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    let committed: Receipt | null = null;
    try {
      committed = await request.operation(request.key);
      pending.current = null;
      setUncertain(false);
      await qc.invalidateQueries({ queryKey: ["purchasing"] });
      await qc.invalidateQueries({ queryKey: ["inventory"] });
      await qc.fetchQuery({
        queryKey: ["purchasing", "detail", request.document, committed.id],
        queryFn: () => getDetail(committed!.id, request.document),
      });
      setSelected({ id: committed.id, document: request.document });
      request.done();
      setNotice("操作已完成，数据已刷新。");
    } catch (e) {
      if (committed) {
        setSelected({ id: committed.id, document: request.document });
        request.done();
        setNotice("操作已提交，详情暂时无法刷新，请重新加载。");
      } else {
        if (e instanceof ApiError && e.status >= 400 && e.status < 500)
          pending.current = null;
        setUncertain(!!pending.current);
        setError(
          (e instanceof Error ? e.message : String(e)) +
            (pending.current ? " 提交结果待确认，请重试原提交。" : ""),
        );
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function editOrder(previous?: Order) {
    if (locked) return;
    setError("");
    setEditor({ kind: "ORDER", previous });
    form.reset({
      supplier: previous?.supplier_id ?? "",
      warehouse: previous?.warehouse_id ?? "",
      reason: previous?.reason ?? "",
      lines:
        previous?.lines?.map((l) => ({
          product_id: l.product_id,
          unit_id: l.unit_id,
          label: l.product_label,
          unitLabel: l.unit_label,
          qty: l.qty,
          unit_price: l.unit_price ?? "",
        })) ?? [],
    });
  }
  function newStock(kind: "RECEIPT" | "RETURN", source: Order | StockDocument) {
    if (locked) return;
    const items =
      kind === "RECEIPT"
        ? ((source as Order).lines ?? []).map((l) => ({
            ...l,
            limit: l.remaining_qty,
          }))
        : ((source as StockDocument).lines ?? []).map((l) => ({
            ...l,
            limit: l.returnable_qty,
          }));
    const remaining = items.filter((l) => positive(l.limit));
    if (!remaining.length) {
      setError("来源单据没有剩余可处理数量。");
      return;
    }
    setError("");
    setEditor({ kind, source_id: source.id });
    form.reset({
      supplier: source.supplier_id,
      warehouse: source.warehouse_id,
      reason: "",
      lines: remaining.map((l) => ({
        source_line_id: l.id,
        product_id: l.product_id,
        unit_id: l.unit_id,
        label: l.product_label,
        unitLabel: l.unit_label,
        qty: l.limit,
        limit: l.limit,
        unit_price: l.unit_price ?? "",
      })),
    });
  }
  function editStock(doc: StockDocument) {
    if (locked) return;
    setEditor({
      kind: doc.kind,
      source_id:
        doc.kind === "RECEIPT" ? doc.order_id : doc.original_document_id!,
      previous: doc,
    });
    setError("");
    form.reset({
      supplier: doc.supplier_id,
      warehouse: doc.warehouse_id,
      reason: doc.reason,
      lines: (doc.lines ?? []).map((l) => ({
        product_id: l.product_id,
        unit_id: l.unit_id,
        source_line_id:
          doc.kind === "RECEIPT" ? l.order_line_id : l.receipt_line_id!,
        label: l.product_label,
        unitLabel: l.unit_label,
        qty: l.qty,
        unit_price: l.unit_price ?? "",
      })),
    });
  }
  async function save() {
    if (pending.current) {
      await execute();
      return;
    }
    if (!editor) return;
    const v = form.getValues();
    if (!formSchema.safeParse(v).success) {
      setError(
        "请填写原因、商品数量和采购单价（最多6位小数）；零价请明确输入0。",
      );
      return;
    }
    if (editor.kind === "ORDER") {
      if (
        !z.string().uuid().safeParse(v.supplier).success ||
        !z.string().uuid().safeParse(v.warehouse).success
      ) {
        setError("请选择供应商和收货仓库。");
        return;
      }
      const body = {
        supplier_id: v.supplier,
        warehouse_id: v.warehouse,
        reason: v.reason,
        lines: v.lines.map((l) => ({
          product_id: l.product_id,
          unit_id: l.unit_id,
          qty: l.qty,
          unit_price: l.unit_price,
        })),
      };
      await execute(
        (key) => saveOrder(body, key, editor.previous as Order | undefined),
        false,
        () => setEditor(null),
      );
    } else {
      const body = {
        source_id: editor.source_id!,
        reason: v.reason,
        lines: v.lines.map((l) => ({
          source_line_id: l.source_line_id!,
          qty: l.qty,
          unit_price: editor.kind === "RETURN" ? null : l.unit_price,
        })),
      };
      await execute(
        (key) =>
          saveDocument(
            editor.kind as "RECEIPT" | "RETURN",
            body,
            key,
            editor.previous as StockDocument | undefined,
          ),
        true,
        () => setEditor(null),
      );
    }
  }
  function ask(
    action: Action,
    row: Order | StockDocument,
    isDocument: boolean,
  ) {
    if (locked) return;
    setReason("");
    setError("");
    setConfirmation({
      id: row.id,
      version: row.version,
      action,
      document: isDocument,
    });
  }
  async function confirm() {
    if (pending.current) {
      await execute();
      return;
    }
    if (!confirmation) return;
    if (
      ["cancel", "close", "reverse"].includes(confirmation.action) &&
      !reason.trim()
    ) {
      setError("请填写操作原因。");
      return;
    }
    const c = confirmation;
    await execute(
      (key) => command(c.id, c.version, c.action, reason, key),
      c.document,
      () => setConfirmation(null),
    );
  }
  async function applyHistoricalPrice(index: number) {
    const v = form.getValues(),
      line = v.lines[index];
    if (!v.supplier) {
      setError("请先选择供应商。");
      return;
    }
    try {
      const data = unwrap(
        await api.GET("/api/v1/purchasing/price-history", {
          params: {
            query: {
              supplier_id: v.supplier,
              product_id: line.product_id,
              page_size: 100,
            },
          },
        }),
      );
      const price = data.items.find((p) => p.unit_id === line.unit_id);
      if (!price) {
        setError("未找到当前供应商和采购单位的历史收货价，请明确填写。");
        return;
      }
      form.setValue(`lines.${index}.unit_price`, price.unit_price);
      setNotice("已沿用最近有效收货价，请核对。");
    } catch (e) {
      setError(String(e));
    }
  }
  if (!read)
    return (
      <p role="alert" className="p-6">
        当前账户没有查看采购的权限。
      </p>
    );
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        确认采购，再按实收数量入库。采购金额供业务核对，资金结算尚未启用。
      </p>
      <div className="flex flex-wrap gap-2">
        {tabs
          .filter((t) => t.id !== "prices" || cost)
          .map((t) => (
            <Button
              key={t.id}
              variant={tab === t.id ? "default" : "outline"}
              disabled={locked}
              onClick={() => {
                setTab(t.id);
                setPage(1);
                setSelected(null);
              }}
            >
              {t.label}
            </Button>
          ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {tab === "orders" && (
          <Input
            aria-label="搜索采购订单"
            className="max-w-xs"
            placeholder="订单号或供应商"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        )}
        <Button
          variant="outline"
          disabled={locked}
          onClick={() =>
            void qc.invalidateQueries({ queryKey: ["purchasing"] })
          }
        >
          刷新数据
        </Button>
        {tab === "orders" && can("purchase.order.write") && (
          <Button disabled={locked} onClick={() => editOrder()}>
            新建采购订单
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm text-primary">
          {notice}
        </p>
      )}
      {active.isFetching && (
        <p role="status" className="text-sm">
          正在加载采购数据…
        </p>
      )}
      {active.error && <p role="alert">{active.error.message}</p>}
      {tab === "orders" && (
        <Grid
          headers={[
            "订单号",
            "供应商 / 仓库",
            "订单状态",
            "收货状态",
            ...(cost ? ["订单金额"] : []),
            "操作",
          ]}
          rows={(orders.data?.items ?? []).map((r) => ({
            id: r.id,
            cells: [
              r.number,
              <div key="party">
                {r.supplier_name}
                <p className="text-xs text-muted-foreground">
                  {r.warehouse_name}
                </p>
              </div>,
              names[r.status],
              names[r.receiving_status],
              ...(cost ? [number(r.amount)] : []),
              <Button
                key="view"
                variant="outline"
                disabled={locked}
                onClick={() => setSelected({ id: r.id, document: false })}
              >
                查看
              </Button>,
            ],
          }))}
        />
      )}
      {(tab === "RECEIPT" || tab === "RETURN") && (
        <Grid
          headers={[
            "单据号",
            "采购订单",
            "供应商",
            "状态",
            "原因",
            ...(cost ? [tab === "RETURN" ? "退货参考金额" : "收货金额"] : []),
            "操作",
          ]}
          rows={(documents.data?.items ?? []).map((r) => ({
            id: r.id,
            cells: [
              r.number,
              r.order_number,
              r.supplier_name,
              names[r.status],
              r.reason,
              ...(cost ? [number(r.amount)] : []),
              <Button
                key="view"
                variant="outline"
                disabled={locked}
                onClick={() => setSelected({ id: r.id, document: true })}
              >
                查看
              </Button>,
            ],
          }))}
        />
      )}
      {tab === "prices" && cost && (
        <Grid
          headers={[
            "商品",
            "供应商",
            "采购单位",
            "收货单价",
            "收货数量",
            "来源",
          ]}
          rows={(prices.data?.items ?? []).map((r, n) => ({
            id: r.document_id + String(n),
            cells: [
              r.product_label,
              r.supplier_name,
              r.unit_label,
              number(r.unit_price),
              number(r.qty),
              <Button
                key="source"
                variant="link"
                disabled={locked}
                onClick={() =>
                  setSelected({ id: r.document_id, document: true })
                }
              >
                {r.document_number}
              </Button>,
            ],
          }))}
        />
      )}
      <div className="flex justify-end items-center gap-3 text-sm">
        <Button
          variant="outline"
          disabled={locked || page === 1}
          onClick={() => setPage((p) => p - 1)}
        >
          上一页
        </Button>
        <span>第 {page} 页</span>
        <Button
          variant="outline"
          disabled={locked || page * 25 >= (active.data?.total ?? 0)}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>
      {selected && detail.isFetching && <p role="status">正在加载单据详情…</p>}
      {detail.error && selected && <p role="alert">{detail.error.message}</p>}
      {order && (
        <section
          aria-label="采购订单详情"
          className="space-y-4 rounded-xl border bg-white p-5"
        >
          <div className="flex justify-between gap-3">
            <div>
              <h2 className="font-semibold">采购订单 · {order.number}</h2>
              <p className="text-sm text-muted-foreground">
                {names[order.status]} · {names[order.receiving_status]} ·{" "}
                {order.supplier_name} · {order.warehouse_name}
              </p>
            </div>
            <Button
              variant="ghost"
              disabled={locked}
              onClick={() => setSelected(null)}
            >
              关闭详情
            </Button>
          </div>
          <p className="text-sm">原因：{order.reason}</p>
          <OrderFundsSummary
            value={order.funds}
            side="AP"
            partyId={order.supplier_id}
            permissions={permissions}
            locked={locked}
          />
          <Grid
            headers={[
              "商品",
              "数量 / 单位",
              "基本数量",
              "已收 / 待收（基本单位）",
              "已退",
              ...(cost ? ["采购单价", "金额"] : []),
            ]}
            rows={(order.lines ?? []).map((l) => ({
              id: l.id,
              cells: [
                l.product_label,
                number(l.qty) + " " + l.unit_label,
                number(l.base_qty),
                number(l.received_base_qty) +
                  " / " +
                  number(l.remaining_base_qty),
                number(l.returned_base_qty),
                ...(cost ? [number(l.unit_price), number(l.amount)] : []),
              ],
            }))}
          />
          <div className="flex flex-wrap gap-2">
            {order.status === "DRAFT" && can("purchase.order.write") && (
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => editOrder(order)}
              >
                编辑订单
              </Button>
            )}
            {order.status === "DRAFT" && can("purchase.order.confirm") && (
              <Button
                disabled={locked}
                onClick={() => ask("confirm", order, false)}
              >
                确认订单
              </Button>
            )}
            {["DRAFT", "CONFIRMED"].includes(order.status) &&
              can("purchase.order.cancel") && (
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={() => ask("cancel", order, false)}
                >
                  取消订单
                </Button>
              )}
            {order.status === "CONFIRMED" && can("purchase.order.close") && (
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => ask("close", order, false)}
              >
                关闭剩余
              </Button>
            )}
            {order.status === "CONFIRMED" && can("purchase.receive") && (
              <Button
                disabled={locked}
                onClick={() => newStock("RECEIPT", order)}
              >
                创建收货单
              </Button>
            )}
          </div>
        </section>
      )}
      {document && (
        <section
          aria-label="采购库存单据详情"
          className="space-y-4 rounded-xl border bg-white p-5"
        >
          <div className="flex justify-between gap-3">
            <div>
              <h2 className="font-semibold">
                {document.kind === "RECEIPT" ? "采购收货" : "采购退货"} ·{" "}
                {document.number}
              </h2>
              <p className="text-sm text-muted-foreground">
                {names[document.status]} · {document.supplier_name} ·{" "}
                {document.warehouse_name}
              </p>
            </div>
            <Button
              variant="ghost"
              disabled={locked}
              onClick={() => setSelected(null)}
            >
              关闭详情
            </Button>
          </div>
          <p className="text-sm">原因：{document.reason}</p>
          <Button
            variant="link"
            disabled={locked}
            onClick={() =>
              setSelected({ id: document.order_id, document: false })
            }
          >
            来源订单 {document.order_number}
          </Button>
          <Grid
            headers={[
              "商品",
              "数量 / 单位",
              "基本数量",
              ...(document.kind === "RECEIPT" ? ["剩余可退"] : []),
              ...(cost
                ? [
                    "采购单位价",
                    "参考金额",
                    "库存金额变化",
                    ...(document.kind === "RETURN"
                      ? ["参考金额减库存成本"]
                      : []),
                  ]
                : []),
            ]}
            rows={(document.lines ?? []).map((l) => ({
              id: l.id,
              cells: [
                l.product_label,
                number(l.qty) + " " + l.unit_label,
                number(l.base_qty),
                ...(document.kind === "RECEIPT"
                  ? [number(l.returnable_qty)]
                  : []),
                ...(cost
                  ? [
                      number(l.unit_price),
                      number(l.amount),
                      number(l.inventory_value_delta),
                      ...(document.kind === "RETURN"
                        ? [number(l.valuation_difference)]
                        : []),
                    ]
                  : []),
              ],
            }))}
          />
          {document.kind === "RETURN" && (
            <p className="text-xs text-muted-foreground">
              退货参考金额沿用原收货价；库存成本按当前均价减少。这里不代表退款或应付核销。
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {document.status === "DRAFT" &&
              can(
                document.kind === "RECEIPT"
                  ? "purchase.receive"
                  : "purchase.return",
              ) && (
                <>
                  <Button
                    variant="outline"
                    disabled={locked}
                    onClick={() => editStock(document)}
                  >
                    编辑草稿
                  </Button>
                  <Button
                    disabled={locked}
                    onClick={() => ask("post", document, true)}
                  >
                    过账
                  </Button>
                </>
              )}
            {document.status === "POSTED" &&
              can("purchase.reverse") &&
              can(
                document.kind === "RECEIPT"
                  ? "purchase.receive"
                  : "purchase.return",
              ) && (
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={() => ask("reverse", document, true)}
                >
                  冲销
                </Button>
              )}
            {document.kind === "RECEIPT" &&
              document.status === "POSTED" &&
              can("purchase.return") && (
                <Button
                  disabled={locked}
                  onClick={() => newStock("RETURN", document)}
                >
                  创建退货单
                </Button>
              )}
            <Link
              className="px-3 py-2 text-sm text-primary"
              href="/inventory?tab=movements"
            >
              查看库存流水
            </Link>
          </div>
        </section>
      )}
      <Dialog.Root
        open={!!editor}
        onOpenChange={(open) => {
          if (!open && !locked) setEditor(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/30" />
          <Dialog.Popup className="fixed top-[5vh] left-1/2 z-50 max-h-[90vh] w-[min(94vw,960px)] -translate-x-1/2 overflow-y-auto rounded-xl bg-white p-6 shadow-xl">
            <Dialog.Title className="text-lg font-semibold">
              {editor?.kind === "ORDER"
                ? "采购订单草稿"
                : editor?.kind === "RECEIPT"
                  ? "采购收货草稿"
                  : "采购退货草稿"}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              数量按所选采购单位填写，保存草稿不改变库存。收货与退货必须确认过账。
            </Dialog.Description>
            {error && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {error}
              </p>
            )}
            <fieldset disabled={locked} className="mt-4 space-y-4">
              {editor?.kind === "ORDER" && (
                <>
                  <Input
                    aria-label="搜索采购关联资料"
                    placeholder="搜索供应商或仓库"
                    value={referenceSearch}
                    onChange={(e) => setReferenceSearch(e.target.value)}
                  />
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="grid gap-1 text-sm">
                      采购供应商
                      <select
                        aria-label="采购供应商"
                        className="h-10 rounded-md border px-3"
                        {...form.register("supplier")}
                      >
                        <option value="">请选择供应商</option>
                        {suppliers.data?.items.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="grid gap-1 text-sm">
                      收货仓库
                      <select
                        aria-label="收货仓库"
                        className="h-10 rounded-md border px-3"
                        {...form.register("warehouse")}
                      >
                        <option value="">请选择仓库</option>
                        {warehouses.data?.items.map((w) => (
                          <option key={w.id} value={w.id}>
                            {w.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </>
              )}
              <label className="grid gap-1 text-sm">
                采购单据原因
                <Input {...form.register("reason")} />
              </label>
              {editor?.kind === "ORDER" && (
                <Button variant="outline" onClick={() => setPicker(true)}>
                  添加采购商品
                </Button>
              )}
              {picker && (
                <ProductPicker
                  permissions={permissions}
                  onSelect={({ product, snapshot, unitLabel }) => {
                    if (
                      form
                        .getValues("lines")
                        .some((l) => l.product_id === product.id)
                    ) {
                      setError("同一商品只能添加一行。");
                      return;
                    }
                    form.setValue("lines", [
                      ...form.getValues("lines"),
                      {
                        product_id: product.id,
                        unit_id: snapshot.unit_id,
                        label: product.sku + " · " + product.name,
                        unitLabel,
                        qty: snapshot.qty,
                        unit_price: "",
                      },
                    ]);
                    setPicker(false);
                  }}
                />
              )}
              {lines.map((l, n) => (
                <div key={l.product_id} className="rounded-lg border p-4">
                  <div className="mb-3 flex justify-between gap-2">
                    <p className="text-sm font-medium">
                      {l.label} · {l.unitLabel}
                      {l.limit ? ` · 可处理 ${number(l.limit)}` : ""}
                    </p>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        form.setValue(
                          "lines",
                          form.getValues("lines").filter((_, i) => i !== n),
                        )
                      }
                    >
                      移除
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="grid gap-1 text-sm">
                      采购数量
                      <Input
                        inputMode="decimal"
                        {...form.register(`lines.${n}.qty`)}
                      />
                    </label>
                    <label className="grid gap-1 text-sm">
                      采购单位单价
                      <Input
                        inputMode="decimal"
                        readOnly={editor?.kind === "RETURN"}
                        placeholder="明确填写，零价输入0"
                        {...form.register(`lines.${n}.unit_price`)}
                      />
                    </label>
                  </div>
                  {editor?.kind === "ORDER" && (
                    <Button
                      variant="link"
                      onClick={() => void applyHistoricalPrice(n)}
                    >
                      沿用最近采购价
                    </Button>
                  )}
                </div>
              ))}
            </fieldset>
            <div className="mt-5 flex justify-end gap-3">
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => setEditor(null)}
              >
                取消
              </Button>
              <Button
                disabled={busy}
                onClick={() => void form.handleSubmit(save)()}
              >
                {busy ? "正在保存…" : uncertain ? "重试原提交" : "保存采购草稿"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open && !locked) setConfirmation(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/30" />
          <Dialog.Popup className="fixed top-[20vh] left-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 rounded-xl bg-white p-6 shadow-xl">
            <Dialog.Title className="text-lg font-semibold">
              复核{confirmation ? actionNames[confirmation.action] : "操作"}
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm text-muted-foreground">
              {confirmation?.action === "confirm"
                ? "确认后采购条款冻结，库存仍不变。"
                : confirmation?.action === "post"
                  ? "将按实际数量过账并改变库存，请核对数量、价格和仓库。"
                  : confirmation?.action === "reverse"
                    ? "仅没有后续库存依赖的整单可以冲销。"
                    : "请说明原因；操作后不能直接重开订单。"}
            </Dialog.Description>
            {confirmation &&
              ["cancel", "close", "reverse"].includes(confirmation.action) && (
                <label className="mt-4 grid gap-1 text-sm">
                  采购操作原因
                  <Input
                    disabled={locked}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
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
                disabled={locked}
                onClick={() => setConfirmation(null)}
              >
                取消
              </Button>
              <Button disabled={busy} onClick={() => void confirm()}>
                {busy ? "正在处理…" : uncertain ? "重试原提交" : "确认执行"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
