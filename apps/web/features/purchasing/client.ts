import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { components } from "@/generated/api/schema";
export type Order = components["schemas"]["PurchaseOrderRead"];
export type StockDocument = components["schemas"]["PurchaseDocumentRead"];
export type OrderInput = components["schemas"]["PurchaseOrderInput"];
export type DocumentInput = components["schemas"]["PurchaseDocumentInput"];
export type Receipt = components["schemas"]["PurchaseReceipt"];
export type Action = "confirm" | "cancel" | "close" | "post" | "reverse";
const options = (key: string) => ({
  headers: { "Idempotency-Key": key },
  params: { header: { "Idempotency-Key": key } },
});
export async function saveOrder(
  body: OrderInput,
  key: string,
  previous?: Order,
) {
  const o = options(key);
  return previous
    ? unwrap(
        await api.PUT("/api/v1/purchasing/orders/{id}/draft", {
          ...o,
          params: { ...o.params, path: { id: previous.id } },
          body: { ...body, expected_version: previous.version },
        }),
      )
    : unwrap(await api.POST("/api/v1/purchasing/orders", { ...o, body }));
}
export async function saveDocument(
  kind: "RECEIPT" | "RETURN",
  body: DocumentInput,
  key: string,
  previous?: StockDocument,
) {
  const o = options(key);
  if (previous)
    return unwrap(
      await api.PUT("/api/v1/purchasing/documents/{id}/draft", {
        ...o,
        params: { ...o.params, path: { id: previous.id } },
        body: { ...body, expected_version: previous.version },
      }),
    );
  return kind === "RECEIPT"
    ? unwrap(await api.POST("/api/v1/purchasing/receipts", { ...o, body }))
    : unwrap(await api.POST("/api/v1/purchasing/returns", { ...o, body }));
}
export async function command(
  id: string,
  version: number,
  action: Action,
  reason: string,
  key: string,
) {
  const o = options(key),
    params = { ...o.params, path: { id } },
    body = { expected_version: version };
  if (action === "confirm")
    return unwrap(
      await api.POST("/api/v1/purchasing/orders/{id}/confirm", {
        ...o,
        params,
        body,
      }),
    );
  if (action === "cancel")
    return unwrap(
      await api.POST("/api/v1/purchasing/orders/{id}/cancel", {
        ...o,
        params,
        body: { ...body, reason },
      }),
    );
  if (action === "close")
    return unwrap(
      await api.POST("/api/v1/purchasing/orders/{id}/close", {
        ...o,
        params,
        body: { ...body, reason },
      }),
    );
  if (action === "post")
    return unwrap(
      await api.POST("/api/v1/purchasing/documents/{id}/post", {
        ...o,
        params,
        body,
      }),
    );
  return unwrap(
    await api.POST("/api/v1/purchasing/documents/{id}/reverse", {
      ...o,
      params,
      body: { ...body, reason },
    }),
  );
}
export async function getDetail(id: string, document: boolean) {
  return document
    ? unwrap(
        await api.GET("/api/v1/purchasing/documents/{id}", {
          params: { path: { id } },
        }),
      )
    : unwrap(
        await api.GET("/api/v1/purchasing/orders/{id}", {
          params: { path: { id } },
        }),
      );
}
