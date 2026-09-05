"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  quote,
  type Action,
  type Order,
  type StockDocument,
  type Receipt,
  type PriceQuote,
  type OrderInput,
} from "./client";
import { Grid, names, number, positive, PriceProvenance } from "./presentation";
import { usePendingNavigationGuard } from "./navigation";

type Tab = "orders" | "SHIPMENT" | "RETURN" | "prices";
type Selection = { id: string; document: boolean };
type Line = {
  key: string;
  product_id: string;
  unit_id: string;
  source_line_id?: string;
  label: string;
  unitLabel: string;
  qty: string;
  unit_price: string;
  pricing_mode: "AUTO" | "MANUAL";
  quote?: PriceQuote;
  reviewed: boolean;
  quoteLoading?: boolean;
  quoteError?: string;
  limit?: string;
};
type Editor = {
  kind: "ORDER" | "SHIPMENT" | "RETURN";
  source_id?: string;
  previous?: Order | StockDocument;
};
type Fields = {
  customer: string;
  warehouse: string;
  reason: string;
  lines: Line[];
};
const numeric = z.string().regex(/^\d+(?:\.\d{1,6})?$/);
const formSchema = z.object({
  reason: z.string().trim().min(1).max(2000),
  lines: z
    .array(z.object({ qty: numeric.refine(positive) }))
    .min(1)
    .max(200),
});
const tabs: { id: Tab; label: string }[] = [
  { id: "orders", label: "销售订单" },
  { id: "SHIPMENT", label: "销售出库" },
  { id: "RETURN", label: "销售退货" },
  { id: "prices", label: "客户成交历史" },
];
const actionNames: Record<Action, string> = {
  confirm: "确认订单",
  cancel: "取消订单",
  close: "关闭剩余",
  post: "过账",
  reverse: "冲销",
};
const selectClass =
  "h-10 min-w-0 max-w-full rounded-md border bg-white px-3 text-sm";

