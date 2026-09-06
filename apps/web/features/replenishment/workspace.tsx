"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { Dialog } from "@base-ui/react/dialog";
import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Facts,
  OperationsTable,
  SubmissionFeedback,
  selectClass,
} from "@/features/operations/presentation";
import { useOperationSubmission } from "@/features/operations/use-submission";
import {
  hasEvery,
  replenishmentReadPermissions,
  replenishmentCreatePermissions,
} from "@/features/operations/permissions";
import {
  createPurchase,
  previewPurchase,
  type Preview,
  type PreviewInput,
  type Receipt,
  type Suggestion,
} from "./client";

type LineForm = {
  product_id: string;
  basis_hash: string;
  unit_id: string;
  qty: string;
  quantity_reason: string;
  price_source: "MANUAL" | "HISTORY";
  unit_price: string;
};
type Form = {
  supplier_id: string;
  warehouse_id: string;
  reason: string;
  lines: LineForm[];
};
const decimal = z.string().regex(/^\d+(?:\.\d+)?$/);
const reasonLabels: Record<string, string> = {
  NO_SALES_HISTORY: "最近30个完整业务日没有销售记录，按已配置的最低库存判断。",
  NON_POSITIVE_NET_SALES: "期间净销量不大于零，按已配置的最低库存判断。",
  MISSING_LEAD_DAYS:
    "未配置供应提前期，使用静态最低库存；需要时先核对供货设置。",
  ABOVE_REORDER_POINT: "当前可用库存高于补货阈值，暂不建议补货。",
  INBOUND_COVERS_TARGET: "已有库存与采购在途已覆盖目标，暂不重复补货。",
  TARGET_COVERED: "当前库存已覆盖目标，暂不建议补货。",
  NO_DEMAND_BASIS: "没有正净销量且未配置最低库存，缺少补货需求依据。",
  REPLENISHMENT_SUGGESTED: "可用库存达到补货阈值且目标仍有缺口，建议复核补货。",
  MISSING_UNIT_CONVERSION:
    "商品尚未配置所选采购单位的换算，请改选已配置单位或先维护换算。",
  ZERO_SUGGESTION: "当前建议量为零。如需采购，请明确填写数量与调整原因。",
  QUANTITY_CONVERSION_REQUIRED:
    "建议量无法精确换算为所选采购单位，请改选基础单位，或明确填写数量与调整原因。",
  HISTORY_PRICE_UNAVAILABLE:
    "该供应商没有可用的实际历史采购价，请明确填写单价。",
  HISTORY_PRICE_PRECISION_CONFLICT:
    "历史采购价无法精确换算为所选单位，请核对单位并明确填写单价。",
  PRICE_REQUIRED: "采购单价尚未填写，请明确填写单价或选用可用的历史采购价。",
  SUPPLIER_DIFFERS_FROM_BASIS:
    "所选供应商与计算依据中的首选供应商不同，请核对供货提前期和采购条件。",
};
function reasonText(value: string) {
  return (
    reasonLabels[value] ??
    (/^[A-Z0-9_]+$/.test(value)
      ? "需要重新核对补货条件，请刷新依据后重试。"
      : value)
  );
}

