import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { components } from "@/generated/api/schema";
export type Suggestion = components["schemas"]["Suggestion"];
export type PreviewInput = components["schemas"]["PurchasePreviewInput"];
export type Preview = components["schemas"]["PurchasePreview"];
export type Receipt = components["schemas"]["ReplenishmentReceipt"];
export async function previewPurchase(body: PreviewInput, signal: AbortSignal) {
  return unwrap(
    await api.POST("/api/v1/replenishment/purchase-preview", { body, signal }),
  );
}
export async function createPurchase(
  body: PreviewInput & { confirmation_hash: string },
  key: string,
) {
  return unwrap(
    await api.POST("/api/v1/replenishment/purchase-orders", {
      body,
      params: { header: { "Idempotency-Key": key } },
    }),
  );
}
