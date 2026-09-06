"use client";
import { BusinessStatus } from "@/features/operations/business-status";

import { useEffect, useRef, useState, type SetStateAction } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { Dialog } from "@base-ui/react/dialog";
import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import {
  resourceClients,
  unwrap as catalogUnwrap,
  type ViewRow,
} from "@/features/catalog/client";
import { configs } from "@/features/catalog/config";
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
  confirmBatch,
  downloadImport,
  retryRows,
  uploadPreview,
  type Batch,
  type ImportRow,
  type Mode,
  type Receipt,
  type Resource,
} from "./client";

const resources: Resource[] = [
  "categories",
  "brands",
  "units",
  "customers",
  "suppliers",
  "warehouses",
  "products",
  "product-units",
  "product-prices",
  "supplier-products",
];
const names: Record<string, string> = {
  PREVIEW_READY: "预览通过，待确认",
  PREVIEW_INVALID: "预览有误",
  QUEUED: "等待执行",
  RUNNING: "正在执行",
  COMPLETED: "全部完成",
  PARTIAL_FAILED: "部分失败",
  FAILED: "执行失败",
  BLOCKED: "授权阻断",
  EXPIRED: "正文已到期",
  READY: "待确认",
  INVALID: "预览有误",
  PENDING: "尚未执行",
  SUCCEEDED: "已成功",
  CREATE: "新增",
  UPDATE: "更新",
};
type UploadFields = { resource: Resource; mode: Mode; worksheet: string };
type Action =
  | { kind: "confirm"; batch: Batch }
  | { kind: "retry"; batch: Batch; rowIds: string[] };
