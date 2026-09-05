"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { Dialog } from "@base-ui/react/dialog";
import { z } from "zod";
import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  activate,
  bindLegacy,
  opening,
  previewCash,
  readCash,
  readSource,
  recordCash,
  reverse,
  type CashInput,
  type CashKind,
  type CashPreview,
  type Legacy,
  type Receipt,
  type Side,
  type Source,
} from "./client";
import { amountText, FundsTable, positiveAmount } from "./presentation";
import { useFundsSubmission } from "./use-submission";

type Tab = "sources" | "cash" | "parties" | "legacy";
type Selection = { kind: "source" | "cash"; id: string };
type Allocation = {
  source_id: string;
  number: string;
  amount: string;
  limit: string;
};
type Editor =
  | { kind: "ACTIVATE" | "OPENING" | "ADJUSTMENT" }
  | { kind: "CASH"; cashKind: CashKind }
  | { kind: "BIND"; document: Legacy }
  | { kind: "REVERSE"; selection: Selection; number: string };
type Fields = {
  party_id: string;
  party_label: string;
  amount: string;
  business_date: string;
  method: CashInput["method"];
  external_reference: string;
  reason: string;
  refund_balance_confirmed: boolean;
  opening_source_id: string;
  opening_source_label: string;
  allocations: Allocation[];
};
const selectClass =
  "h-10 min-w-0 max-w-full rounded-md border bg-white px-3 text-sm";
const money = z.string().regex(/^-?\d+(?:\.\d{1,4})?$/);
const explanation = z.string().trim().min(1).max(2000);
const names: Record<string, string> = {
  SHIPMENT: "销售出库",
  RECEIPT: "采购入库",
  OPENING: "期初往来",
  LEGACY: "历史单据",
  ADJUSTMENT: "往来调整",
  OPEN: "待结算",
  REFUND: "待退款",
  SETTLED: "已结清",
  POSTED: "已记录",
  REVERSED: "已冲销",
  CASH: "现金",
  BANK_TRANSFER: "银行转账",
  OTHER: "其他",
};
function defaults(businessDate = ""): Fields {
  return {
    party_id: "",
    party_label: "",
    amount: "",
    business_date: businessDate,
    method: "CASH",
    external_reference: "",
    reason: "",
    refund_balance_confirmed: false,
    opening_source_id: "",
    opening_source_label: "",
    allocations: [],
  };
}
function cashName(side: Side, kind: CashKind) {
  return kind === "REFUND"
    ? side === "AR"
      ? "客户退款"
      : "供应商退款"
    : side === "AR"
      ? "收款"
      : "付款";
}
function actionPermission(side: Side, kind: CashKind) {
  return kind === "REFUND"
    ? side === "AR"
      ? "funds.customer_refund"
      : "funds.supplier_refund"
    : side === "AR"
      ? "funds.receive"
      : "funds.pay";
}

