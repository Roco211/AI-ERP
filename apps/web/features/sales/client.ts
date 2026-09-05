import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { components } from "@/generated/api/schema";
export type Order = components["schemas"]["SalesOrderRead"];
export type StockDocument = components["schemas"]["SalesDocumentRead"];
export type OrderInput = components["schemas"]["SalesOrderInput"];
export type DocumentInput = components["schemas"]["SalesShipmentInput"];
export type Receipt =
  | components["schemas"]["SalesReceipt"]
  | components["schemas"]["SalesDocumentReceipt"];
export type PriceQuote = components["schemas"]["PriceQuote"];
export type PriceSource = components["schemas"]["PriceSource"];
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
        await api.PUT("/api/v1/sales/orders/{id}/draft", {
          ...o,
          params: { ...o.params, path: { id: previous.id } },
          body: { ...body, expected_version: previous.version },
        }),
      )
    : unwrap(await api.POST("/api/v1/sales/orders", { ...o, body }));
}
export async function saveDocument(
  kind: "SHIPMENT" | "RETURN",
  body: DocumentInput,
  key: string,
  previous?: StockDocument,
) {
  const o = options(key);
  if (previous)
    return unwrap(
      await api.PUT("/api/v1/sales/documents/{id}/draft", {
        ...o,
        params: { ...o.params, path: { id: previous.id } },
        body: { ...body, expected_version: previous.version },
      }),
    );
  return kind === "SHIPMENT"
    ? unwrap(await api.POST("/api/v1/sales/shipments", { ...o, body }))
    : unwrap(await api.POST("/api/v1/sales/returns", { ...o, body }));
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
      await api.POST("/api/v1/sales/orders/{id}/confirm", {
        ...o,
        params,
        body,
      }),
    );
  if (action === "cancel")
    return unwrap(
      await api.POST("/api/v1/sales/orders/{id}/cancel", {
        ...o,
        params,
        body: { ...body, reason },
      }),
    );
  if (action === "close")
    return unwrap(
      await api.POST("/api/v1/sales/orders/{id}/close", {
        ...o,
        params,
        body: { ...body, reason },
      }),
    );
  if (action === "post")
    return unwrap(
      await api.POST("/api/v1/sales/documents/{id}/post", {
        ...o,
        params,
        body,
      }),
    );
  return unwrap(
    await api.POST("/api/v1/sales/documents/{id}/reverse", {
      ...o,
      params,
      body: { ...body, reason },
    }),
  );
}
export async function getDetail(
  id: string,
  document: boolean,
  signal?: AbortSignal,
) {
  return document
    ? unwrap(
        await api.GET("/api/v1/sales/documents/{id}", {
          signal,
          params: { path: { id } },
        }),
      )
    : unwrap(
        await api.GET("/api/v1/sales/orders/{id}", {
          signal,
          params: { path: { id } },
        }),
      );
}

export async function quote(
  customer: string,
  product: string,
  unit: string,
  signal?: AbortSignal,
): Promise<PriceQuote> {
  return unwrap(
    await api.GET("/api/v1/sales/price-quote", {
      signal,
      params: {
        query: { customer_id: customer, product_id: product, unit_id: unit },
      },
    }),
  );
}