function Basis({ value }: { value: Suggestion }) {
  return (
    <div className="space-y-3">
      <Facts
        values={[
          ["基础单位", value.base_unit_name],
          ["当前可用库存", value.available_qty],
          ["采购在途", value.open_purchase_qty],
          ["期间出库", value.shipped_qty],
          ["期间退货", value.returned_qty],
          ["净销量", value.net_sales_qty],
          ["日均销量", value.daily_sales_qty],
          ["最低库存", value.safety_stock_qty],
          ["最低建议量", value.minimum_reorder_qty],
          ["提前期（天）", value.lead_days ?? "未配置"],
          ["补货阈值", value.reorder_point],
          ["目标库存", value.target_stock],
          ["库存位置", value.inventory_position],
          ["缺口", value.gap],
          ["建议基础数量", value.suggested_base_qty],
          ["可售天数", value.days_of_stock ?? "无可用销量依据"],
          ["首选供应商", value.preferred_supplier_name ?? "未配置"],
        ]}
      />
      <ul className="list-inside list-disc text-sm">
        {value.reasons.map((reason, index) => (
          <li key={index}>{reasonText(reason)}</li>
        ))}
      </ul>
    </div>
  );
}
export function ReplenishmentWorkspace({
  permissions,
}: {
  permissions: string[];
}) {
  const read = hasEvery(permissions, replenishmentReadPermissions);
  const create = hasEvery(permissions, replenishmentCreatePermissions);
  const permissionKey = permissions.join("|");
  const qc = useQueryClient();
  const submission = useOperationSubmission();
  const { locked } = submission;
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [supplierFilter, setSupplierFilter] = useState("");
  const [candidateOnly, setCandidateOnly] = useState(false);
  const [suggestedOnly, setSuggestedOnly] = useState(true);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Suggestion[]>([]);
  const [basis, setBasis] = useState<Suggestion | null>(null);
  const [open, setOpen] = useState(false);
  const [supplierQ, setSupplierQ] = useState("");
  const [warehouseQ, setWarehouseQ] = useState("");
  const [unitQ, setUnitQ] = useState("");
  const [categoryQ, setCategoryQ] = useState("");
  const [review, setReview] = useState<{
    fingerprint: string;
    body: PreviewInput;
    value: Preview;
  } | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const controller = useRef<AbortController | null>(null);
  const previewFlight = useRef(false);
  const form = useForm<Form>({
    defaultValues: { supplier_id: "", warehouse_id: "", reason: "", lines: [] },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const current = useWatch({ control: form.control });
  const fingerprint = JSON.stringify([current, permissionKey]);
  const currentFingerprint = useRef(fingerprint);

  const reviewed =
    create && review?.fingerprint === fingerprint ? review : null;
  useEffect(() => {
    currentFingerprint.current = fingerprint;
    controller.current?.abort();
    return () => controller.current?.abort();
  }, [fingerprint]);
  const suggestions = useQuery({
    queryKey: [
      "replenishment",
      "suggestions",
      q,
      category,
      supplierFilter,
      candidateOnly,
      suggestedOnly,
      page,
      permissionKey,
    ],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/replenishment/suggestions", {
          signal,
          params: {
            query: {
              q: q || undefined,
              category_id: category || undefined,
              supplier_id: supplierFilter || undefined,
              candidate_only: candidateOnly,
              suggested_only: suggestedOnly,
              page,
              page_size: 25,
            },
          },
        }),
      ),
  });
  const suppliers = useQuery({
    queryKey: ["replenishment", "suppliers", supplierQ, permissionKey],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/suppliers", {
          signal,
          params: {
            query: {
              q: supplierQ || undefined,
              active: true,
              page: 1,
              page_size: 100,
            },
          },
        }),
      ),
  });
  const categories = useQuery({
    queryKey: ["replenishment", "categories", categoryQ, permissionKey],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/categories", {
          signal,
          params: {
            query: {
              q: categoryQ || undefined,
              active: true,
              page: 1,
              page_size: 100,
            },
          },
        }),
      ),
  });
  const warehouses = useQuery({
    queryKey: ["replenishment", "warehouses", warehouseQ, permissionKey],
    enabled: create && open,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/replenishment/warehouses", {
          signal,
          params: {
            query: { q: warehouseQ || undefined, page: 1, page_size: 100 },
          },
        }),
      ),
  });
  const units = useQuery({
    queryKey: ["replenishment", "units", unitQ, permissionKey],
    enabled: create && open,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/units", {
          signal,
          params: {
            query: {
              q: unitQ || undefined,
              active: true,
              page: 1,
              page_size: 100,
            },
          },
        }),
      ),
  });
  function start() {
    if (submission.isLocked() || !create || !selected.length) return;
    form.reset({
      supplier_id: "",
      warehouse_id: "",
      reason: "",
      lines: selected.map((value) => ({
        product_id: value.product_id,
        basis_hash: value.basis_hash,
        unit_id: value.default_purchase_unit_id,
        qty: "",
        quantity_reason: "",
        price_source: "MANUAL",
        unit_price: "",
      })),
    });
    setReview(null);
    setPreviewError("");
    setReceipt(null);
    submission.setError("");
    setOpen(true);
  }
  async function preview(values: Form) {
    if (submission.isLocked() || previewFlight.current || !create) return;
    if (
      !values.supplier_id ||
      !values.warehouse_id ||
      !values.reason.trim() ||
      values.reason.trim().length > 500 ||
      values.lines.some(
        (line) =>
          !line.unit_id ||
          (line.qty !== "" &&
            (!decimal.safeParse(line.qty).success ||
              !line.quantity_reason.trim())) ||
          (line.price_source === "MANUAL" &&
            line.unit_price !== "" &&
            !decimal.safeParse(line.unit_price).success),
      )
    ) {
      setPreviewError(
        "请选择供应商、仓库与单位并填写原因；手工数量须填写调整原因，数量和单价使用十进制文本。",
      );
      return;
    }
    const body: PreviewInput = {
      supplier_id: values.supplier_id,
      warehouse_id: values.warehouse_id,
      reason: values.reason.trim(),
      lines: values.lines.map((line) => ({
        product_id: line.product_id,
        basis_hash: line.basis_hash,
        unit_id: line.unit_id,
        qty: line.qty || null,
        quantity_reason: line.qty ? line.quantity_reason.trim() : null,
        price_source: line.price_source,
        unit_price:
          line.price_source === "MANUAL" ? line.unit_price || null : null,
      })),
    };
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const capturedFingerprint = currentFingerprint.current;
    previewFlight.current = true;
    setPreviewBusy(true);
    setReview(null);
    setPreviewError("");
    try {
      const value = await previewPurchase(body, request.signal);
      if (
        !request.signal.aborted &&
        capturedFingerprint === currentFingerprint.current
      )
        setReview({ body, value, fingerprint: capturedFingerprint });
    } catch (error) {
      if (!request.signal.aborted)
        setPreviewError(
          error instanceof Error ? error.message : "预览失败，请重试。",
        );
    } finally {
      previewFlight.current = false;
      setPreviewBusy(false);
    }
  }
  async function save() {
    if (submission.isLocked() || !create || !reviewed?.value.can_create) return;
    const body = {
      ...reviewed.body,
      confirmation_hash: reviewed.value.confirmation_hash,
    };
    await submission.submit(
      (key) => createPurchase(body, key),
      async (value) => {
        setReceipt(value);
        setOpen(false);
        setReview(null);
        setSelected([]);
        await qc.invalidateQueries({ queryKey: ["replenishment"] });
      },
    );
  }
  async function refresh() {
    if (submission.isLocked()) return;
    controller.current?.abort();
    setReview(null);
    setSelected([]);
    setOpen(false);
    setBasis(null);
    submission.clearConflict();
    await qc.invalidateQueries({ queryKey: ["replenishment"] });
  }
  return (
    <section aria-label="补货建议工作台" className="mt-8 min-w-0 space-y-5">
      {!read ? (
        <p role="alert">当前没有查看补货建议所需的权限。</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            按组织汇总可用库存、已确认采购在途及最近30个完整业务日的净销量。建议供人工复核，生成后仍是采购草稿。
          </p>
          <div className="grid min-w-0 gap-5 rounded-2xl border border-border bg-card/25 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-3">
            <label className="text-sm">
              商品关键词
              <Input
                aria-label="补货商品关键词"
                maxLength={200}
                value={q}
                disabled={locked}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
              />
            </label>
            <label className="text-sm">
              查找分类
              <Input
                aria-label="查找补货分类"
                maxLength={200}
                value={categoryQ}
                disabled={locked}
                onChange={(e) => setCategoryQ(e.target.value)}
              />
              <select
                aria-label="补货分类"
                className={selectClass + " mt-1 w-full"}
                disabled={locked}
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部分类</option>
                {categories.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.code} · {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              查找供应商
              <Input
                aria-label="查找补货供应商"
                maxLength={200}
                value={supplierQ}
                disabled={locked}
                onChange={(e) => setSupplierQ(e.target.value)}
              />
              <select
                aria-label="首选供应商筛选"
                className={selectClass + " mt-1 w-full"}
                disabled={locked}
                value={supplierFilter}
                onChange={(e) => {
                  setSupplierFilter(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部首选供应商</option>
                {suppliers.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.code} · {item.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm">
              <input
                type="checkbox"
                checked={candidateOnly}
                disabled={locked}
                onChange={(e) => {
                  setCandidateOnly(e.target.checked);
                  setPage(1);
                }}
              />{" "}
              只看达到补货阈值
            </label>
            <label className="text-sm">
              <input
                type="checkbox"
                checked={suggestedOnly}
                disabled={locked}
                onChange={(e) => {
                  setSuggestedOnly(e.target.checked);
                  setPage(1);
                }}
              />{" "}
              只看有建议量
            </label>
            <Button
              variant="outline"
              disabled={locked}
              onClick={() => void refresh()}
            >
              刷新补货依据
            </Button>
            {create && (
              <Button
                disabled={locked || selected.length === 0}
                onClick={start}
              >
                复核采购草稿（{selected.length}）
              </Button>
            )}
          </div>
          {!open && (
            <SubmissionFeedback
              value={submission}
              onRefresh={() => void refresh()}
            />
          )}
          {receipt && create && (
            <p role="status">
              已生成采购草稿。
              <Link
                className="underline"
                href={`/purchase?order=${receipt.id}`}
              >
                查看采购草稿
              </Link>
            </p>
          )}
          {[suggestions.error, suppliers.error, categories.error]
            .filter(Boolean)
            .map((error, index) => (
              <p role="alert" key={index}>
                {error?.message}
              </p>
            ))}
          {suggestions.isPending && <p role="status">正在读取补货建议…</p>}
          {suggestions.data && !suggestions.error && (
            <>
              <p className="break-words text-sm text-muted-foreground">
                截至 {suggestions.data.as_of} · 业务时区{" "}
                {suggestions.data.business_timezone} · 销量窗口 [
                {suggestions.data.window_start}, {suggestions.data.window_end})
                · 规则 {suggestions.data.algorithm_version}
              </p>
              <OperationsTable
                headers={[
                  "选择 / 商品",
                  "基础单位",
                  "可用 / 在途",
                  "阈值 / 目标",
                  "建议基础数量",
                  "首选供应商 / 原因",
                  "依据",
                ]}
                rows={suggestions.data.items.map((item) => ({
                  id: item.product_id,
                  cells: [
                    <div key="product" className="min-w-36">
                      {create && (
                        <input
                          type="checkbox"
                          aria-label={`选择补货商品 ${item.sku}`}
                          disabled={
                            locked ||
                            (!selected.some(
                              (value) => value.product_id === item.product_id,
                            ) &&
                              selected.length >= 200)
                          }
                          checked={selected.some(
                            (value) => value.product_id === item.product_id,
                          )}
                          onChange={(e) =>
                            setSelected((prior) =>
                              e.target.checked
                                ? [...prior, item]
                                : prior.filter(
                                    (value) =>
                                      value.product_id !== item.product_id,
                                  ),
                            )
                          }
                        />
                      )}{" "}
                      {item.sku}
                      <p>{item.name}</p>
                    </div>,
                    item.base_unit_name,
                    <div key="available">
                      可用 {item.available_qty}
                      <p>在途 {item.open_purchase_qty}</p>
                    </div>,
                    <div key="target">
                      阈值 {item.reorder_point}
                      <p>目标 {item.target_stock}</p>
                    </div>,
                    item.suggested_base_qty,
                    <div
                      key="reason"
                      className="min-w-48 max-w-72 whitespace-normal"
                    >
                      {item.preferred_supplier_name ?? "未配置"}
                      {item.reasons.map((reason, index) => (
                        <p key={index}>{reasonText(reason)}</p>
                      ))}
                    </div>,
                    <Button
                      key="basis"
                      variant="outline"
                      disabled={locked}
                      onClick={() => setBasis(item)}
                    >
                      查看补货依据
                    </Button>,
                  ],
                }))}
              />
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="outline"
                  disabled={locked || page <= 1}
                  onClick={() => setPage((value) => value - 1)}
                >
                  上一页补货
                </Button>
                <span className="text-sm">
                  第 {page} 页 · 共 {suggestions.data.total} 项
                </span>
                <Button
                  variant="outline"
                  disabled={locked || page * 25 >= suggestions.data.total}
                  onClick={() => setPage((value) => value + 1)}
                >
                  下一页补货
                </Button>
              </div>
            </>
          )}
        </>
      )}
      <Dialog.Root
        open={basis !== null}
        onOpenChange={(value) => {
          if (!value && !submission.isLocked()) setBasis(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[88vh] w-[min(96vw,900px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[30px] bg-background p-4 sm:p-6 border border-border">
            <Dialog.Title className="text-lg font-medium">
              补货计算依据
            </Dialog.Title>
            <Dialog.Description className="my-3 text-sm">
              以下数量均使用商品基础单位，采购单位换算在生成草稿前单独复核。
            </Dialog.Description>
            {read && basis ? (
              <>
                <p className="mb-3">
                  {basis.sku} · {basis.name}
                </p>
                <Basis value={basis} />
              </>
            ) : (
              <p role="alert">当前已无权查看补货依据。</p>
            )}
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => setBasis(null)}
            >
              关闭依据
            </Button>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={open}
        onOpenChange={(value) => {
          if (!value && !submission.isLocked()) {
            controller.current?.abort();
            setOpen(false);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[90vh] w-[min(96vw,1100px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[30px] bg-background p-4 sm:p-6 border border-border">
            <Dialog.Title className="text-lg font-medium">
              补货采购草稿复核
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              选择一个供应商和收货仓库，核对采购单位、数量与单价。只有服务器预览通过后才能创建草稿。
            </Dialog.Description>
            <div className="mt-4 space-y-4">
              <SubmissionFeedback
                value={submission}
                onRefresh={() => void refresh()}
              />
              {!create ? (
                <p role="alert">当前已无权复核或创建采购草稿。</p>
              ) : (
                <>
                  <form
                    className="space-y-4"
                    onSubmit={(event) => void form.handleSubmit(preview)(event)}
                  >
                    <fieldset disabled={locked} className="min-w-0 space-y-4">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-sm">
                          供应商
                          <select
                            aria-label="补货采购供应商"
                            className={selectClass + " mt-1 w-full"}
                            {...form.register("supplier_id")}
                          >
                            <option value="">请选择供应商</option>
                            {suppliers.data?.items.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.code} · {item.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="text-sm">
                          收货仓库
                          <select
                            aria-label="补货收货仓库"
                            className={selectClass + " mt-1 w-full"}
                            {...form.register("warehouse_id")}
                          >
                            <option value="">请选择收货仓库</option>
                            {warehouses.data?.items.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.code} · {item.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="text-sm">
                          查找其他供应商
                          <Input
                            aria-label="复核查找供应商"
                            maxLength={200}
                            value={supplierQ}
                            onChange={(e) => setSupplierQ(e.target.value)}
                          />
                        </label>
                        <label className="text-sm">
                          查找其他仓库
                          <Input
                            aria-label="复核查找仓库"
                            maxLength={200}
                            value={warehouseQ}
                            onChange={(e) => setWarehouseQ(e.target.value)}
                          />
                        </label>
                      </div>
                      <label className="block text-sm">
                        采购原因
                        <Input
                          aria-label="补货采购原因"
                          maxLength={500}
                          className="mt-1"
                          {...form.register("reason")}
                        />
                      </label>
                      <label className="block text-sm">
                        查找采购单位
                        <Input
                          aria-label="查找补货采购单位"
                          maxLength={200}
                          className="mt-1"
                          value={unitQ}
                          onChange={(e) => setUnitQ(e.target.value)}
                        />
                      </label>
                      {lines.fields.map((field, index) => (
                        <section
                          aria-label={`补货明细${index + 1}`}
                          key={field.id}
                          className="min-w-0 space-y-3 rounded-lg border p-3"
                        >
                          <h3 className="font-medium">
                            {selected.find(
                              (item) => item.product_id === field.product_id,
                            )?.name ?? "补货商品"}
                          </h3>
                          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            <label className="text-sm">
                              采购单位
                              <select
                                aria-label={`补货采购单位${index + 1}`}
                                className={selectClass + " mt-1 w-full"}
                                {...form.register(`lines.${index}.unit_id`)}
                              >
                                {!units.data?.items.some(
                                  (item) =>
                                    item.id === current.lines?.[index]?.unit_id,
                                ) && (
                                  <option
                                    value={current.lines?.[index]?.unit_id}
                                  >
                                    默认采购单位（预览核对）
                                  </option>
                                )}
                                {units.data?.items.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.code} · {item.name}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="text-sm">
                              采购数量（空白使用建议）
                              <Input
                                aria-label={`补货采购数量${index + 1}`}
                                inputMode="decimal"
                                className="mt-1"
                                {...form.register(`lines.${index}.qty`)}
                              />
                            </label>
                            <label className="text-sm">
                              手工数量调整原因
                              <Input
                                aria-label={`补货数量调整原因${index + 1}`}
                                maxLength={500}
                                className="mt-1"
                                {...form.register(
                                  `lines.${index}.quantity_reason`,
                                )}
                              />
                            </label>
                            <label className="text-sm">
                              单价依据
                              <select
                                aria-label={`补货单价依据${index + 1}`}
                                className={selectClass + " mt-1 w-full"}
                                {...form.register(
                                  `lines.${index}.price_source`,
                                )}
                              >
                                <option value="MANUAL">明确填写单价</option>
                                <option value="HISTORY">
                                  选用该供应商实际历史价
                                </option>
                              </select>
                            </label>
                            {current.lines?.[index]?.price_source !==
                              "HISTORY" && (
                              <label className="text-sm">
                                采购单价（空白待补，0为明确零价）
                                <Input
                                  aria-label={`补货采购单价${index + 1}`}
                                  inputMode="decimal"
                                  className="mt-1"
                                  {...form.register(
                                    `lines.${index}.unit_price`,
                                  )}
                                />
                              </label>
                            )}
                          </div>
                          <Button
                            type="button"
                            variant="outline"
                            disabled={lines.fields.length <= 1}
                            onClick={() => lines.remove(index)}
                          >
                            移除此补货行
                          </Button>
                        </section>
                      ))}
                      <p className="text-sm text-muted-foreground">
                        数量、金额与历史价格换算均由服务器计算。单位无法精确换算时，改选基础单位或明确填写数量与原因；不会猜测箱规或把缺价视为零。
                      </p>
                    </fieldset>
                    {[warehouses.error, units.error]
                      .filter(Boolean)
                      .map((error, index) => (
                        <p key={index} role="alert">
                          {error?.message}
                        </p>
                      ))}
                    <Button type="submit" disabled={locked || previewBusy}>
                      {previewBusy ? "正在核对…" : "预览采购草稿"}
                    </Button>
                  </form>
                  {previewError && <p role="alert">{previewError}</p>}
                  {review && !reviewed && (
                    <p role="status">选择或依据已变化，请重新预览并复核。</p>
                  )}
                  {reviewed && (
                    <section
                      aria-label="补货采购预览"
                      className="min-w-0 space-y-4 rounded-lg border p-3"
                    >
                      <Facts
                        values={[
                          ["供应商", reviewed.value.supplier_name],
                          ["收货仓库", reviewed.value.warehouse_name],
                          ["采购原因", reviewed.value.reason],
                          ["采购总额", reviewed.value.total_amount],
                          ["复核时点", reviewed.value.as_of],
                        ]}
                      />
                      {reviewed.value.blocking_reasons.map((reason, index) => (
                        <p role="alert" key={index}>
                          {reasonText(reason)}
                        </p>
                      ))}
                      {reviewed.value.lines.map((line) => (
                        <section
                          key={line.product_id}
                          className="min-w-0 space-y-3 border-t pt-3"
                        >
                          <h3 className="font-medium">{line.product_label}</h3>
                          <Facts
                            values={[
                              ["采购单位", line.unit_name],
                              ["换算率", line.unit_to_base_factor],
                              ["换算版本", line.conversion_version],
                              ["采购数量", line.qty],
                              ["基础数量", line.base_qty],
                              [
                                "数量依据",
                                line.quantity_source === "MANUAL"
                                  ? "手工调整"
                                  : "服务器建议",
                              ],
                              ["调整原因", line.quantity_reason],
                              ["采购单价", line.unit_price],
                              ["采购行金额", line.amount],
                            ]}
                          />
                          <details>
                            <summary className="cursor-pointer text-sm">
                              查看本行完整补货依据
                            </summary>
                            <div className="mt-3">
                              <Basis value={line.basis} />
                            </div>
                          </details>
                          {line.historical_price && (
                            <div className="space-y-2 rounded-md bg-muted p-3 text-sm">
                              <p>
                                实际历史采购价来源：
                                <Link
                                  className="underline"
                                  href={`/purchase?document=${line.historical_price.document_id}`}
                                >
                                  {line.historical_price.document_number}
                                </Link>
                              </p>
                              <Facts
                                values={[
                                  [
                                    "历史单位",
                                    line.historical_price.unit_label,
                                  ],
                                  [
                                    "历史单价",
                                    line.historical_price.unit_price,
                                  ],
                                  [
                                    "历史换算率",
                                    line.historical_price.unit_to_base_factor,
                                  ],
                                  [
                                    "历史换算版本",
                                    line.historical_price.conversion_version,
                                  ],
                                  [
                                    "转换后单价",
                                    line.historical_price.selected_unit_price,
                                  ],
                                  [
                                    "实际入库时间",
                                    line.historical_price.posted_at,
                                  ],
                                ]}
                              />
                            </div>
                          )}
                          {line.blocking_reasons.map((reason, index) => (
                            <p role="alert" key={index}>
                              {reasonText(reason)}
                            </p>
                          ))}
                          {line.warnings.map((warning, index) => (
                            <p key={index} className="text-sm">
                              {reasonText(warning)}
                            </p>
                          ))}
                        </section>
                      ))}
                      <Button
                        disabled={locked || !reviewed.value.can_create}
                        onClick={() => void save()}
                      >
                        确认创建采购草稿
                      </Button>
                    </section>
                  )}
                </>
              )}
              <Button
                variant="outline"
                disabled={locked}
                onClick={() => {
                  controller.current?.abort();
                  setOpen(false);
                }}
              >
                关闭采购复核
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