export function FundsWorkspace({ permissions }: { permissions: string[] }) {
  const params = useSearchParams();
  const qc = useQueryClient();
  const submission = useFundsSubmission();
  const { locked } = submission;
  const has = (permission: string) => permissions.includes(permission);
  const canAR = has("funds.ar.read"),
    canAP = has("funds.ap.read");
  const [side, setSide] = useState<Side>(
    (params.get("side") === "AP" && canAP) || (!canAR && canAP) ? "AP" : "AR",
  );
  const read = side === "AR" ? canAR : canAP;
  const [requestedTab, setTab] = useState<Tab>(
    ["sources", "cash", "parties", "legacy"].includes(params.get("tab") ?? "")
      ? (params.get("tab") as Tab)
      : "sources",
  );
  const tab =
    requestedTab === "legacy" && !has("funds.opening")
      ? "sources"
      : requestedTab;
  const [page, setPage] = useState(1);
  const [q, setQ] = useState((params.get("q") ?? "").slice(0, 200));
  const [partyFilter, setPartyFilter] = useState(params.get("party") ?? "");
  const [status, setStatus] = useState<Source["status"] | "">("");
  const [cashFilter, setCashFilter] = useState<CashKind | "">("");
  const [partySearch, setPartySearch] = useState("");
  const [candidatePage, setCandidatePage] = useState(1);
  const [selected, setSelected] = useState<Selection | null>(
    params.get("source")
      ? { kind: "source", id: params.get("source")! }
      : params.get("cash")
        ? { kind: "cash", id: params.get("cash")! }
        : null,
  );
  const [editor, setEditor] = useState<Editor | null>(null);
  const [cashPreview, setCashPreview] = useState<{
    body: CashInput;
    value: CashPreview;
  } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewRequest = useRef<AbortController | null>(null);
  const previewGeneration = useRef(0);
  const form = useForm<Fields>({ defaultValues: defaults() });
  const fields = useWatch({ control: form.control });
  const allocations = (fields.allocations ?? []) as Allocation[];
  const partyLabel = side === "AR" ? "客户" : "供应商";

  useEffect(() => {
    const unsubscribe = form.subscribe({
      formState: { values: true },
      callback: () => {
        previewGeneration.current += 1;
        previewRequest.current?.abort();
        setPreviewLoading(false);
        setCashPreview(null);
      },
    });
    return () => {
      unsubscribe();
      previewRequest.current?.abort();
    };
  }, [form]);
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("side", side);
    url.searchParams.set("tab", tab);
    for (const [key, value] of [
      ["q", q],
      ["party", partyFilter],
      ["source", selected?.kind === "source" ? selected.id : ""],
      ["cash", selected?.kind === "cash" ? selected.id : ""],
    ]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.replaceState(
      window.history.state,
      "",
      url.pathname + url.search,
    );
  }, [side, tab, q, partyFilter, selected]);

  const settings = useQuery({
    queryKey: ["funds", "settings"],
    enabled: canAR || canAP || has("funds.activate"),
    queryFn: async ({ signal }) =>
      unwrap(await api.GET("/api/v1/funds/settings", { signal })),
  });
  const summary = useQuery({
    queryKey: ["funds", side, "summary", partyFilter],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/summary", {
          signal,
          params: { query: { side, party_id: partyFilter || undefined } },
        }),
      ),
  });
  const parties = useQuery({
    queryKey: ["funds", side, "party-options", partySearch],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/parties", {
          signal,
          params: { query: { side, q: partySearch, page_size: 100 } },
        }),
      ),
  });
  const partyList = useQuery({
    queryKey: ["funds", side, "parties", page, q],
    enabled: read && tab === "parties",
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/parties", {
          signal,
          params: { query: { side, page, page_size: 25, q } },
        }),
      ),
  });
  const sources = useQuery({
    queryKey: ["funds", side, "sources", page, q, partyFilter, status],
    enabled: read && tab === "sources",
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/sources", {
          signal,
          params: {
            query: {
              side,
              page,
              page_size: 25,
              q,
              party_id: partyFilter || undefined,
              status: status || undefined,
            },
          },
        }),
      ),
  });
  const cash = useQuery({
    queryKey: ["funds", side, "cash", page, partyFilter, cashFilter],
    enabled: read && tab === "cash",
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/cash", {
          signal,
          params: {
            query: {
              side,
              page,
              page_size: 25,
              party_id: partyFilter || undefined,
              kind: cashFilter || undefined,
            },
          },
        }),
      ),
  });
  const legacy = useQuery({
    queryKey: ["funds", side, "legacy", page, partyFilter],
    enabled: read && tab === "legacy" && has("funds.opening"),
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/legacy-documents", {
          signal,
          params: {
            query: {
              side,
              page,
              page_size: 25,
              party_id: partyFilter || undefined,
            },
          },
        }),
      ),
  });
  const sourceDetail = useQuery({
    queryKey: ["funds", side, "source", selected?.id],
    enabled: read && selected?.kind === "source",
    queryFn: ({ signal }) => readSource(selected!.id, signal),
  });
  const cashDetail = useQuery({
    queryKey: ["funds", side, "cash-detail", selected?.id],
    enabled: read && selected?.kind === "cash",
    queryFn: ({ signal }) => readCash(selected!.id, signal),
  });
  const candidateStatus =
    editor?.kind === "CASH"
      ? editor.cashKind === "REFUND"
        ? "REFUND"
        : "OPEN"
      : undefined;
  const candidates = useQuery({
    queryKey: [
      "funds",
      side,
      "candidates",
      fields.party_id,
      candidateStatus,
      candidatePage,
    ],
    enabled:
      read &&
      !!fields.party_id &&
      (editor?.kind === "CASH" || editor?.kind === "BIND"),
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/funds/sources", {
          signal,
          params: {
            query: {
              side,
              party_id: fields.party_id,
              status: candidateStatus,
              page: candidatePage,
              page_size: 25,
            },
          },
        }),
      ),
  });

  const source =
    selected?.kind === "source" &&
    sourceDetail.data?.id === selected.id &&
    sourceDetail.data.side === side
      ? sourceDetail.data
      : null;
  const cashRecord =
    selected?.kind === "cash" &&
    cashDetail.data?.id === selected.id &&
    cashDetail.data.side === side
      ? cashDetail.data
      : null;
  const active =
    tab === "sources"
      ? sources
      : tab === "cash"
        ? cash
        : tab === "parties"
          ? partyList
          : legacy;
  const canCash = (kind: CashKind) => read && has(actionPermission(side, kind));
  const editorAllowed =
    read &&
    (editor?.kind === "CASH"
      ? canCash(editor.cashKind)
      : editor?.kind === "OPENING" || editor?.kind === "BIND"
        ? has("funds.opening")
        : editor?.kind === "ADJUSTMENT"
          ? has("funds.adjust")
          : editor?.kind === "REVERSE"
            ? has("funds.reverse")
            : has("funds.activate"));
  const canSubmit =
    editor?.kind === "ACTIVATE" ? has("funds.activate") : editorAllowed;
  const canSeeEditor =
    editor?.kind === "ACTIVATE" ? has("funds.activate") : read;
  const previewContext = `${canSubmit}:${submission.conflict}`;
  const [previousPreviewContext, setPreviousPreviewContext] =
    useState(previewContext);
  // Discard a previously reviewed amount before rendering a changed authorization or conflict.
  if (previousPreviewContext !== previewContext) {
    setPreviousPreviewContext(previewContext);
    setPreviewLoading(false);
    setCashPreview(null);
  }
  useEffect(() => {
    previewGeneration.current += 1;
    previewRequest.current?.abort();
  }, [previewContext]);

  function changeSide(next: Side) {
    if (submission.isLocked()) return;
    setSide(next);
    setSelected(null);
    setEditor(null);
    setPage(1);
    setPartyFilter("");
    setPartySearch("");
    setQ("");
  }
  function openEditor(next: Editor, initial: Partial<Fields> = {}) {
    if (submission.isLocked()) return;
    previewRequest.current?.abort();
    previewGeneration.current += 1;
    setCashPreview(null);
    setPreviewLoading(false);
    setCandidatePage(1);
    setPartySearch("");
    form.reset({
      ...defaults(settings.data?.business_today),
      party_id: partyFilter,
      ...initial,
    });
    submission.setError("");
    submission.setNotice("");
    submission.clearConflict();
    setEditor(next);
  }
  function openCash(kind: CashKind, item?: Source) {
    openEditor(
      { kind: "CASH", cashKind: kind },
      item
        ? {
            party_id: item.party_id,
            party_label: item.party_name,
            allocations: [
              {
                source_id: item.id,
                number: item.number,
                amount:
                  kind === "REFUND"
                    ? item.refund_amount
                    : item.settlement_amount,
                limit:
                  kind === "REFUND"
                    ? item.refund_amount
                    : item.settlement_amount,
              },
            ],
          }
        : {},
    );
  }
  function closeEditor() {
    if (submission.isLocked()) return;
    previewRequest.current?.abort();
    previewGeneration.current += 1;
    setEditor(null);
    setCashPreview(null);
    setPreviewLoading(false);
  }
  function commercialLink(item: {
    side: Side;
    source_document_id?: string | null;
  }) {
    const allowed =
      item.side === "AR" ? has("sales.read") : has("purchase.read");
    return allowed && item.source_document_id ? (
      <Link
        className="text-primary underline"
        href={`/${item.side === "AR" ? "sales" : "purchase"}?document=${encodeURIComponent(item.source_document_id)}`}
      >
        查看原业务单据
      </Link>
    ) : null;
  }
  async function afterReceipt(
    receipt: Receipt,
    kind: Selection["kind"] | null,
  ) {
    setEditor(null);
    setCashPreview(null);
    if (kind) setSelected({ id: receipt.id, kind });
    await qc.invalidateQueries({ queryKey: ["funds"] });
    if (kind === "cash")
      await qc.fetchQuery({
        queryKey: ["funds", side, "cash-detail", receipt.id],
        queryFn: ({ signal }) => readCash(receipt.id, signal),
      });
    if (kind === "source")
      await qc.fetchQuery({
        queryKey: ["funds", side, "source", receipt.id],
        queryFn: ({ signal }) => readSource(receipt.id, signal),
      });
  }
  function cashBody(values: Fields): CashInput | null {
    if (
      editor?.kind !== "CASH" ||
      !values.party_id ||
      !explanation.safeParse(values.reason).success ||
      !/^\d{4}-\d{2}-\d{2}$/.test(values.business_date) ||
      values.external_reference.length > 200 ||
      !values.allocations.length ||
      values.allocations.length > 100 ||
      values.allocations.some((line) => !positiveAmount(line.amount))
    ) {
      submission.setError(
        "请选择往来对象和核销来源，填写有效日期、正数金额及操作说明；金额最多保留四位小数。",
      );
      return null;
    }
    return {
      side,
      party_id: values.party_id,
      kind: editor.cashKind,
      business_date: values.business_date,
      method: values.method,
      external_reference: values.external_reference,
      reason: values.reason.trim(),
      allocations: values.allocations.map(({ source_id, amount }) => ({
        source_id,
        amount,
      })),
    };
  }
  async function calculatePreview(values: Fields) {
    if (submission.isLocked() || !canSubmit) return;
    const body = cashBody(values);
    if (!body) return;
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    const generation = ++previewGeneration.current;
    setPreviewLoading(true);
    setCashPreview(null);
    submission.setError("");
    try {
      const value = await previewCash(body, controller.signal);
      if (
        !controller.signal.aborted &&
        previewGeneration.current === generation
      ) {
        if (
          value.side !== body.side ||
          value.party_id !== body.party_id ||
          value.kind !== body.kind
        )
          throw new Error("金额预览的往来对象不一致，请重新核对。");
        setCashPreview({ body, value });
      }
    } catch (error) {
      if (
        !controller.signal.aborted &&
        previewGeneration.current === generation
      )
        submission.setError(
          error instanceof Error ? error.message : "暂时无法核对金额，请重试。",
        );
    } finally {
      if (previewRequest.current === controller) setPreviewLoading(false);
    }
  }
  async function submit(values: Fields) {
    if (submission.isLocked() || !editor) return;
    if (!canSubmit) {
      submission.setError("当前没有执行这项资金操作的权限。");
      return;
    }
    if (!explanation.safeParse(values.reason).success) {
      submission.setError("请填写操作说明（不超过2000字）。");
      return;
    }
    const reason = values.reason.trim();
    if (editor.kind === "ACTIVATE") {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(values.business_date)) {
        submission.setError("请选择资金启用日期。");
        return;
      }
      const body = { business_date: values.business_date, reason };
      await submission.submit(
        (key) => activate(body, key),
        (receipt) => afterReceipt(receipt, null),
      );
    } else if (editor.kind === "CASH") {
      const body = cashBody(values);
      if (!body) return;
      if (
        !cashPreview ||
        JSON.stringify(body) !== JSON.stringify(cashPreview.body)
      ) {
        setCashPreview(null);
        submission.setError("请先预览并复核本次收付款金额。");
        return;
      }
      const captured = cashPreview.body;
      await submission.submit(
        (key) => recordCash(captured, key),
        (receipt) => afterReceipt(receipt, "cash"),
      );
    } else if (editor.kind === "REVERSE") {
      const selection = editor.selection;
      await submission.submit(
        (key) => reverse(selection.id, selection.kind === "cash", reason, key),
        (receipt) =>
          afterReceipt({ ...receipt, id: selection.id }, selection.kind),
      );
    } else if (editor.kind === "BIND") {
      if (!money.safeParse(values.amount).success) {
        submission.setError("请填写明确的历史未结金额，最多四位小数。");
        return;
      }
      if (values.amount.startsWith("-") && !values.refund_balance_confirmed) {
        submission.setError(
          "负数历史余额表示已结算但尚待退款，请明确确认该退款余额。",
        );
        return;
      }
      const body = {
        side,
        source_document_id: editor.document.id,
        opening_source_id: values.opening_source_id || null,
        amount: values.amount,
        refund_balance_confirmed: values.refund_balance_confirmed,
        reason,
      };
      await submission.submit(
        (key) => bindLegacy(body, key),
        (receipt) => afterReceipt(receipt, "source"),
      );
    } else {
      if (
        !values.party_id ||
        !money.safeParse(values.amount).success ||
        /^-?0+(?:\.0*)?$/.test(values.amount)
      ) {
        submission.setError("请选择往来对象并填写非零金额，最多四位小数。");
        return;
      }
      if (values.amount.startsWith("-") && !values.refund_balance_confirmed) {
        submission.setError("负数金额表示待退款，请明确确认该退款余额。");
        return;
      }
      const body = {
        side,
        party_id: values.party_id,
        amount: values.amount,
        reason,
        refund_balance_confirmed: values.refund_balance_confirmed,
      };
      const adjustment = editor.kind === "ADJUSTMENT";
      await submission.submit(
        (key) => opening(body, adjustment, key),
        (receipt) => afterReceipt(receipt, "source"),
      );
    }
  }
  async function refresh() {
    if (submission.isLocked()) return;
    previewGeneration.current += 1;
    previewRequest.current?.abort();
    setCashPreview(null);
    await qc.invalidateQueries({ queryKey: ["funds"] });
    submission.clearConflict();
  }
  const feedback = (
    <>
      {submission.error && (
        <p
          role="alert"
          className="my-3 rounded-lg bg-red-50 p-3 text-sm text-red-800"
        >
          {submission.error}
        </p>
      )}
      {submission.notice && (
        <p role="status" className="my-3 rounded-lg bg-green-50 p-3 text-sm">
          {submission.notice}
        </p>
      )}
      {submission.uncertain && (
        <div className="my-3 space-y-2 text-sm">
          <p>请保留当前页面，使用原提交核对结果；不要重新登记这笔资金。</p>
          <Button
            disabled={submission.busy}
            onClick={() => void submission.retry()}
          >
            重试原提交
          </Button>
        </div>
      )}
      {submission.conflict && (
        <Button
          variant="outline"
          disabled={locked}
          onClick={() => void refresh()}
        >
          刷新余额并重新复核
        </Button>
      )}
    </>
  );
  const dialogTitle = !editor
    ? "资金操作"
    : editor.kind === "ACTIVATE"
      ? "启用资金管理"
      : editor.kind === "OPENING"
        ? "录入期初往来"
        : editor.kind === "ADJUSTMENT"
          ? "登记往来调整"
          : editor.kind === "BIND"
            ? "绑定历史期初"
            : editor.kind === "REVERSE"
              ? "冲销资金记录"
              : editor.kind === "CASH"
                ? `登记${cashName(side, editor.cashKind)}`
                : "资金操作";
  const confirmationLabel =
    editor?.kind === "CASH"
      ? `确认${cashName(side, editor.cashKind)}`
      : editor?.kind === "ACTIVATE"
        ? "确认启用"
        : editor?.kind === "REVERSE"
          ? "确认冲销"
          : editor?.kind === "BIND"
            ? "确认绑定"
            : "保存资金记录";
  const canView = canAR || canAP || has("funds.activate");

  return (
    <section
      className="mt-7 min-w-0 max-w-full space-y-5"
      aria-label="资金工作台"
    >
      {!canView && (
        <p role="alert" className="text-sm">
          当前没有查看资金的权限，请联系管理员。
        </p>
      )}
      {!editor && feedback}
      {canView && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {canAR && (
              <Button
                variant={side === "AR" ? "default" : "outline"}
                disabled={locked}
                onClick={() => changeSide("AR")}
              >
                应收与收款
              </Button>
            )}
            {canAP && (
              <Button
                variant={side === "AP" ? "default" : "outline"}
                disabled={locked}
                onClick={() => changeSide("AP")}
              >
                应付与付款
              </Button>
            )}
            <Button
              variant="outline"
              disabled={locked}
              onClick={() => void refresh()}
            >
              刷新资金数据
            </Button>
          </div>
          {settings.isPending ? (
            <p role="status">正在读取资金启用状态…</p>
          ) : settings.error ? (
            <p role="alert">{settings.error.message}</p>
          ) : settings.data?.enabled ? (
            <p className="text-sm text-muted-foreground">
              资金管理已启用 · 业务切换日期 {settings.data.business_date} ·
              人民币 CNY
            </p>
          ) : (
            <div className="rounded-xl border bg-white p-5 text-sm">
              <p>
                资金管理尚未启用。历史单据不会自动作为未收付款；启用后请按真实余额录入期初。
              </p>
              {has("funds.activate") && (
                <Button
                  className="mt-3"
                  disabled={locked}
                  onClick={() => openEditor({ kind: "ACTIVATE" })}
                >
                  启用资金管理
                </Button>
              )}
            </div>
          )}
          {read && (
            <>
              {summary.error ? (
                <p role="alert">{summary.error.message}</p>
              ) : summary.isPending ? (
                <p role="status">正在读取资金余额…</p>
              ) : (
                summary.data && (
                  <div className="grid min-w-0 gap-3 sm:grid-cols-3">
                    {[
                      {
                        label: side === "AR" ? "尚待收款" : "尚待付款",
                        value: summary.data.settlement_amount,
                      },
                      {
                        label:
                          side === "AR" ? "尚待退给客户" : "尚待供应商退款",
                        value: summary.data.refund_amount,
                      },
                      { label: "往来净额", value: summary.data.balance },
                    ].map(({ label, value }) => (
                      <div
                        key={label}
                        className="min-w-0 rounded-xl border bg-white p-4"
                      >
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="mt-2 break-all text-xl tabular-nums">
                          {amountText(value)}
                        </p>
                      </div>
                    ))}
                  </div>
                )
              )}
              <div className="flex flex-wrap gap-2">
                {canCash("SETTLEMENT") && (
                  <Button
                    disabled={locked || !settings.data?.enabled}
                    onClick={() => openCash("SETTLEMENT")}
                  >
                    登记{cashName(side, "SETTLEMENT")}
                  </Button>
                )}
                {canCash("REFUND") && (
                  <Button
                    variant="outline"
                    disabled={locked || !settings.data?.enabled}
                    onClick={() => openCash("REFUND")}
                  >
                    登记{cashName(side, "REFUND")}
                  </Button>
                )}
                {has("funds.opening") && (
                  <Button
                    variant="outline"
                    disabled={locked || !settings.data?.enabled}
                    onClick={() => openEditor({ kind: "OPENING" })}
                  >
                    录入期初往来
                  </Button>
                )}
                {has("funds.adjust") && (
                  <Button
                    variant="outline"
                    disabled={locked || !settings.data?.enabled}
                    onClick={() => openEditor({ kind: "ADJUSTMENT" })}
                  >
                    登记往来调整
                  </Button>
                )}
              </div>
              <div
                className="flex flex-wrap gap-2"
                role="tablist"
                aria-label="资金记录分类"
              >
                {(
                  [
                    {
                      id: "sources",
                      label: side === "AR" ? "应收来源" : "应付来源",
                    },
                    {
                      id: "cash",
                      label: side === "AR" ? "收款与退款" : "付款与退款",
                    },
                    { id: "parties", label: `${partyLabel}往来` },
                    ...(has("funds.opening")
                      ? [{ id: "legacy", label: "历史单据绑定" }]
                      : []),
                  ] as { id: Tab; label: string }[]
                ).map((item) => (
                  <Button
                    key={item.id}
                    role="tab"
                    aria-selected={tab === item.id}
                    variant={tab === item.id ? "default" : "outline"}
                    disabled={locked}
                    onClick={() => {
                      if (!submission.isLocked()) {
                        setTab(item.id);
                        setPage(1);
                      }
                    }}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
              <div className="flex min-w-0 flex-wrap gap-2">
                {(tab === "sources" || tab === "parties") && (
                  <Input
                    className="w-full sm:w-60"
                    aria-label="搜索资金记录"
                    placeholder="单据号、名称或编码"
                    maxLength={200}
                    disabled={locked}
                    value={q}
                    onChange={(event) => {
                      setQ(event.target.value);
                      setPage(1);
                    }}
                  />
                )}
                {tab !== "parties" && (
                  <>
                    <Input
                      className="w-full sm:w-48"
                      aria-label="搜索往来对象"
                      placeholder={`搜索${partyLabel}`}
                      maxLength={200}
                      disabled={locked}
                      value={partySearch}
                      onChange={(event) => setPartySearch(event.target.value)}
                    />
                    <select
                      className={selectClass}
                      aria-label="筛选往来对象"
                      disabled={locked}
                      value={partyFilter}
                      onChange={(event) => {
                        setPartyFilter(event.target.value);
                        setPage(1);
                      }}
                    >
                      <option value="">全部{partyLabel}</option>
                      {parties.data?.items.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.code} {item.name}
                        </option>
                      ))}
                    </select>
                  </>
                )}
                {tab === "sources" && (
                  <select
                    className={selectClass}
                    aria-label="资金来源状态"
                    disabled={locked}
                    value={status}
                    onChange={(event) => {
                      setStatus(event.target.value as Source["status"] | "");
                      setPage(1);
                    }}
                  >
                    <option value="">全部状态</option>
                    {["OPEN", "REFUND", "SETTLED", "REVERSED"].map((item) => (
                      <option key={item} value={item}>
                        {names[item]}
                      </option>
                    ))}
                  </select>
                )}
                {tab === "cash" && (
                  <select
                    className={selectClass}
                    aria-label="收付款类型"
                    disabled={locked}
                    value={cashFilter}
                    onChange={(event) => {
                      setCashFilter(event.target.value as CashKind | "");
                      setPage(1);
                    }}
                  >
                    <option value="">全部类型</option>
                    <option value="SETTLEMENT">
                      {cashName(side, "SETTLEMENT")}
                    </option>
                    <option value="REFUND">{cashName(side, "REFUND")}</option>
                  </select>
                )}
              </div>
              {parties.error && <p role="alert">{parties.error.message}</p>}
              {active.error ? (
                <p role="alert">{active.error.message}</p>
              ) : active.isPending ? (
                <p role="status">正在读取资金记录…</p>
              ) : (
                <>
                  {tab === "sources" && (
                    <FundsTable
                      headers={[
                        "来源单号",
                        partyLabel,
                        "类型 / 状态",
                        "业务金额",
                        "待结算",
                        "待退款",
                        "操作",
                      ]}
                      rows={(sources.data?.items ?? []).map((item) => ({
                        id: item.id,
                        cells: [
                          item.number,
                          item.party_name,
                          `${names[item.kind]} / ${names[item.status]}`,
                          amountText(item.commercial_amount),
                          amountText(item.settlement_amount),
                          amountText(item.refund_amount),
                          <Button
                            key="view"
                            variant="outline"
                            disabled={locked}
                            onClick={() =>
                              setSelected({ kind: "source", id: item.id })
                            }
                          >
                            查看来源
                          </Button>,
                        ],
                      }))}
                    />
                  )}
                  {tab === "cash" && (
                    <FundsTable
                      headers={[
                        "收付款单号",
                        partyLabel,
                        "类型 / 状态",
                        "日期",
                        "金额",
                        "方式",
                        "操作",
                      ]}
                      rows={(cash.data?.items ?? []).map((item) => ({
                        id: item.id,
                        cells: [
                          item.number,
                          item.party_name,
                          `${cashName(side, item.kind)} / ${names[item.status]}`,
                          item.business_date,
                          amountText(item.amount),
                          names[item.method],
                          <Button
                            key="view"
                            variant="outline"
                            disabled={locked}
                            onClick={() =>
                              setSelected({ kind: "cash", id: item.id })
                            }
                          >
                            查看收付款
                          </Button>,
                        ],
                      }))}
                    />
                  )}
                  {tab === "parties" && (
                    <FundsTable
                      headers={[
                        partyLabel,
                        "编码",
                        "状态",
                        "待结算",
                        "待退款",
                        "净额",
                        "操作",
                      ]}
                      rows={(partyList.data?.items ?? []).map((item) => ({
                        id: item.id,
                        cells: [
                          item.name,
                          item.code,
                          item.is_active ? "使用中" : "已停用",
                          amountText(item.settlement_amount),
                          amountText(item.refund_amount),
                          amountText(item.balance),
                          <Button
                            key="view"
                            variant="outline"
                            disabled={locked}
                            onClick={() => {
                              setPartyFilter(item.id);
                              setTab("sources");
                              setPage(1);
                            }}
                          >
                            查看往来
                          </Button>,
                        ],
                      }))}
                    />
                  )}
                  {tab === "legacy" && has("funds.opening") && (
                    <>
                      <p className="mb-3 text-sm text-muted-foreground">
                        旧单不会自动记为欠款。继续办理旧单退货或冲销前，请按真实未结金额绑定期初；已结清原单可明确绑定零余额。
                      </p>
                      <FundsTable
                        headers={[
                          "旧业务单号",
                          partyLabel,
                          "原商业金额",
                          "已退金额",
                          "有效金额",
                          "操作",
                        ]}
                        rows={(legacy.data?.items ?? []).map((item) => ({
                          id: item.id,
                          cells: [
                            item.number,
                            item.party_name,
                            amountText(item.commercial_amount),
                            amountText(item.returned_amount),
                            amountText(item.effective_amount),
                            <Button
                              key="bind"
                              disabled={locked}
                              onClick={() =>
                                openEditor(
                                  { kind: "BIND", document: item },
                                  {
                                    party_id: item.party_id,
                                    party_label: item.party_name,
                                    amount: "0.0000",
                                  },
                                )
                              }
                            >
                              绑定期初
                            </Button>,
                          ],
                        }))}
                      />
                    </>
                  )}
                </>
              )}
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <Button
                  variant="outline"
                  disabled={locked || page <= 1}
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
              {selected &&
                (selected.kind === "source"
                  ? sourceDetail.isPending
                  : cashDetail.isPending) && (
                  <p role="status">正在读取资金详情…</p>
                )}
              {selected &&
                (selected.kind === "source"
                  ? sourceDetail.error
                  : cashDetail.error) && (
                  <p role="alert">
                    {
                      (selected.kind === "source"
                        ? sourceDetail.error
                        : cashDetail.error
                      )?.message
                    }
                  </p>
                )}
              {source && !sourceDetail.error && (
                <section
                  aria-label="资金来源详情"
                  className="min-w-0 space-y-4 rounded-xl border bg-white p-4 sm:p-6"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h2 className="break-all text-lg font-medium">
                        {source.number}
                      </h2>
                      <p className="text-sm text-muted-foreground">
                        {source.party_name} · {names[source.kind]} ·{" "}
                        {names[source.status]}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      disabled={locked}
                      onClick={() => setSelected(null)}
                    >
                      关闭详情
                    </Button>
                  </div>
                  <dl className="grid min-w-0 grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                    {[
                      ["来源金额", source.amount],
                      ["有效商业金额", source.commercial_amount],
                      ["期初已结金额", source.historically_settled_amount],
                      ["本系统已结算", source.settled_amount],
                      ["本系统已退款", source.refunded_amount],
                      ["待结算", source.settlement_amount],
                      ["待退款", source.refund_amount],
                      ["往来净额", source.balance],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd className="break-all tabular-nums">
                          {amountText(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className="text-sm text-muted-foreground">
                    期初已结金额来自历史余额核对，不计为本系统实际收付款。
                  </p>
                  <p className="break-words text-sm">{source.reason}</p>
                  {commercialLink(source)}
                  <div className="flex flex-wrap gap-2">
                    {canCash("SETTLEMENT") &&
                      positiveAmount(source.settlement_amount) && (
                        <Button
                          disabled={locked}
                          onClick={() => openCash("SETTLEMENT", source)}
                        >
                          登记{cashName(side, "SETTLEMENT")}
                        </Button>
                      )}
                    {canCash("REFUND") &&
                      positiveAmount(source.refund_amount) && (
                        <Button
                          disabled={locked}
                          onClick={() => openCash("REFUND", source)}
                        >
                          登记{cashName(side, "REFUND")}
                        </Button>
                      )}
                    {has("funds.reverse") &&
                      ["OPENING", "ADJUSTMENT"].includes(source.kind) &&
                      source.status !== "REVERSED" && (
                        <Button
                          variant="outline"
                          disabled={locked}
                          onClick={() =>
                            openEditor({
                              kind: "REVERSE",
                              selection: { kind: "source", id: source.id },
                              number: source.number,
                            })
                          }
                        >
                          冲销来源
                        </Button>
                      )}
                  </div>
                  <h3 className="text-sm font-medium">来源变化记录</h3>
                  <FundsTable
                    headers={["时间", "变动金额", "说明", "关联来源"]}
                    rows={source.entries.map((entry) => ({
                      id: entry.id,
                      cells: [
                        entry.created_at,
                        amountText(entry.amount),
                        entry.reason,
                        entry.related_source_id ? (
                          <Button
                            key="source"
                            variant="outline"
                            disabled={locked}
                            onClick={() =>
                              setSelected({
                                kind: "source",
                                id: entry.related_source_id!,
                              })
                            }
                          >
                            查看关联来源
                          </Button>
                        ) : (
                          "—"
                        ),
                      ],
                    }))}
                  />
                  <h3 className="text-sm font-medium">核销与退款记录</h3>
                  <FundsTable
                    headers={[
                      "收付款单号",
                      "类型",
                      "状态",
                      "本来源金额",
                      "操作",
                    ]}
                    rows={source.cash.map((item) => ({
                      id: `${item.cash_id}:${item.source_id}`,
                      cells: [
                        item.cash_number,
                        cashName(side, item.cash_kind),
                        names[item.cash_status],
                        amountText(item.amount),
                        <Button
                          key="cash"
                          variant="outline"
                          disabled={locked}
                          onClick={() =>
                            setSelected({ kind: "cash", id: item.cash_id })
                          }
                        >
                          查看收付款
                        </Button>,
                      ],
                    }))}
                  />
                </section>
              )}
              {cashRecord && !cashDetail.error && (
                <section
                  aria-label="收付款详情"
                  className="min-w-0 space-y-4 rounded-xl border bg-white p-4 sm:p-6"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h2 className="break-all text-lg font-medium">
                        {cashRecord.number}
                      </h2>
                      <p className="text-sm text-muted-foreground">
                        {cashRecord.party_name} ·{" "}
                        {cashName(side, cashRecord.kind)} ·{" "}
                        {names[cashRecord.status]}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      disabled={locked}
                      onClick={() => setSelected(null)}
                    >
                      关闭详情
                    </Button>
                  </div>
                  <p className="break-all text-xl tabular-nums">
                    本次{cashName(side, cashRecord.kind)}：
                    {amountText(cashRecord.amount)}
                  </p>
                  <p className="text-sm">
                    业务日期 {cashRecord.business_date} ·{" "}
                    {names[cashRecord.method]}
                  </p>
                  <p className="break-all text-sm">
                    外部参考号：{cashRecord.external_reference || "未填写"}
                  </p>
                  <p className="break-words text-sm">{cashRecord.reason}</p>
                  {cashRecord.reversal_reason && (
                    <p className="break-words text-sm">
                      冲销说明：{cashRecord.reversal_reason}
                    </p>
                  )}
                  {has("funds.reverse") && cashRecord.status === "POSTED" && (
                    <Button
                      variant="outline"
                      disabled={locked}
                      onClick={() =>
                        openEditor({
                          kind: "REVERSE",
                          selection: { kind: "cash", id: cashRecord.id },
                          number: cashRecord.number,
                        })
                      }
                    >
                      冲销收付款
                    </Button>
                  )}
                  <FundsTable
                    headers={["核销来源", "分配金额", "操作"]}
                    rows={cashRecord.allocations.map((item) => ({
                      id: item.source_id,
                      cells: [
                        item.source_number,
                        amountText(item.amount),
                        <Button
                          key="source"
                          variant="outline"
                          disabled={locked}
                          onClick={() =>
                            setSelected({ kind: "source", id: item.source_id })
                          }
                        >
                          查看来源
                        </Button>,
                      ],
                    }))}
                  />
                </section>
              )}
            </>
          )}
        </>
      )}

      <Dialog.Root
        open={!!editor}
        onOpenChange={(open) => {
          if (!open) closeEditor();
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/35" />
          <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] max-w-4xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-white p-4 shadow-xl sm:p-6">
            <Dialog.Title className="text-lg font-medium">
              {dialogTitle}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              {editor?.kind === "CASH"
                ? "这里只记录实际发生的收付款，不会执行银行转账。先选择来源并预览金额，再确认记录。"
                : editor?.kind === "ACTIVATE"
                  ? "启用后新出入库单据会形成应收应付；已有单据不会自动补记。请确认切换日期及历史余额安排。"
                  : editor?.kind === "REVERSE"
                    ? "冲销保留原始记录；服务端会检查后续核销、退货及退款依赖。"
                    : "根据已核对的真实余额录入，保存后保留资金来源与操作说明。"}
            </Dialog.Description>
            {feedback}
            {!canSubmit && (
              <p role="alert" className="mt-3 text-sm">
                当前操作权限已变化，无法提交新的资金记录。
              </p>
            )}
            {canSeeEditor ? (
              <form
                className="mt-4 min-w-0 space-y-4"
                onSubmit={form.handleSubmit(submit)}
              >
                <fieldset
                  disabled={locked || !canSubmit}
                  className="min-w-0 space-y-4 disabled:opacity-70"
                >
                  {(editor?.kind === "CASH" ||
                    editor?.kind === "OPENING" ||
                    editor?.kind === "ADJUSTMENT") && (
                    <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                      <label className="min-w-0 text-sm">
                        搜索{partyLabel}
                        <Input
                          aria-label="查找资金往来对象"
                          className="mt-1"
                          value={partySearch}
                          maxLength={200}
                          onChange={(event) =>
                            setPartySearch(event.target.value)
                          }
                          placeholder={`输入${partyLabel}名称或编码`}
                        />
                      </label>
                      <label className="min-w-0 text-sm">
                        {partyLabel}
                        <select
                          className={`${selectClass} mt-1 w-full`}
                          aria-label="资金往来对象"
                          value={fields.party_id ?? ""}
                          onChange={(event) => {
                            form.setValue("party_id", event.target.value);
                            form.setValue(
                              "party_label",
                              parties.data?.items.find(
                                (item) => item.id === event.target.value,
                              )?.name ?? "",
                            );
                            form.setValue("allocations", []);
                            setCandidatePage(1);
                          }}
                        >
                          <option value="">请选择{partyLabel}</option>
                          {fields.party_id &&
                            !parties.data?.items.some(
                              (item) => item.id === fields.party_id,
                            ) && (
                              <option value={fields.party_id}>
                                {fields.party_label || "已选择往来对象"}
                              </option>
                            )}
                          {parties.data?.items.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.code} {item.name}
                              {item.is_active ? "" : "（已停用）"}
                            </option>
                          ))}
                        </select>
                      </label>
                      {parties.isPending && (
                        <p role="status">正在读取往来对象…</p>
                      )}
                      {parties.error && (
                        <p role="alert">{parties.error.message}</p>
                      )}
                    </div>
                  )}
                  {(editor?.kind === "ACTIVATE" || editor?.kind === "CASH") && (
                    <label className="block text-sm">
                      {editor.kind === "ACTIVATE" ? "资金启用日期" : "业务日期"}
                      <Input
                        className="mt-1"
                        type="date"
                        aria-label={
                          editor.kind === "ACTIVATE"
                            ? "资金启用日期"
                            : "资金业务日期"
                        }
                        {...form.register("business_date")}
                      />
                    </label>
                  )}
                  {editor?.kind === "CASH" && (
                    <>
                      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                        <label className="min-w-0 text-sm">
                          收付方式
                          <select
                            className={`${selectClass} mt-1 w-full`}
                            aria-label="收付方式"
                            {...form.register("method")}
                          >
                            <option value="CASH">现金</option>
                            <option value="BANK_TRANSFER">银行转账</option>
                            <option value="OTHER">其他</option>
                          </select>
                        </label>
                        <label className="min-w-0 text-sm">
                          外部参考号
                          <Input
                            className="mt-1"
                            aria-label="资金外部参考号"
                            maxLength={200}
                            {...form.register("external_reference")}
                          />
                        </label>
                      </div>
                      <h3 className="text-sm font-medium">
                        选择{editor.cashKind === "REFUND" ? "退款" : "核销"}来源
                      </h3>
                      {!fields.party_id ? (
                        <p className="text-sm text-muted-foreground">
                          请先选择{partyLabel}。
                        </p>
                      ) : candidates.isPending ? (
                        <p role="status">正在读取可选来源…</p>
                      ) : candidates.error ? (
                        <p role="alert">{candidates.error.message}</p>
                      ) : (
                        <>
                          <FundsTable
                            headers={[
                              "选择",
                              "来源单号",
                              "类型",
                              editor.cashKind === "REFUND"
                                ? "可退款"
                                : "可核销",
                            ]}
                            empty="本页没有可用来源。"
                            rows={(candidates.data?.items ?? []).map(
                              (item) => ({
                                id: item.id,
                                cells: [
                                  <input
                                    key="select"
                                    type="checkbox"
                                    aria-label={`选择来源 ${item.number}`}
                                    checked={allocations.some(
                                      (line) => line.source_id === item.id,
                                    )}
                                    disabled={
                                      locked ||
                                      (!allocations.some(
                                        (line) => line.source_id === item.id,
                                      ) &&
                                        allocations.length >= 100)
                                    }
                                    onChange={(event) => {
                                      const current =
                                        form.getValues("allocations");
                                      form.setValue(
                                        "allocations",
                                        event.target.checked
                                          ? [
                                              ...current,
                                              {
                                                source_id: item.id,
                                                number: item.number,
                                                amount:
                                                  editor.cashKind === "REFUND"
                                                    ? item.refund_amount
                                                    : item.settlement_amount,
                                                limit:
                                                  editor.cashKind === "REFUND"
                                                    ? item.refund_amount
                                                    : item.settlement_amount,
                                              },
                                            ]
                                          : current.filter(
                                              (line) =>
                                                line.source_id !== item.id,
                                            ),
                                      );
                                    }}
                                  />,
                                  item.number,
                                  names[item.kind],
                                  amountText(
                                    editor.cashKind === "REFUND"
                                      ? item.refund_amount
                                      : item.settlement_amount,
                                  ),
                                ],
                              }),
                            )}
                          />
                          <div className="flex flex-wrap items-center gap-2 text-sm">
                            <Button
                              type="button"
                              variant="outline"
                              disabled={candidatePage <= 1}
                              onClick={() =>
                                setCandidatePage((value) => value - 1)
                              }
                            >
                              来源上一页
                            </Button>
                            <span>来源第 {candidatePage} 页</span>
                            <Button
                              type="button"
                              variant="outline"
                              disabled={
                                candidatePage * 25 >=
                                (candidates.data?.total ?? 0)
                              }
                              onClick={() =>
                                setCandidatePage((value) => value + 1)
                              }
                            >
                              来源下一页
                            </Button>
                          </div>
                        </>
                      )}
                      {allocations.length > 0 && (
                        <FundsTable
                          headers={[
                            "已选来源",
                            "查询时可用额",
                            "本次分配金额",
                            "操作",
                          ]}
                          rows={allocations.map((line, index) => ({
                            id: line.source_id,
                            cells: [
                              line.number,
                              amountText(line.limit),
                              <Input
                                key="amount"
                                aria-label={`核销金额 ${line.number}`}
                                inputMode="decimal"
                                className="min-w-32"
                                {...form.register(
                                  `allocations.${index}.amount`,
                                )}
                              />,
                              <Button
                                key="remove"
                                type="button"
                                variant="outline"
                                onClick={() =>
                                  form.setValue(
                                    "allocations",
                                    form
                                      .getValues("allocations")
                                      .filter(
                                        (item) =>
                                          item.source_id !== line.source_id,
                                      ),
                                  )
                                }
                              >
                                移除来源
                              </Button>,
                            ],
                          }))}
                        />
                      )}
                    </>
                  )}
                  {(editor?.kind === "OPENING" ||
                    editor?.kind === "ADJUSTMENT") && (
                    <>
                      <label className="block text-sm">
                        往来金额
                        <Input
                          className="mt-1"
                          inputMode="decimal"
                          aria-label="资金往来金额"
                          {...form.register("amount")}
                        />
                        <span className="mt-1 block text-xs text-muted-foreground">
                          正数表示尚待{side === "AR" ? "收款" : "付款"}
                          ；负数表示尚待
                          {side === "AR" ? "退给客户" : "供应商退款"}
                          。最多四位小数。
                        </span>
                      </label>
                      <label className="flex items-start gap-2 text-sm">
                        <input
                          className="mt-1"
                          type="checkbox"
                          {...form.register("refund_balance_confirmed")}
                        />
                        我已核实负数金额对应真实待退款余额
                      </label>
                    </>
                  )}
                  {editor?.kind === "BIND" && (
                    <>
                      <p className="break-words text-sm">
                        历史单据 {editor.document.number} ·{" "}
                        {editor.document.party_name} · 有效商业金额{" "}
                        {amountText(editor.document.effective_amount)} ·
                        已退商业金额{" "}
                        {amountText(editor.document.returned_amount)}
                      </p>
                      <label className="block text-sm">
                        从期初来源分配
                        <select
                          className={`${selectClass} mt-1 w-full`}
                          aria-label="绑定期初来源"
                          value={fields.opening_source_id ?? ""}
                          onChange={(event) => {
                            form.setValue(
                              "opening_source_id",
                              event.target.value,
                            );
                            const item = candidates.data?.items.find(
                              (candidate) =>
                                candidate.id === event.target.value,
                            );
                            form.setValue("amount", item?.balance ?? "0.0000");
                            form.setValue(
                              "opening_source_label",
                              item
                                ? `${item.number} · 余额 ${amountText(item.balance)}`
                                : "",
                            );
                          }}
                        >
                          <option value="">无期初来源（明确零余额）</option>
                          {fields.opening_source_id &&
                            !candidates.data?.items.some(
                              (item) => item.id === fields.opening_source_id,
                            ) && (
                              <option value={fields.opening_source_id}>
                                {fields.opening_source_label ||
                                  "已选择期初来源"}
                              </option>
                            )}
                          {candidates.data?.items
                            .filter(
                              (item) =>
                                item.kind === "OPENING" &&
                                item.status !== "REVERSED",
                            )
                            .map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.number} · 余额 {amountText(item.balance)}
                              </option>
                            ))}
                        </select>
                      </label>
                      {candidates.error && (
                        <p role="alert">{candidates.error.message}</p>
                      )}
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <Button
                          type="button"
                          variant="outline"
                          disabled={candidatePage <= 1}
                          onClick={() => setCandidatePage((value) => value - 1)}
                        >
                          来源上一页
                        </Button>
                        <span>来源第 {candidatePage} 页</span>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={
                            candidatePage * 25 >= (candidates.data?.total ?? 0)
                          }
                          onClick={() => setCandidatePage((value) => value + 1)}
                        >
                          来源下一页
                        </Button>
                      </div>
                      <label className="block text-sm">
                        该历史单据的未结金额
                        <Input
                          className="mt-1"
                          aria-label="历史未结金额"
                          inputMode="decimal"
                          disabled={!fields.opening_source_id}
                          {...form.register("amount")}
                        />
                      </label>
                      <label className="flex items-start gap-2 text-sm">
                        <input
                          className="mt-1"
                          type="checkbox"
                          {...form.register("refund_balance_confirmed")}
                        />
                        我已核实负数历史余额为已结算但尚待退款
                      </label>
                      <p className="text-xs text-muted-foreground">
                        正数表示欠款，负数表示已结算但尚待退款。绑定只是明确旧单的资金归属，不重复登记现金；服务端会核对原单及期初可用额度。请核对历史实际结算情况，不把原销售或采购总额直接当作欠款。
                      </p>
                    </>
                  )}
                  {editor?.kind === "REVERSE" && (
                    <p className="break-all text-sm">
                      待冲销单号：{editor.number}
                    </p>
                  )}
                  <label className="block text-sm">
                    操作说明
                    <textarea
                      className="mt-1 min-h-24 w-full rounded-md border px-3 py-2"
                      aria-label="资金操作说明"
                      maxLength={2000}
                      {...form.register("reason")}
                    />
                  </label>
                </fieldset>
                {cashPreview && editor?.kind === "CASH" && canSubmit && (
                  <section
                    aria-label="收付款金额预览"
                    className="min-w-0 space-y-3 rounded-lg bg-[#eef4e9] p-4 text-sm"
                  >
                    <p className="break-all font-medium">
                      本次{cashName(side, editor.cashKind)}总额：
                      {amountText(cashPreview.value.amount)}
                    </p>
                    <p>{cashPreview.value.party_name} · 人民币 CNY</p>
                    <FundsTable
                      headers={["来源", "本次金额", "查询时可用额"]}
                      rows={cashPreview.value.allocations.map((item) => ({
                        id: item.source_id,
                        cells: [
                          item.source_number,
                          amountText(item.amount),
                          amountText(item.available_amount),
                        ],
                      }))}
                    />
                    <p>
                      金额已由服务器核对。确认记录时会重新检查余额；更改任一填写内容后须重新预览。
                    </p>
                  </section>
                )}
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={locked}
                    onClick={closeEditor}
                  >
                    取消
                  </Button>
                  {editor?.kind === "CASH" && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={locked || previewLoading || !canSubmit}
                      onClick={() => void form.handleSubmit(calculatePreview)()}
                    >
                      {previewLoading ? "正在核对金额…" : "预览核销金额"}
                    </Button>
                  )}
                  <Button
                    type="submit"
                    disabled={
                      locked ||
                      !canSubmit ||
                      (editor?.kind === "CASH" &&
                        (!cashPreview || previewLoading))
                    }
                  >
                    {submission.busy ? "正在提交…" : confirmationLabel}
                  </Button>
                </div>
              </form>
            ) : (
              <Button
                className="mt-4"
                variant="outline"
                disabled={locked}
                onClick={closeEditor}
              >
                关闭
              </Button>
            )}
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