function cells(values: ImportRow["raw_values"]) {
  if (!values) return <span>正文已到期</span>;
  return (
    <dl className="min-w-48 space-y-1">
      {Object.entries(values).map(([key, value]) => (
        <div
          key={key}
          className="grid grid-cols-[minmax(70px,1fr)_minmax(110px,2fr)] gap-2"
        >
          <dt className="break-words text-muted-foreground">{key}</dt>
          <dd className="break-all">
            {value === null ? "（空）" : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function ImportsWorkspace({ permissions }: { permissions: string[] }) {
  const params = useSearchParams();
  const qc = useQueryClient();
  const has = (permission: string) => permissions.includes(permission);
  const canRead = (resource: Resource) =>
    has("catalog.import.read") && has(configs[resource].permission + ".read");
  const canWrite = (resource: Resource) =>
    canRead(resource) &&
    has("catalog.import.write") &&
    has(configs[resource].permission + ".write");
  const permitted = resources.filter(canRead);
  const read = has("catalog.import.read") && permitted.length > 0;
  const submission = useOperationSubmission();
  const { locked } = submission;
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(params.get("batch") ?? "");
  const [rowPage, setRowPage] = useState(1);
  const [rowStatus, setRowStatus] = useState<ImportRow["status"] | "">("");
  const [rowSelection, updateRowSelection] = useState<{
    context: string;
    ids: string[];
  }>({ context: "", ids: [] });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [templateResource, setTemplateResource] = useState<Resource>(
    permitted[0] ?? "products",
  );
  const [record, setRecord] = useState<{
    resource: Resource;
    id: string;
  } | null>(null);
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const downloadController = useRef<AbortController | null>(null);
  const permissionKey = permissions.join("|");
  const form = useForm<UploadFields>({
    defaultValues: {
      resource: permitted.find(canWrite) ?? "products",
      mode: "CREATE_ONLY",
      worksheet: "",
    },
  });
  const uploadResource = useWatch({ control: form.control, name: "resource" });
  useEffect(() => {
    downloadController.current?.abort();
    return () => downloadController.current?.abort();
  }, [permissionKey]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (selected) url.searchParams.set("batch", selected);
    else url.searchParams.delete("batch");
    window.history.replaceState(
      window.history.state,
      "",
      url.pathname + url.search,
    );
  }, [selected]);
  const batches = useQuery({
    queryKey: ["imports", "batches", page, permissionKey],
    enabled: read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/catalog-imports", {
          signal,
          params: { query: { page, page_size: 25 } },
        }),
      ),
    refetchInterval: (query) =>
      !locked &&
      query.state.data?.items.some((batch) =>
        ["QUEUED", "RUNNING"].includes(batch.status),
      )
        ? 2000
        : false,
  });
  const batchQuery = useQuery({
    queryKey: ["imports", "batch", selected, permissionKey],
    enabled: read && !!selected,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/catalog-imports/{id}", {
          signal,
          params: { path: { id: selected } },
        }),
      ),
    refetchInterval: (query) =>
      !locked && ["QUEUED", "RUNNING"].includes(query.state.data?.status ?? "")
        ? 1500
        : false,
  });
  const batch =
    batchQuery.data?.id === selected && canRead(batchQuery.data.resource)
      ? batchQuery.data
      : null;
  const selectionContext = [
    selected,
    batch?.version,
    batch?.failed,
    batch?.succeeded,
    permissionKey,
  ].join("|");
  const selectedRows =
    rowSelection.context === selectionContext ? rowSelection.ids : [];
  function setSelectedRows(value: SetStateAction<string[]>) {
    updateRowSelection((previous) => ({
      context: selectionContext,
      ids:
        typeof value === "function"
          ? value(previous.context === selectionContext ? previous.ids : [])
          : value,
    }));
  }
  const rows = useQuery({
    queryKey: [
      "imports",
      "rows",
      selected,
      rowPage,
      rowStatus,
      batch?.status,
      batch?.succeeded,
      batch?.failed,
      batch?.pending,
      permissionKey,
    ],
    enabled: !!batch && read,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/api/v1/catalog-imports/{id}/rows", {
          signal,
          params: {
            path: { id: selected },
            query: {
              page: rowPage,
              page_size: 25,
              status: rowStatus || undefined,
            },
          },
        }),
      ),
  });
  const original = useQuery({
    queryKey: [
      "imports",
      "original",
      record?.resource,
      record?.id,
      permissionKey,
    ],
    enabled: !!record && canRead(record.resource),
    queryFn: async () =>
      catalogUnwrap<ViewRow>(
        await resourceClients[record!.resource].get(record!.id),
      ),
  });
  const activeAction =
    action &&
    action.batch.id === selected &&
    canWrite(action.batch.resource) &&
    action.batch.is_creator &&
    batch?.version === action.batch.version &&
    (action.kind === "confirm" ? batch.can_confirm : batch.can_retry);
  function selectBatch(id: string) {
    if (submission.isLocked()) return;
    setSelected(id);
    setRowPage(1);
    setRowStatus("");
    setSelectedRows([]);
    setRecord(null);
  }
  async function afterReceipt(receipt: Receipt) {
    setUploadOpen(false);
    setAction(null);
    setSelected(receipt.id);
    setRowPage(1);
    setSelectedRows([]);
    setFile(null);
    await qc.invalidateQueries({ queryKey: ["imports"] });
  }
  async function refresh() {
    if (submission.isLocked()) return;
    setAction(null);
    setSelectedRows([]);
    submission.clearConflict();
    await qc.invalidateQueries({ queryKey: ["imports"] });
  }
  async function upload(values: UploadFields) {
    if (submission.isLocked()) return;
    if (!canWrite(values.resource)) {
      submission.setError("当前无权导入该资料。");
      return;
    }
    if (
      !file ||
      !/\.xlsx$/i.test(file.name) ||
      file.size > 10000000 ||
      !z.string().max(128).safeParse(values.worksheet).success
    ) {
      submission.setError("请选择不超过10MB的xlsx文件，并填写有效工作表名。");
      return;
    }
    const capturedFile = file,
      captured = { ...values, worksheet: values.worksheet.trim() };
    await submission.submit(
      (key) =>
        uploadPreview(
          capturedFile,
          captured.resource,
          captured.mode,
          captured.worksheet,
          key,
        ),
      afterReceipt,
    );
  }
  async function executeAction() {
    if (submission.isLocked() || !action) return;
    if (!activeAction) {
      submission.setError("当前没有确认或重试该批次的权限。");
      return;
    }
    const current = action.batch;
    if (action.kind === "confirm") {
      const body = {
        expected_version: current.version,
        preview_hash: current.preview_hash,
      };
      await submission.submit(
        (key) => confirmBatch(current.id, body, key),
        afterReceipt,
      );
    } else {
      const body = {
        expected_version: current.version,
        row_ids: [...action.rowIds],
      };
      await submission.submit(
        (key) => retryRows(current.id, body, key),
        afterReceipt,
      );
    }
  }
  async function download(
    value: { resource: Resource } | { id: string; failedOnly: boolean },
    resource: Resource,
  ) {
    if (submission.isLocked() || !canRead(resource)) return;
    downloadController.current?.abort();
    const controller = new AbortController();
    downloadController.current = controller;
    setDownloading(true);
    setDownloadError("");
    try {
      const blob = await downloadImport(value, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download =
        "resource" in value
          ? `${value.resource}-template.xlsx`
          : value.failedOnly
            ? "导入失败行.xlsx"
            : "导入结果.xlsx";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (!controller.signal.aborted)
        setDownloadError(error instanceof Error ? error.message : "下载失败。");
    } finally {
      if (downloadController.current === controller) setDownloading(false);
    }
  }
  return (
    <section aria-label="资料导入工作台" className="mt-8 min-w-0 space-y-5">
      {!read ? (
        <p role="alert">当前没有查看资料导入的权限。</p>
      ) : (
        <>
          <div className="flex min-w-0 flex-wrap items-center gap-3 rounded-2xl border border-border bg-card/25 p-4">
            <label className="text-sm">
              模板类型
              <select
                aria-label="导入模板类型"
                className={selectClass + " ml-2"}
                value={
                  canRead(templateResource) ? templateResource : permitted[0]
                }
                disabled={locked}
                onChange={(e) =>
                  setTemplateResource(e.target.value as Resource)
                }
              >
                {permitted.map((resource) => (
                  <option key={resource} value={resource}>
                    {configs[resource].title}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="outline"
              disabled={locked || downloading}
              onClick={() =>
                void download(
                  {
                    resource: canRead(templateResource)
                      ? templateResource
                      : permitted[0],
                  },
                  canRead(templateResource) ? templateResource : permitted[0],
                )
              }
            >
              下载导入模板
            </Button>
            {resources.some(canWrite) && (
              <Button
                disabled={locked}
                onClick={() => {
                  if (submission.isLocked()) return;
                  form.reset({
                    resource: permitted.find(canWrite)!,
                    mode: "CREATE_ONLY",
                    worksheet: "",
                  });
                  setFile(null);
                  setUploadOpen(true);
                  submission.setError("");
                }}
              >
                上传资料
              </Button>
            )}
            <Button
              variant="outline"
              disabled={locked}
              onClick={() => void refresh()}
            >
              刷新导入记录
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            一批只导入一种资料。先下载模板、填写文本编码，再预览核对。系统逐行保存，已成功的资料不会因其他行失败而回滚。
          </p>
          {!uploadOpen && !action && (
            <SubmissionFeedback
              value={submission}
              onRefresh={() => void refresh()}
            />
          )}
          {downloadError && <p role="alert">{downloadError}</p>}
          {batches.isPending && <p role="status">正在读取导入记录…</p>}
          {batches.error && <p role="alert">{batches.error.message}</p>}
          {batches.data && !batches.error && (
            <>
              <OperationsTable
                headers={["文件 / 资料", "工作表", "状态", "进度", "操作"]}
                rows={batches.data.items
                  .filter((item) => canRead(item.resource))
                  .map((item) => ({
                    id: item.id,
                    cells: [
                      <div key="file" className="max-w-64 break-words">
                        {item.filename}
                        <p className="text-muted-foreground">
                          {configs[item.resource].title}
                        </p>
                      </div>,
                      item.worksheet,
                      <BusinessStatus key="status" status={item.status}>{names[item.status]}</BusinessStatus>,
                      `成功 ${item.succeeded} / 共 ${item.total} · 失败 ${item.failed}`,
                      <Button
                        key="open"
                        variant="outline"
                        disabled={locked}
                        onClick={() => selectBatch(item.id)}
                      >
                        查看批次
                      </Button>,
                    ],
                  }))}
              />
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="outline"
                  disabled={locked || page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  上一页
                </Button>
                <span className="text-sm">
                  第 {page} 页 · 共 {batches.data.total} 批
                </span>
                <Button
                  variant="outline"
                  disabled={locked || page * 25 >= batches.data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页
                </Button>
              </div>
            </>
          )}
          {selected && batchQuery.isPending && (
            <p role="status">正在读取导入批次…</p>
          )}
          {selected && batchQuery.error && (
            <p role="alert">{batchQuery.error.message}</p>
          )}
          {batch && !batchQuery.error && (
            <section
              aria-label="导入批次详情"
              className="min-w-0 space-y-4 rounded-2xl border bg-background p-4 sm:p-6"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="break-all text-lg font-medium">
                  {batch.filename}
                </h2>
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={() => selectBatch("")}
                >
                  关闭批次
                </Button>
              </div>
              <Facts
                values={[
                  ["资料", configs[batch.resource].title],
                  ["工作表", batch.worksheet],
                  ["状态", names[batch.status]],
                  ["全部行", batch.total],
                  ["成功行", batch.succeeded],
                  ["失败行", batch.failed],
                  ["待确认行", batch.ready],
                  ["预览错误行", batch.invalid],
                  ["尚未执行行", batch.pending],
                ]}
              />
              <p className="text-sm text-muted-foreground">
                正文保留至 {batch.expires_at}
                。之后仅保留摘要与成功来源，不能继续执行或下载原文。
              </p>
              {!batch.body_available && (
                <p role="status">预览正文已到期，成功资料和来源仍然保留。</p>
              )}
              {!batch.is_creator && (
                <p className="text-sm">只有批次创建者可确认和发起重试。</p>
              )}
              <div className="flex flex-wrap gap-2">
                {batch.can_confirm &&
                  batch.is_creator &&
                  canWrite(batch.resource) && (
                    <Button
                      disabled={locked}
                      onClick={() => setAction({ kind: "confirm", batch })}
                    >
                      确认执行导入
                    </Button>
                  )}
                {batch.can_retry &&
                  batch.is_creator &&
                  canWrite(batch.resource) && (
                    <Button
                      disabled={locked || selectedRows.length === 0}
                      onClick={() =>
                        setAction({
                          kind: "retry",
                          batch,
                          rowIds: [...selectedRows],
                        })
                      }
                    >
                      重试所选失败行
                    </Button>
                  )}
                {batch.body_available && (
                  <>
                    <Button
                      variant="outline"
                      disabled={locked || downloading}
                      onClick={() =>
                        void download(
                          { id: batch.id, failedOnly: false },
                          batch.resource,
                        )
                      }
                    >
                      下载完整结果
                    </Button>
                    {(batch.failed > 0 || batch.invalid > 0) && (
                      <Button
                        variant="outline"
                        disabled={locked || downloading}
                        onClick={() =>
                          void download(
                            { id: batch.id, failedOnly: true },
                            batch.resource,
                          )
                        }
                      >
                        下载失败行
                      </Button>
                    )}
                  </>
                )}
              </div>
              <p className="text-sm">
                成功行不可重试。版本或引用冲突需要下载失败行、核对后重新预览；尚未执行行由原批次恢复。
              </p>
              <label className="block text-sm">
                筛选行状态
                <select
                  aria-label="导入行状态"
                  className={selectClass + " ml-2"}
                  value={rowStatus}
                  disabled={locked}
                  onChange={(e) => {
                    setRowStatus(e.target.value as ImportRow["status"] | "");
                    setRowPage(1);
                  }}
                >
                  {[
                    "",
                    "READY",
                    "INVALID",
                    "PENDING",
                    "SUCCEEDED",
                    "FAILED",
                  ].map((status) => (
                    <option key={status} value={status}>
                      {status ? names[status] : "全部行"}
                    </option>
                  ))}
                </select>
              </label>
              {rows.isPending && <p role="status">正在读取导入行…</p>}
              {rows.error && <p role="alert">{rows.error.message}</p>}
              {rows.data && !rows.error && (
                <>
                  <OperationsTable
                    headers={[
                      "选择 / 行号",
                      "动作 / 状态",
                      "原值",
                      "清洗与最终值",
                      "错误 / 来源",
                    ]}
                    rows={rows.data.items.map((row) => ({
                      id: row.id,
                      cells: [
                        <div
                          key="selection"
                          className="flex items-center gap-2"
                        >
                          {batch.can_retry &&
                            batch.is_creator &&
                            canWrite(batch.resource) &&
                            row.status === "FAILED" &&
                            row.retryable && (
                              <input
                                type="checkbox"
                                aria-label={`重试第${row.row_no}行`}
                                checked={selectedRows.includes(row.id)}
                                disabled={locked}
                                onChange={(e) =>
                                  setSelectedRows((prior) =>
                                    e.target.checked
                                      ? [...prior, row.id]
                                      : prior.filter((id) => id !== row.id),
                                  )
                                }
                              />
                            )}
                          第 {row.row_no} 行
                        </div>,
                        <div key="status">
                          {names[row.action]} · {names[row.status]}
                          <p className="text-muted-foreground">
                            尝试 {row.attempts} 次
                          </p>
                        </div>,
                        cells(row.raw_values),
                        cells(row.cleaned_values),
                        <div key="errors" className="min-w-48 space-y-2">
                          {row.errors?.map((error, index) => (
                            <p key={index} className="break-words">
                              {error.column}：{error.message}
                              <span className="block text-xs text-muted-foreground">
                                {error.code}
                              </span>
                            </p>
                          ))}
                          {row.status === "SUCCEEDED" && row.target_id && (
                            <Button
                              variant="outline"
                              disabled={locked}
                              onClick={() =>
                                setRecord({
                                  resource: batch.resource,
                                  id: row.target_id!,
                                })
                              }
                            >
                              查看已保存资料
                            </Button>
                          )}
                          {row.status === "FAILED" && !row.retryable && (
                            <p>需要修正后重新预览。</p>
                          )}
                        </div>,
                      ],
                    }))}
                  />
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="outline"
                      disabled={locked || rowPage <= 1}
                      onClick={() => setRowPage((p) => p - 1)}
                    >
                      上一页导入行
                    </Button>
                    <span className="text-sm">
                      第 {rowPage} 页 · 共 {rows.data.total} 行
                    </span>
                    <Button
                      variant="outline"
                      disabled={locked || rowPage * 25 >= rows.data.total}
                      onClick={() => setRowPage((p) => p + 1)}
                    >
                      下一页导入行
                    </Button>
                  </div>
                </>
              )}
            </section>
          )}
        </>
      )}
      <Dialog.Root
        open={uploadOpen || !!action}
        onOpenChange={(open) => {
          if (!open && !submission.isLocked()) {
            setUploadOpen(false);
            setAction(null);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[88vh] w-[min(96vw,720px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[30px] bg-background p-4 sm:p-6 border border-border">
            <Dialog.Title className="text-lg font-medium">
              {uploadOpen
                ? "上传并预览资料"
                : action?.kind === "confirm"
                  ? "确认导入批次"
                  : "重试失败行"}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-muted-foreground">
              先预览核对，确认后按行执行。不会自动覆盖已有编码，也不会把空价格改成零。
            </Dialog.Description>
            <div className="mt-4 space-y-4">
              <SubmissionFeedback
                value={submission}
                onRefresh={() => void refresh()}
              />
              {uploadOpen ? (
                read ? (
                  <form
                    className="space-y-4"
                    onSubmit={form.handleSubmit(upload)}
                  >
                    <fieldset
                      disabled={locked || !canWrite(uploadResource)}
                      className="min-w-0 space-y-4"
                    >
                      <label className="block text-sm">
                        资料类型
                        <select
                          aria-label="上传资料类型"
                          className={selectClass + " mt-1 block w-full"}
                          {...form.register("resource")}
                        >
                          {permitted.filter(canWrite).map((resource) => (
                            <option value={resource} key={resource}>
                              {configs[resource].title}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-sm">
                        处理已有资料
                        <select
                          aria-label="资料导入模式"
                          className={selectClass + " mt-1 block w-full"}
                          {...form.register("mode")}
                        >
                          <option value="CREATE_ONLY">
                            只新增，不覆盖已有资料
                          </option>
                          <option value="UPDATE_EXISTING">
                            明确更新已有资料，重新预览版本
                          </option>
                        </select>
                      </label>
                      <label className="block text-sm">
                        Excel 文件
                        <Input
                          type="file"
                          aria-label="资料Excel文件"
                          accept=".xlsx"
                          className="mt-1"
                          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                        />
                      </label>
                      <label className="block text-sm">
                        工作表名称（只有一个数据表时可留空）
                        <Input
                          aria-label="资料工作表名称"
                          maxLength={128}
                          className="mt-1"
                          {...form.register("worksheet")}
                        />
                      </label>
                      <p className="text-sm text-muted-foreground">
                        最多10MB、10,000行。编码与条码请设置为文本；禁止公式、宏或外部链接。不同资源和新增引用需分批导入。
                      </p>
                    </fieldset>
                    {!canWrite(uploadResource) && (
                      <p role="alert">当前无权导入所选资料。</p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        disabled={locked}
                        onClick={() => setUploadOpen(false)}
                      >
                        取消
                      </Button>
                      <Button
                        type="submit"
                        disabled={locked || !canWrite(uploadResource)}
                      >
                        生成导入预览
                      </Button>
                    </div>
                  </form>
                ) : (
                  <p role="alert">当前已无权读取或导入资料。</p>
                )
              ) : (
                action && (
                  <>
                    {canRead(action.batch.resource) && (
                      <>
                        <p className="break-all text-sm">
                          {action.batch.filename} ·{" "}
                          {configs[action.batch.resource].title}
                        </p>
                        <p className="text-sm">
                          {action.kind === "confirm"
                            ? `确认执行 ${action.batch.total} 行资料。`
                            : `仅重试所选 ${action.rowIds.length} 行失败记录。`}{" "}
                          已成功行不会再次执行。
                        </p>
                      </>
                    )}
                    <p className="text-sm">
                      任务在后台执行，可从批次列表或当前地址恢复查看。可能部分完成，请逐行核对结果。
                    </p>
                    {!activeAction && (
                      <p role="alert">
                        批次状态或当前权限已变化，请刷新后重新核对。
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        disabled={locked}
                        onClick={() => setAction(null)}
                      >
                        取消
                      </Button>
                      <Button
                        disabled={locked || !activeAction}
                        onClick={() => void executeAction()}
                      >
                        确认执行
                      </Button>
                    </div>
                  </>
                )
              )}
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        open={record !== null}
        onOpenChange={(open) => {
          if (!open) setRecord(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 max-h-[88vh] w-[min(96vw,750px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[30px] bg-background p-4 sm:p-6 border border-border">
            <Dialog.Title className="text-lg font-medium">
              已保存的资料
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm">
              按本次成功行的资料标识读取当前记录；后续更新会体现在这里。
            </Dialog.Description>
            {record && canRead(record.resource) ? (
              <div className="mt-4 space-y-4">
                {original.isPending && <p role="status">正在读取已保存资料…</p>}
                {original.error && <p role="alert">{original.error.message}</p>}
                {original.data && !original.error && (
                  <Facts
                    values={configs[record.resource].fields.map((field) => [
                      field.label,
                      typeof original.data[field.key] === "object"
                        ? JSON.stringify(original.data[field.key])
                        : String(original.data[field.key] ?? "—"),
                    ])}
                  />
                )}
              </div>
            ) : (
              <p role="alert">当前已无权查看该资料。</p>
            )}
            <Button
              className="mt-4"
              variant="outline"
              onClick={() => setRecord(null)}
            >
              关闭资料
            </Button>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