export function SalesWorkspace({ permissions }: { permissions: string[] }) {
  const params = useSearchParams(),
    qc = useQueryClient();
  const read = permissions.includes("sales.read"),
    price = read && permissions.includes("product.price.read"),
    cost = price && permissions.includes("product.cost.read"),
    stock = permissions.includes("inventory.read");
  const has = (permission: string) => permissions.includes(permission);
  const canQuote = price && has("customer.read") && has("catalog.read");
  const [tab, setTab] = useState<Tab>(
    tabs.some((item) => item.id === params.get("tab"))
      ? (params.get("tab") as Tab)
      : "orders",
  );
  const [page, setPage] = useState(1),
    [q, setQ] = useState((params.get("q") ?? "").slice(0, 200));
  const [customerFilter, setCustomerFilter] = useState(
      params.get("customer") ?? "",
    ),
    [statusFilter, setStatusFilter] = useState("");
  const [historyProduct, setHistoryProduct] = useState(
    params.get("product") ?? "",
  );
  const [referenceSearch, setReferenceSearch] = useState("");
  const [selected, setSelected] = useState<Selection | null>(
    params.get("document")
      ? { id: params.get("document")!, document: true }
      : params.get("order")
        ? { id: params.get("order")!, document: false }
        : null,
  );
  const [editor, setEditor] = useState<Editor | null>(null),
    [picker, setPicker] = useState<{
      generation: number;
      customer: string;
    } | null>(null);
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
    [uncertain, setUncertain] = useState(false),
    [conflict, setConflict] = useState(false);
  const inFlight = useRef(false),
    generation = useRef(0),
    pickerGeneration = useRef(0),
    quoteRequests = useRef(new Map<string, AbortController>());
  const pending = useRef<{
    key: string;
    operation: (key: string) => Promise<Receipt>;
    document: boolean;
    uncertain: boolean;
    done: (saved?: Order | StockDocument) => string | void;
  } | null>(null);
  const form = useForm<Fields>({
    defaultValues: { customer: "", warehouse: "", reason: "", lines: [] },
  });
  const fields = useWatch({ control: form.control }),
    lines = (fields.lines ?? []) as Line[];
  const locked = busy || uncertain;
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    for (const [key, value] of [
      ["q", q],
      ["customer", customerFilter],
      ["product", historyProduct],
      ["document", selected?.document ? selected.id : ""],
      ["order", selected && !selected.document ? selected.id : ""],
    ]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.replaceState(
      window.history.state,
      "",
      url.pathname + url.search,
    );
  }, [tab, q, customerFilter, historyProduct, selected]);
  usePendingNavigationGuard(locked);
  useEffect(() => {
    const requests = quoteRequests.current;
    return () => {
      for (const controller of requests.values()) controller.abort();
    };
  }, []);
  useEffect(() => {
    if (canQuote) return;
    for (const controller of quoteRequests.current.values()) controller.abort();
    quoteRequests.current.clear();
    form.setValue(
      "lines",
      form.getValues("lines").map((line) => ({
        ...line,
        quoteLoading: false,
        reviewed: false,
        quote: undefined,
        unit_price: "",
      })),
    );
  }, [canQuote, form]);
  const customers = useQuery({
    queryKey: ["catalog", "sales-customers", referenceSearch],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/customers", {
          signal,
          params: { query: { q: referenceSearch, page_size: 100 } },
        }),
      ),
    enabled: read && has("customer.read"),
  });
  const warehouses = useQuery({
    queryKey: ["catalog", "sales-warehouses", referenceSearch],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/warehouses", {
          signal,
          params: { query: { q: referenceSearch, page_size: 100 } },
        }),
      ),
    enabled: read && has("warehouse.read"),
  });
  const orders = useQuery({
    queryKey: ["sales", "orders", page, q, customerFilter, statusFilter],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/sales/orders", {
          signal,
          params: {
            query: {
              page,
              page_size: 25,
              q,
              customer_id: customerFilter || undefined,
              status: (statusFilter || undefined) as
                | Order["status"]
                | undefined,
            },
          },
        }),
      ),
    enabled: read && tab === "orders",
  });
  const documents = useQuery({
    queryKey: [
      "sales",
      "documents",
      page,
      tab,
      q,
      customerFilter,
      statusFilter,
    ],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/sales/documents", {
          signal,
          params: {
            query: {
              page,
              page_size: 25,
              kind: tab as "SHIPMENT" | "RETURN",
              q,
              customer_id: customerFilter || undefined,
              status: (statusFilter || undefined) as
                | StockDocument["status"]
                | undefined,
            },
          },
        }),
      ),
    enabled: read && (tab === "SHIPMENT" || tab === "RETURN"),
  });
  const prices = useQuery({
    queryKey: ["sales", "prices", page, customerFilter, historyProduct],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/sales/price-history", {
          signal,
          params: {
            query: {
              page,
              page_size: 25,
              customer_id: customerFilter || undefined,
              product_id: historyProduct || undefined,
            },
          },
        }),
      ),
    enabled: price && tab === "prices",
  });
  const detail = useQuery({
    queryKey: ["sales", "detail", selected?.document, selected?.id],
    queryFn: ({ signal }) =>
      getDetail(selected!.id, selected!.document, signal),
    enabled: read && !!selected,
  });
  const order =
    selected && !selected.document && detail.data?.id === selected.id
      ? (detail.data as Order)
      : undefined;
  const stockDocument =
    selected?.document && detail.data?.id === selected.id
      ? (detail.data as StockDocument)
      : undefined;
  const active =
    tab === "orders" ? orders : tab === "prices" ? prices : documents;

  function clearQuotes() {
    generation.current += 1;
    pickerGeneration.current += 1;
    for (const controller of quoteRequests.current.values()) controller.abort();
    quoteRequests.current.clear();
    setPicker(null);
  }
  async function loadQuote(lineKey: string) {
    if (inFlight.current || pending.current) return;
    if (!canQuote) {
      setError("当前账户没有获取销售报价的权限。");
      return;
    }
    const index = form
      .getValues("lines")
      .findIndex((line) => line.key === lineKey);
    if (index < 0) return;
    const line = form.getValues(`lines.${index}`),
      customer = form.getValues("customer"),
      capturedGeneration = generation.current;
    if (!customer) {
      setError("请先选择销售客户。");
      return;
    }
    quoteRequests.current.get(lineKey)?.abort();
    const controller = new AbortController();
    quoteRequests.current.set(lineKey, controller);
    form.setValue(`lines.${index}`, {
      ...line,
      pricing_mode: "AUTO",
      reviewed: false,
      unit_price: "",
      quote: undefined,
      quoteLoading: true,
      quoteError: "",
    });
    try {
      const result = await quote(
        customer,
        line.product_id,
        line.unit_id,
        controller.signal,
      );
      const currentIndex = form
          .getValues("lines")
          .findIndex((item) => item.key === lineKey),
        currentLine =
          currentIndex < 0
            ? undefined
            : form.getValues(`lines.${currentIndex}`);
      if (
        controller.signal.aborted ||
        generation.current !== capturedGeneration ||
        form.getValues("customer") !== customer ||
        !currentLine ||
        currentLine.product_id !== result.product_id ||
        currentLine.unit_id !== result.price_source.target_unit_id ||
        result.customer_id !== customer ||
        currentLine.pricing_mode !== "AUTO"
      )
        return;
      form.setValue(`lines.${currentIndex}`, {
        ...currentLine,
        quote: result,
        unit_price: result.unit_price ?? "",
        reviewed: result.unit_price != null,
        quoteLoading: false,
        quoteError:
          result.unit_price == null ? "暂无建议价，请明确选择手动单价。" : "",
      });
    } catch (e) {
      const currentIndex = form
        .getValues("lines")
        .findIndex((item) => item.key === lineKey);
      if (
        !controller.signal.aborted &&
        generation.current === capturedGeneration &&
        currentIndex >= 0 &&
        form.getValues("customer") === customer
      ) {
        const currentLine = form.getValues(`lines.${currentIndex}`);
        form.setValue(`lines.${currentIndex}`, {
          ...currentLine,
          quoteLoading: false,
          reviewed: false,
          quoteError:
            e instanceof Error ? e.message : "报价暂时不可用，请重试。",
        });
      }
    } finally {
      if (quoteRequests.current.get(lineKey) === controller)
        quoteRequests.current.delete(lineKey);
    }
  }
  async function execute(
    operation?: (key: string) => Promise<Receipt>,
    isDocument = false,
    done: (saved?: Order | StockDocument) => string | void = () => {},
  ) {
    if (inFlight.current) return;
    if (!pending.current) {
      if (!operation) return;
      pending.current = {
        key: crypto.randomUUID(),
        operation,
        document: isDocument,
        uncertain: false,
        done,
      };
    }
    const request = pending.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setConflict(false);
    let committed: Receipt | null = null;
    try {
      committed = await request.operation(request.key);
      pending.current = null;
      setUncertain(false);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["sales"] }),
        qc.invalidateQueries({ queryKey: ["inventory"] }),
      ]);
      const id = committed.id;
      const saved = await qc.fetchQuery({
        queryKey: ["sales", "detail", request.document, id],
        queryFn: ({ signal }) => getDetail(id, request.document, signal),
      });
      setSelected({ id, document: request.document });
      const message = request.done(saved);
      setNotice(message || "操作已完成，数据已刷新。");
    } catch (e) {
      if (committed) {
        setSelected({ id: committed.id, document: request.document });
        request.done();
        setNotice("操作已提交，详情暂时无法刷新，请重新加载后复核。");
      } else {
        const rejected =
          e instanceof ApiError && e.status >= 400 && e.status < 500;
        // Once a response was lost, later rejection cannot prove the original write failed.
        if (!rejected) request.uncertain = true;
        if (rejected && !request.uncertain) {
          pending.current = null;
          setConflict(e.status === 409);
        }
        setUncertain(!!pending.current);
        setError(
          (e instanceof Error ? e.message : "暂时无法完成操作。") +
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
    clearQuotes();
    setError("");
    setNotice("");
    setConflict(false);
    setEditor({ kind: "ORDER", previous });
    form.reset({
      customer: previous?.customer_id ?? "",
      warehouse: previous?.warehouse_id ?? "",
      reason: previous?.reason ?? "",
      lines:
        previous?.lines?.map((line) => ({
          key: crypto.randomUUID(),
          product_id: line.product_id,
          unit_id: line.unit_id,
          label: line.product_label,
          unitLabel: line.unit_label,
          qty: line.qty,
          unit_price: line.unit_price ?? "",
          pricing_mode: line.pricing_mode ?? "AUTO",
          reviewed: line.pricing_mode === "MANUAL",
          quote: line.price_source
            ? {
                product_id: line.product_id,
                customer_id: previous.customer_id,
                unit_price: line.unit_price,
                price_source: line.price_source,
              }
            : undefined,
        })) ?? [],
    });
    if (previous?.lines?.some((line) => line.pricing_mode === "AUTO"))
      setNotice("自动价格将在再次保存时重新解析，请逐行获取并复核最新建议价。");
  }
  function newStock(
    kind: "SHIPMENT" | "RETURN",
    source: Order | StockDocument,
  ) {
    if (locked) return;
    const items =
      kind === "SHIPMENT"
        ? ((source as Order).lines ?? []).map((line) => ({
            ...line,
            limit: line.executable_qty,
          }))
        : ((source as StockDocument).lines ?? []).map((line) => ({
            ...line,
            limit: line.returnable_qty,
          }));
    const remaining = items.filter((line) => positive(line.limit));
    if (!remaining.length) {
      setError("来源单据没有剩余可处理数量。");
      return;
    }
    clearQuotes();
    setError("");
    setNotice("");
    setConflict(false);
    setEditor({ kind, source_id: source.id });
    form.reset({
      customer: source.customer_id,
      warehouse: source.warehouse_id,
      reason: "",
      lines: remaining.map((line) => ({
        key: crypto.randomUUID(),
        source_line_id: line.id,
        product_id: line.product_id,
        unit_id: line.unit_id,
        label: line.product_label,
        unitLabel: line.unit_label,
        qty: line.limit!,
        limit: line.limit!,
        unit_price: line.unit_price ?? "",
        pricing_mode: "AUTO",
        reviewed: true,
      })),
    });
  }
  function editStock(doc: StockDocument) {
    if (locked) return;
    clearQuotes();
    setError("");
    setNotice("");
    setConflict(false);
    setEditor({
      kind: doc.kind,
      source_id:
        doc.kind === "SHIPMENT" ? doc.order_id : doc.original_document_id!,
      previous: doc,
    });
    form.reset({
      customer: doc.customer_id,
      warehouse: doc.warehouse_id,
      reason: doc.reason,
      lines: (doc.lines ?? []).map((line) => ({
        key: crypto.randomUUID(),
        product_id: line.product_id,
        unit_id: line.unit_id,
        source_line_id:
          doc.kind === "SHIPMENT" ? line.order_line_id : line.shipment_line_id!,
        label: line.product_label,
        unitLabel: line.unit_label,
        qty: line.qty,
        unit_price: line.unit_price ?? "",
        pricing_mode: "AUTO",
        reviewed: true,
      })),
    });
  }
  async function save() {
    if (inFlight.current) return;
    if (
      !editor ||
      (editor.kind === "ORDER" && (!price || !has("sales.order.write"))) ||
      (editor.kind === "SHIPMENT" && !has("sales.ship")) ||
      (editor.kind === "RETURN" && !has("sales.return"))
    ) {
      setError("当前账户没有保存此单据的权限，请恢复权限后再操作。");
      return;
    }
    if (pending.current) {
      await execute();
      return;
    }
    if (!editor) return;
    const values = form.getValues();
    if (!formSchema.safeParse(values).success) {
      setError(
        "请填写单据原因，添加 1 至 200 行商品，并输入大于零的数量（最多六位小数）。",
      );
      return;
    }
    if (editor.kind === "ORDER") {
      if (
        !z.string().uuid().safeParse(values.customer).success ||
        !z.string().uuid().safeParse(values.warehouse).success
      ) {
        setError("请选择销售客户和出库仓库。");
        return;
      }
      if (
        values.lines.some(
          (line) =>
            !line.reviewed ||
            line.quoteLoading ||
            !numeric.safeParse(line.unit_price).success ||
            (line.pricing_mode === "AUTO" &&
              (!line.quote ||
                line.quote.customer_id !== values.customer ||
                line.quote.unit_price == null)),
        )
      ) {
        setError(
          "请逐行复核当前客户的建议价，或选择手动单价并明确输入；零价请输入 0。",
        );
        return;
      }
      const body: OrderInput = {
        customer_id: values.customer,
        warehouse_id: values.warehouse,
        reason: values.reason,
        lines: values.lines.map((line) => ({
          product_id: line.product_id,
          unit_id: line.unit_id,
          qty: line.qty,
          pricing_mode: line.pricing_mode,
          ...(line.pricing_mode === "MANUAL"
            ? { unit_price: line.unit_price }
            : {}),
        })),
      };
      const captured = values.lines.map((line) => ({
        product: line.product_id,
        mode: line.pricing_mode,
        price: line.unit_price,
      }));
      const previous = editor.previous as Order | undefined;
      await execute(
        (key) => saveOrder(body, key, previous),
        false,
        (saved) => {
          clearQuotes();
          setEditor(null);
          const changed =
            saved &&
            "fulfillment_status" in saved &&
            (saved.lines ?? []).some((line) =>
              captured.some(
                (item) =>
                  item.product === line.product_id &&
                  item.mode === "AUTO" &&
                  number(item.price) !== number(line.unit_price),
              ),
            );
          return changed
            ? "建议价在保存时发生变化，已采用最新服务端价格。请复核保存后的单价和金额，再确认订单。"
            : "草稿已保存，请复核服务端单价和金额，再确认订单。";
        },
      );
    } else {
      const body = {
        source_id: editor.source_id!,
        reason: values.reason,
        lines: values.lines.map((line) => ({
          source_line_id: line.source_line_id!,
          qty: line.qty,
        })),
      };
      const kind = editor.kind,
        previous = editor.previous as StockDocument | undefined;
      await execute(
        (key) => saveDocument(kind, body, key, previous),
        true,
        () => {
          setEditor(null);
          return "草稿已保存，请复核来源、数量和服务端金额，再过账。";
        },
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
    setConflict(false);
    setConfirmation({
      id: row.id,
      version: row.version,
      action,
      document: isDocument,
    });
  }
  async function confirm() {
    if (inFlight.current) return;
    if (
      confirmation?.action === "confirm" &&
      (!price || !has("sales.order.confirm"))
    ) {
      setError("当前账户没有确认销售订单的权限，请恢复权限后再操作。");
      return;
    }
    if (pending.current) {
      await execute();
      return;
    }
    if (!confirmation) return;
    if (
      ["cancel", "close", "reverse"].includes(confirmation.action) &&
      (!reason.trim() || reason.length > 2000)
    ) {
      setError("请填写操作原因（不超过 2000 字）。");
      return;
    }
    const captured = { ...confirmation },
      capturedReason = reason;
    await execute(
      (key) =>
        command(
          captured.id,
          captured.version,
          captured.action,
          capturedReason,
          key,
        ),
      captured.document,
      () => setConfirmation(null),
    );
  }
  async function refresh() {
    if (locked || inFlight.current || pending.current) return;
    setConflict(false);
    setError("");
    if (editor?.previous || confirmation) {
      clearQuotes();
      setEditor(null);
      setConfirmation(null);
    }
    await qc.invalidateQueries({ queryKey: ["sales"] });
    setNotice("数据已重新加载，请复核当前数量、价格与状态后操作。");
  }
  const conflictButton = conflict && (
    <Button variant="outline" disabled={locked} onClick={() => void refresh()}>
      重新加载单据
    </Button>
  );
  if (!read)
    return (
      <p role="alert" className="p-6">
        当前账户没有查看销售的权限。
      </p>
    );
  return (
    <div className="min-w-0 max-w-full space-y-5">
      <p className="text-sm text-muted-foreground">
        确认订单占用库存，按实发数量出库；退货沿用原成交与成本。销售金额供业务核对，资金结算尚未启用。
      </p>
      <div className="flex flex-wrap gap-2">
        {tabs
          .filter((item) => item.id !== "prices" || price)
          .map((item) => (
            <Button
              key={item.id}
              variant={tab === item.id ? "default" : "outline"}
              disabled={locked}
              onClick={() => {
                setTab(item.id);
                setPage(1);
                setStatusFilter("");
                setSelected(null);
              }}
            >
              {item.label}
            </Button>
          ))}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        {tab !== "prices" && (
          <Input
            aria-label="搜索销售记录"
            className="w-full max-w-xs"
            placeholder="单据号或客户"
            maxLength={200}
            value={q}
            disabled={locked}
            onChange={(event) => {
              setQ(event.target.value.slice(0, 200));
              setPage(1);
            }}
          />
        )}
        {has("customer.read") && (
          <select
            aria-label="筛选销售客户"
            className={selectClass}
            value={customerFilter}
            disabled={locked}
            onChange={(event) => {
              setCustomerFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部客户</option>
            {customers.data?.items.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.name}
              </option>
            ))}
          </select>
        )}
        {tab !== "prices" && (
          <select
            aria-label="筛选销售状态"
            className={selectClass}
            value={statusFilter}
            disabled={locked}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部状态</option>
            {(tab === "orders"
              ? (["DRAFT", "CONFIRMED", "CLOSED", "CANCELLED"] as const)
              : (["DRAFT", "POSTED", "REVERSED"] as const)
            ).map((status) => (
              <option key={status} value={status}>
                {names[status]}
              </option>
            ))}
          </select>
        )}
        {tab === "prices" && historyProduct && (
          <Button
            variant="outline"
            disabled={locked}
            onClick={() => {
              setHistoryProduct("");
              setPage(1);
            }}
          >
            清除商品筛选
          </Button>
        )}
        <Button
          variant="outline"
          disabled={locked}
          onClick={() => void refresh()}
        >
          刷新数据
        </Button>
        {tab === "orders" && price && has("sales.order.write") && (
          <Button disabled={locked} onClick={() => editOrder()}>
            新建销售订单
          </Button>
        )}
      </div>
      {error && !editor && !confirmation && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {notice && !editor && (
        <p role="status" className="text-sm text-primary">
          {notice}
        </p>
      )}
      {!editor && !confirmation && conflictButton}
      {uncertain && !editor && !confirmation && (
        <Button disabled={busy} onClick={() => void execute()}>
          重试原提交
        </Button>
      )}
      {active.isFetching && (
        <p role="status" className="text-sm">
          正在加载销售数据…
        </p>
      )}
      {active.error && <p role="alert">{active.error.message}</p>}
      {tab === "prices" && !price && (
        <p role="alert">当前账户没有查看成交价格的权限。</p>
      )}
      {tab === "orders" && (
        <Grid
          headers={[
            "订单号",
            "客户 / 仓库",
            "订单状态",
            "出库状态",
            ...(price ? ["订单金额"] : []),
            "操作",
          ]}
          rows={(orders.data?.items ?? []).map((row) => ({
            id: row.id,
            cells: [
              row.number,
              <div key="party">
                {row.customer_name}
                <p className="text-xs text-muted-foreground">
                  {row.warehouse_name}
                </p>
              </div>,
              names[row.status],
              names[row.fulfillment_status],
              ...(price ? [number(row.amount)] : []),
              <Button
                key="view"
                variant="outline"
                disabled={locked}
                onClick={() => setSelected({ id: row.id, document: false })}
              >
                查看
              </Button>,
            ],
          }))}
        />
      )}
      {(tab === "SHIPMENT" || tab === "RETURN") && (
        <Grid
          headers={[
            "单据号",
            "销售订单",
            "客户",
            "状态",
            "原因",
            ...(price
              ? [tab === "RETURN" ? "销售冲减金额" : "出库销售金额"]
              : []),
            "操作",
          ]}
          rows={(documents.data?.items ?? []).map((row) => ({
            id: row.id,
            cells: [
              row.number,
              row.order_number,
              row.customer_name,
              names[row.status],
              row.reason,
              ...(price ? [number(row.amount)] : []),
              <Button
                key="view"
                variant="outline"
                disabled={locked}
                onClick={() => setSelected({ id: row.id, document: true })}
              >
                查看
              </Button>,
            ],
          }))}
        />
      )}
      {tab === "prices" && price && (
        <Grid
          headers={[
            "商品",
            "客户",
            "成交单位",
            "成交单价",
            "实发 / 已退数量",
            "成交金额",
            "来源",
          ]}
          rows={(prices.data?.items ?? []).map((row) => ({
            id: row.line_id,
            cells: [
              row.product_label,
              row.customer_name,
              row.unit_label,
              number(row.unit_price),
              `${number(row.qty)} / ${number(row.returned_qty)}`,
              number(row.amount),
              <Button
                key="source"
                variant="link"
                disabled={locked}
                onClick={() =>
                  setSelected({ id: row.document_id, document: true })
                }
              >
                {row.document_number}
              </Button>,
            ],
          }))}
        />
      )}
      <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
        <Button
          variant="outline"
          disabled={locked || page === 1}
          onClick={() => setPage((value) => value - 1)}
        >
          上一页
        </Button>
        <span>
          第 {page} 页 · 共 {active.data?.total ?? 0} 条
        </span>
        <Button
          variant="outline"
          disabled={locked || page * 25 >= (active.data?.total ?? 0)}
          onClick={() => setPage((value) => value + 1)}
        >
          下一页
        </Button>
      </div>
      {selected && detail.isFetching && <p role="status">正在加载单据详情…</p>}
      {selected && detail.error && <p role="alert">{detail.error.message}</p>}
      {order && (
        <section
          aria-label="销售订单详情"
          className="min-w-0 space-y-4 rounded-xl border bg-white p-4 sm:p-5"
        >
          <div className="flex flex-wrap justify-between gap-3">
            <div className="min-w-0">
              <h2 className="break-all font-semibold">
                销售订单 · {order.number}
              </h2>
              <p className="text-sm text-muted-foreground">
                {names[order.status]} · {names[order.fulfillment_status]} ·{" "}
                {order.customer_name} · {order.warehouse_name}
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
          <p className="break-words text-sm">原因：{order.reason}</p>
          {order.action_reason && (
            <p className="break-words text-sm">
              操作原因：{order.action_reason}
            </p>
          )}
          {price && (
            <p className="text-sm">服务端订单金额：{number(order.amount)}</p>
          )}
          <Grid
            headers={[
              "商品",
              "数量 / 单位",
              "已发 / 待发 / 已退（基本单位）",
              "本单剩余占用（基本单位）",
              "当前可执行（销售单位）",
              ...(stock ? ["现有 / 仓库占用 / 可用（基本单位）"] : []),
              ...(price ? ["销售单价", "金额", "价格来源"] : []),
            ]}
            rows={(order.lines ?? []).map((line) => ({
              id: line.id,
              cells: [
                line.product_label,
                `${number(line.qty)} ${line.unit_label}`,
                `${number(line.shipped_base_qty)} / ${number(line.remaining_base_qty)} / ${number(line.returned_base_qty)}`,
                number(line.reserved_base_qty),
                `${number(line.executable_qty)} ${line.unit_label}`,
                ...(stock
                  ? [
                      `${number(line.on_hand_qty)} / ${number(line.warehouse_reserved_qty)} / ${number(line.available_qty)}`,
                    ]
                  : []),
                ...(price
                  ? [
                      number(line.unit_price),
                      number(line.amount),
                      <PriceProvenance
                        key="price-source"
                        source={line.price_source}
                        customer={order.customer_id}
                        product={line.product_id}
                        locked={locked}
                      />,
                    ]
                  : []),
              ],
            }))}
          />
          <p className="text-xs text-muted-foreground">
            待发与已退分别记录；退货不会恢复待发或占用。已关闭订单的可执行数量为零。
          </p>
          {price && (
            <div className="grid gap-2 rounded-lg bg-[#f4f6f2] p-4 text-sm sm:grid-cols-3">
              <p>有效出库销售金额：{number(order.shipment_amount)}</p>
              <p>有效退货销售冲减：{number(order.return_amount)}</p>
              <p>净销售金额：{number(order.net_sales_amount)}</p>
              {cost && (
                <>
                  <p>出库实际成本：{number(order.shipment_cost)}</p>
                  <p>退货成本回收：{number(order.return_cost)}</p>
                  <p>净成本：{number(order.net_cost)}</p>
                  <p className="font-medium">
                    已实现净毛利：{number(order.gross_margin)}
                  </p>
                </>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {order.status === "DRAFT" && price && has("sales.order.write") && (
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => editOrder(order)}
              >
                编辑订单
              </Button>
            )}
            {order.status === "DRAFT" &&
              price &&
              has("sales.order.confirm") && (
                <Button
                  disabled={locked}
                  onClick={() => ask("confirm", order, false)}
                >
                  确认订单
                </Button>
              )}
            {["DRAFT", "CONFIRMED"].includes(order.status) &&
              has("sales.order.cancel") && (
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={() => ask("cancel", order, false)}
                >
                  取消订单
                </Button>
              )}
            {order.status === "CONFIRMED" && has("sales.order.close") && (
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => ask("close", order, false)}
              >
                关闭剩余
              </Button>
            )}
            {order.status === "CONFIRMED" && has("sales.ship") && (
              <Button
                disabled={
                  locked ||
                  !(order.lines ?? []).some((line) =>
                    positive(line.executable_qty),
                  )
                }
                onClick={() => newStock("SHIPMENT", order)}
              >
                创建出库单
              </Button>
            )}
          </div>
        </section>
      )}
      {stockDocument && (
        <section
          aria-label="销售库存单据详情"
          className="min-w-0 space-y-4 rounded-xl border bg-white p-4 sm:p-5"
        >
          <div className="flex flex-wrap justify-between gap-3">
            <div className="min-w-0">
              <h2 className="break-all font-semibold">
                {stockDocument.kind === "SHIPMENT" ? "销售出库" : "销售退货"} ·{" "}
                {stockDocument.number}
              </h2>
              <p className="text-sm text-muted-foreground">
                {names[stockDocument.status]} · {stockDocument.customer_name} ·{" "}
                {stockDocument.warehouse_name}
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
          <p className="break-words text-sm">原因：{stockDocument.reason}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="link"
              disabled={locked}
              onClick={() =>
                setSelected({ id: stockDocument.order_id, document: false })
              }
            >
              来源订单 {stockDocument.order_number}
            </Button>
            {stockDocument.original_document_id && (
              <Button
                variant="link"
                disabled={locked}
                onClick={() =>
                  setSelected({
                    id: stockDocument.original_document_id!,
                    document: true,
                  })
                }
              >
                原销售出库单
              </Button>
            )}
          </div>
          <Grid
            headers={[
              "商品",
              "数量 / 单位",
              "基本数量",
              ...(stockDocument.kind === "SHIPMENT"
                ? ["已退 / 剩余可退（原单位）"]
                : []),
              ...(price
                ? [
                    "冻结销售单价",
                    stockDocument.kind === "RETURN"
                      ? "销售冲减金额"
                      : "销售金额",
                  ]
                : []),
              ...(cost
                ? [
                    stockDocument.kind === "RETURN"
                      ? "原出库成本回收"
                      : "实际出库成本",
                    "毛利影响",
                  ]
                : []),
            ]}
            rows={(stockDocument.lines ?? []).map((line) => ({
              id: line.id,
              cells: [
                line.product_label,
                `${number(line.qty)} ${line.unit_label}`,
                number(line.base_qty),
                ...(stockDocument.kind === "SHIPMENT"
                  ? [
                      `${number(line.returned_qty)} / ${number(line.returnable_qty)}`,
                    ]
                  : []),
                ...(price
                  ? [number(line.unit_price), number(line.amount)]
                  : []),
                ...(cost
                  ? [
                      number(
                        stockDocument.kind === "RETURN" &&
                          stockDocument.status === "DRAFT"
                          ? line.return_cost
                          : line.actual_cost,
                      ),
                      number(line.gross_margin),
                    ]
                  : []),
              ],
            }))}
          />
          {price && (
            <p className="text-sm">
              服务端
              {stockDocument.kind === "RETURN" ? "销售冲减金额" : "销售金额"}：
              {number(stockDocument.amount)}
              {cost && (
                <>
                  {" "}
                  · {stockDocument.kind === "RETURN" ? "成本回收" : "实际成本"}
                  ：{number(stockDocument.actual_cost)} · 毛利影响：
                  {number(stockDocument.gross_margin)}
                </>
              )}
            </p>
          )}
          {stockDocument.kind === "RETURN" && (
            <p className="text-xs text-muted-foreground">
              销售金额与成本分别按原出库分配，最后退完时各自结清尾差。退货不恢复订单占用，不代表已退款。
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {stockDocument.status === "DRAFT" &&
              has(
                stockDocument.kind === "SHIPMENT"
                  ? "sales.ship"
                  : "sales.return",
              ) && (
                <>
                  <Button
                    variant="outline"
                    disabled={locked}
                    onClick={() => editStock(stockDocument)}
                  >
                    编辑草稿
                  </Button>
                  <Button
                    disabled={locked}
                    onClick={() => ask("post", stockDocument, true)}
                  >
                    过账
                  </Button>
                </>
              )}
            {stockDocument.status === "POSTED" &&
              has("sales.reverse") &&
              has(
                stockDocument.kind === "SHIPMENT"
                  ? "sales.ship"
                  : "sales.return",
              ) && (
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={() => ask("reverse", stockDocument, true)}
                >
                  冲销
                </Button>
              )}
            {stockDocument.kind === "SHIPMENT" &&
              stockDocument.status === "POSTED" &&
              has("sales.return") && (
                <Button
                  disabled={
                    locked ||
                    !(stockDocument.lines ?? []).some((line) =>
                      positive(line.returnable_qty),
                    )
                  }
                  onClick={() => newStock("RETURN", stockDocument)}
                >
                  创建退货单
                </Button>
              )}
            {stock && (
              <Link
                aria-disabled={locked}
                tabIndex={locked ? -1 : undefined}
                onClick={(event) => {
                  if (locked) event.preventDefault();
                }}
                className="px-3 py-2 text-sm text-primary"
                href={`/inventory?tab=movements&document=${encodeURIComponent(stockDocument.id)}`}
              >
                查看库存流水
              </Link>
            )}
          </div>
        </section>
      )}
      <Dialog.Root
        open={!!editor}
        onOpenChange={(open) => {
          if (!open && !locked && !pending.current && !inFlight.current) {
            clearQuotes();
            setEditor(null);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/30" />
          <Dialog.Popup className="fixed left-1/2 top-[5vh] z-50 max-h-[90vh] w-[min(94vw,960px)] min-w-0 -translate-x-1/2 overflow-y-auto rounded-xl bg-white p-4 shadow-xl sm:p-6">
            <Dialog.Title className="text-lg font-semibold">
              {editor?.kind === "ORDER"
                ? "销售订单草稿"
                : editor?.kind === "SHIPMENT"
                  ? "销售出库草稿"
                  : "销售退货草稿"}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              {editor?.kind === "ORDER"
                ? "选择客户、仓库和销售单位，逐行复核价格。保存草稿后核对服务端金额，确认订单才会占用库存。"
                : "按原销售单位填写数量；客户、仓库、单位与销售价沿用来源。保存草稿不改变库存，复核后再过账。"}
            </Dialog.Description>
            {error && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {error}
              </p>
            )}
            {notice && (
              <p role="status" className="mt-3 text-sm text-primary">
                {notice}
              </p>
            )}
            {conflict && <div className="mt-3">{conflictButton}</div>}
            <fieldset
              disabled={locked || (editor?.kind === "ORDER" && !price)}
              className="mt-4 min-w-0 space-y-4"
            >
              {editor?.kind === "ORDER" && (
                <>
                  <Input
                    aria-label="搜索销售关联资料"
                    placeholder="搜索客户或仓库（显示前 100 条）"
                    maxLength={200}
                    value={referenceSearch}
                    onChange={(event) =>
                      setReferenceSearch(event.target.value.slice(0, 200))
                    }
                  />
                  {customers.error && (
                    <p role="alert">
                      客户资料加载失败：{customers.error.message}
                    </p>
                  )}
                  {warehouses.error && (
                    <p role="alert">
                      仓库资料加载失败：{warehouses.error.message}
                    </p>
                  )}
                  <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                    <label className="grid min-w-0 gap-1 text-sm">
                      销售客户
                      <select
                        aria-label="销售客户"
                        className={selectClass}
                        value={fields.customer ?? ""}
                        onChange={(event) => {
                          clearQuotes();
                          form.setValue("customer", event.target.value);
                          form.setValue(
                            "lines",
                            form.getValues("lines").map((line) => ({
                              ...line,
                              pricing_mode: "AUTO",
                              unit_price: "",
                              quote: undefined,
                              reviewed: false,
                              quoteLoading: false,
                              quoteError: "",
                            })),
                          );
                          setError("");
                          setNotice(
                            "客户已更换，所有商品价格均需重新复核；请获取建议价或明确填写手动单价。",
                          );
                        }}
                      >
                        <option value="">请选择客户</option>
                        {customers.data?.items.map((customer) => (
                          <option key={customer.id} value={customer.id}>
                            {customer.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="grid min-w-0 gap-1 text-sm">
                      出库仓库
                      <select
                        aria-label="出库仓库"
                        className={selectClass}
                        {...form.register("warehouse")}
                      >
                        <option value="">请选择仓库</option>
                        {warehouses.data?.items.map((warehouse) => (
                          <option key={warehouse.id} value={warehouse.id}>
                            {warehouse.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </>
              )}
              <label className="grid gap-1 text-sm">
                销售单据原因
                <Input maxLength={2000} {...form.register("reason")} />
              </label>
              {editor?.kind === "ORDER" && (
                <Button
                  variant="outline"
                  disabled={!fields.customer || lines.length >= 200}
                  onClick={() => {
                    pickerGeneration.current += 1;
                    setPicker({
                      generation: pickerGeneration.current,
                      customer: form.getValues("customer"),
                    });
                  }}
                >
                  添加销售商品
                </Button>
              )}
              {picker && (
                <ProductPicker
                  permissions={permissions}
                  onSelect={({ product, snapshot, unitLabel }) => {
                    const customer = picker.customer,
                      captured = picker.generation;
                    if (
                      inFlight.current ||
                      pending.current ||
                      pickerGeneration.current !== captured ||
                      form.getValues("customer") !== customer
                    )
                      return;
                    const current = form.getValues("lines");
                    if (current.length >= 200) {
                      setError("每张订单最多 200 行商品。");
                      return;
                    }
                    if (
                      current.some((line) => line.product_id === product.id)
                    ) {
                      setError("同一商品只能添加一行。");
                      return;
                    }
                    const line: Line = {
                      key: crypto.randomUUID(),
                      product_id: product.id,
                      unit_id: snapshot.unit_id,
                      label: `${product.sku} · ${product.name}`,
                      unitLabel,
                      qty: snapshot.qty,
                      unit_price: "",
                      pricing_mode: "AUTO",
                      reviewed: false,
                    };
                    form.setValue("lines", [...current, line]);
                    pickerGeneration.current += 1;
                    setPicker(null);
                    setError("");
                    void loadQuote(line.key);
                  }}
                />
              )}
              {lines.map((line, index) => (
                <div
                  key={line.key}
                  className="min-w-0 rounded-lg border p-3 sm:p-4"
                >
                  <div className="mb-3 flex flex-wrap justify-between gap-2">
                    <p className="min-w-0 break-words text-sm font-medium">
                      {line.label} · {line.unitLabel}
                      {line.limit ? ` · 可处理 ${number(line.limit)}` : ""}
                    </p>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        quoteRequests.current.get(line.key!)?.abort();
                        quoteRequests.current.delete(line.key!);
                        form.setValue(
                          "lines",
                          form
                            .getValues("lines")
                            .filter((item) => item.key !== line.key),
                        );
                      }}
                    >
                      移除
                    </Button>
                  </div>
                  <div className="grid min-w-0 gap-3 sm:grid-cols-3">
                    <label className="grid min-w-0 gap-1 text-sm">
                      销售数量
                      <Input
                        inputMode="decimal"
                        {...form.register(`lines.${index}.qty`)}
                      />
                    </label>
                    {editor?.kind === "ORDER" && (
                      <label className="grid min-w-0 gap-1 text-sm">
                        定价方式
                        <select
                          aria-label="定价方式"
                          className={selectClass}
                          value={line.pricing_mode ?? "AUTO"}
                          onChange={(event) => {
                            const current = form.getValues(`lines.${index}`);
                            quoteRequests.current.get(current.key)?.abort();
                            quoteRequests.current.delete(current.key);
                            const mode = event.target.value as
                              | "AUTO"
                              | "MANUAL";
                            form.setValue(`lines.${index}`, {
                              ...current,
                              pricing_mode: mode,
                              unit_price: "",
                              quote: undefined,
                              reviewed: mode === "MANUAL",
                              quoteLoading: false,
                              quoteError: "",
                            });
                            if (mode === "AUTO") void loadQuote(current.key);
                          }}
                        >
                          <option value="AUTO">自动建议</option>
                          <option value="MANUAL">手动单价</option>
                        </select>
                      </label>
                    )}
                    {price && (
                      <label className="grid min-w-0 gap-1 text-sm">
                        销售单位单价
                        <Input
                          inputMode="decimal"
                          readOnly={
                            editor?.kind !== "ORDER" ||
                            line.pricing_mode !== "MANUAL"
                          }
                          placeholder={
                            line.pricing_mode === "MANUAL"
                              ? "明确填写，零价输入 0"
                              : "等待建议价"
                          }
                          {...form.register(`lines.${index}.unit_price`)}
                        />
                      </label>
                    )}
                  </div>
                  {editor?.kind === "ORDER" && price && (
                    <div className="mt-3 space-y-2">
                      {line.quoteLoading && (
                        <p role="status" className="text-xs">
                          正在获取当前客户的建议价…
                        </p>
                      )}
                      {line.quoteError && (
                        <p role="alert" className="text-xs text-destructive">
                          {line.quoteError}
                        </p>
                      )}
                      {line.pricing_mode === "MANUAL" ? (
                        <p className="text-xs text-muted-foreground">
                          手动单价将随订单保存；零价也需明确输入。
                        </p>
                      ) : (
                        <>
                          <PriceProvenance
                            source={line.quote?.price_source}
                            customer={fields.customer ?? ""}
                            product={line.product_id ?? ""}
                            locked={locked}
                          />
                          <Button
                            variant="link"
                            disabled={line.quoteLoading}
                            onClick={() => void loadQuote(line.key!)}
                          >
                            重新获取报价
                          </Button>
                          {!line.reviewed && !line.quoteLoading && (
                            <p className="text-xs text-muted-foreground">
                              请获取并复核当前客户的建议价。
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </fieldset>
            <div className="mt-5 flex flex-wrap justify-end gap-3">
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => {
                  clearQuotes();
                  setEditor(null);
                }}
              >
                取消
              </Button>
              <Button
                disabled={busy}
                onClick={() => void form.handleSubmit(save)()}
              >
                {busy ? "正在保存…" : uncertain ? "重试原提交" : "保存销售草稿"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open && !locked && !pending.current && !inFlight.current)
            setConfirmation(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/30" />
          <Dialog.Popup className="fixed left-1/2 top-[15vh] z-50 max-h-[75vh] w-[min(92vw,520px)] -translate-x-1/2 overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
            <Dialog.Title className="text-lg font-semibold">
              复核{confirmation ? actionNames[confirmation.action] : "操作"}
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm text-muted-foreground">
              {confirmation?.action === "confirm"
                ? "将按当前订单全部数量占用库存；任一商品缺货会拒绝整单确认。确认不改变现有库存，也不会重新定价，请先核对保存后的单价与金额。"
                : confirmation?.action === "post"
                  ? "将按本单数量改变库存。出库只消费本订单占用，退货按原销售来源回收库存；请核对来源、数量和仓库。"
                  : confirmation?.action === "reverse"
                    ? "仅严格末笔整单可以冲销，存在后续库存流转或退货依赖时会拒绝。出库冲销需订单仍已确认；退货冲销不恢复订单占用。"
                    : confirmation?.action === "close"
                      ? "将释放未发数量的剩余占用，并关闭订单。已有出库与退货事实保留，关闭后不可直接重开。"
                      : "仅没有有效出库的订单可以取消；取消会释放本订单占用，不能直接重开。"}
            </Dialog.Description>
            {confirmation &&
              ["cancel", "close", "reverse"].includes(confirmation.action) && (
                <label className="mt-4 grid gap-1 text-sm">
                  销售操作原因
                  <Input
                    disabled={locked}
                    maxLength={2000}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
              )}
            {error && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {error}
              </p>
            )}
            {conflict && <div className="mt-3">{conflictButton}</div>}
            <div className="mt-5 flex flex-wrap justify-end gap-3">
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
