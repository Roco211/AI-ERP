import { api, ApiError } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { components } from "@/generated/api/schema";

export type Batch = components["schemas"]["BatchRead"];
export type ImportRow = components["schemas"]["RowRead"];
export type Resource = Batch["resource"];
export type Mode = Batch["mode"];
export type Receipt = components["schemas"]["BatchReceipt"];
export async function uploadPreview(
  file: File,
  resource: Resource,
  mode: Mode,
  worksheet: string,
  key: string,
) {
  return unwrap(
    await api.POST("/api/v1/catalog-imports/previews", {
      params: { header: { "Idempotency-Key": key } },
      body: { file, resource, mode, ...(worksheet ? { worksheet } : {}) },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file);
        form.append("resource", resource);
        form.append("mode", mode);
        if (worksheet) form.append("worksheet", worksheet);
        return form;
      },
    }),
  );
}
export async function confirmBatch(
  id: string,
  body: components["schemas"]["ConfirmInput"],
  key: string,
) {
  return unwrap(
    await api.POST("/api/v1/catalog-imports/{id}/confirm", {
      params: { path: { id }, header: { "Idempotency-Key": key } },
      body,
    }),
  );
}
export async function retryRows(
  id: string,
  body: components["schemas"]["RetryInput"],
  key: string,
) {
  return unwrap(
    await api.POST("/api/v1/catalog-imports/{id}/retry", {
      params: { path: { id }, header: { "Idempotency-Key": key } },
      body,
    }),
  );
}
export async function downloadImport(
  value: { resource: Resource } | { id: string; failedOnly: boolean },
  signal: AbortSignal,
) {
  const result =
    "resource" in value
      ? await api.GET("/api/v1/catalog-imports/templates/{resource}", {
          signal,
          params: { path: { resource: value.resource } },
          parseAs: "blob",
        })
      : await api.GET("/api/v1/catalog-imports/{id}/result.xlsx", {
          signal,
          params: {
            path: { id: value.id },
            query: { failed_only: value.failedOnly },
          },
          parseAs: "blob",
        });
  if (!result.response.ok || !(result.data instanceof Blob)) {
    const error = result.error as
      | components["schemas"]["ProblemDetails"]
      | undefined;
    throw new ApiError(
      result.response.status,
      error?.detail ?? "下载失败，请刷新后重试。",
      error?.request_id,
    );
  }
  return result.data;
}
