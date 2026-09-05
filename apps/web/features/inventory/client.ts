import { api, ApiError } from "@/lib/api";
import type { components } from "@/generated/api/schema";
export type Document = components["schemas"]["InventoryDocumentRead"];
export type Draft = Omit<
  components["schemas"]["InventoryDraftUpdate"],
  "expected_version"
>;
export type Kind = Exclude<
  Document["type"],
  "PURCHASE_RECEIPT" | "PURCHASE_RETURN" | "SALES_RESERVATION" | "SALES_SHIPMENT" | "SALES_RETURN"
>;
export const labels: Record<Kind, string> = {
  OPENING: "期初库存",
  ADJUSTMENT: "库存调整",
  TRANSFER: "仓库调拨",
  STOCKTAKE: "库存盘点",
};
export const permissionsByKind: Record<Kind, string> = {
  OPENING: "inventory.opening",
  ADJUSTMENT: "inventory.adjust",
  TRANSFER: "inventory.transfer",
  STOCKTAKE: "inventory.stocktake",
};
export async function saveDraft(
  kind: Kind,
  body: Draft,
  key: string,
  doc?: Document,
) {
  const headers = { "Idempotency-Key": key };
  if (doc)
    return unwrap(
      await api.PUT("/api/v1/inventory/documents/{id}/draft", {
        params: { path: { id: doc.id }, header: headers },
        headers,
        body: { ...body, expected_version: doc.version },
      }),
    );
  const common = {
    warehouse_id: body.warehouse_id,
    reason: body.reason,
    lines: body.lines,
  };
  switch (kind) {
    case "OPENING":
      return unwrap(
        await api.POST("/api/v1/inventory/openings", {
          headers,
          params: { header: headers },
          body: common,
        }),
      );
    case "ADJUSTMENT":
      return unwrap(
        await api.POST("/api/v1/inventory/adjustments", {
          headers,
          params: { header: headers },
          body: common,
        }),
      );
    case "STOCKTAKE":
      return unwrap(
        await api.POST("/api/v1/inventory/stocktakes", {
          headers,
          params: { header: headers },
          body: common,
        }),
      );
    case "TRANSFER":
      if (!body.target_warehouse_id) throw new Error("请选择目标仓库");
      return unwrap(
        await api.POST("/api/v1/inventory/transfers", {
          headers,
          params: { header: headers },
          body: { ...common, target_warehouse_id: body.target_warehouse_id },
        }),
      );
  }
}
export async function changeDocument(
  doc: Document,
  action: "post" | "reverse" | "refresh",
  key: string,
  reason: string,
) {
  const headers = { "Idempotency-Key": key },
    params = { path: { id: doc.id }, header: headers },
    body = { expected_version: doc.version };
  if (action === "post")
    return unwrap(
      await api.POST("/api/v1/inventory/documents/{id}/post", {
        params,
        headers,
        body,
      }),
    );
  if (action === "reverse")
    return unwrap(
      await api.POST("/api/v1/inventory/documents/{id}/reverse", {
        params,
        headers,
        body: { ...body, reason },
      }),
    );
  return unwrap(
    await api.POST("/api/v1/inventory/stocktakes/{id}/refresh-baseline", {
      params,
      headers,
      body,
    }),
  );
}

export function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (!result.response.ok || result.data === undefined) {
    const error = result.error as
      components["schemas"]["ProblemDetails"] | undefined;
    throw new ApiError(
      result.response.status,
      error?.detail ?? "操作失败，请重试",
      error?.request_id,
    );
  }
  return result.data;
}

export const documentLabels: Record<Document["type"], string> = {
  ...labels,
  PURCHASE_RECEIPT: "采购收货",
  PURCHASE_RETURN: "采购退货",
  SALES_RESERVATION: "销售库存占用",
  SALES_SHIPMENT: "销售出库",
  SALES_RETURN: "销售退货",
};
