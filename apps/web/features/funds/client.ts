import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { components } from "@/generated/api/schema";

export type Side = "AR" | "AP";
export type CashKind = "SETTLEMENT" | "REFUND";
export type Source = components["schemas"]["FundsSource"];
export type SourceDetail = components["schemas"]["FundsSourceDetail"];
export type Cash = components["schemas"]["FundsCash"];
export type CashDetail = components["schemas"]["FundsCashDetail"];
export type Party = components["schemas"]["FundsParty"];
export type Legacy = components["schemas"]["FundsLegacyDocument"];
export type Receipt = components["schemas"]["FundsReceipt"];
export type CashInput = components["schemas"]["FundsCashInput"];
export type CashPreview = components["schemas"]["FundsCashPreview"];
export type OpeningInput = components["schemas"]["FundsOpening"];
export type BindingInput = components["schemas"]["FundsLegacyBinding"];
export type ActivateInput = components["schemas"]["FundsActivate"];

function options(key: string) {
  return {
    headers: { "Idempotency-Key": key },
    params: { header: { "Idempotency-Key": key } },
  };
}

export async function readSource(id: string, signal?: AbortSignal) {
  return unwrap(
    await api.GET("/api/v1/funds/sources/{id}", {
      signal,
      params: { path: { id } },
    }),
  );
}

export async function readCash(id: string, signal?: AbortSignal) {
  return unwrap(
    await api.GET("/api/v1/funds/cash/{id}", {
      signal,
      params: { path: { id } },
    }),
  );
}

export async function activate(body: ActivateInput, key: string) {
  return unwrap(
    await api.POST("/api/v1/funds/activate", { ...options(key), body }),
  );
}

export async function opening(
  body: OpeningInput,
  adjustment: boolean,
  key: string,
) {
  return adjustment
    ? unwrap(
        await api.POST("/api/v1/funds/adjustments", { ...options(key), body }),
      )
    : unwrap(
        await api.POST("/api/v1/funds/openings", { ...options(key), body }),
      );
}

export async function bindLegacy(body: BindingInput, key: string) {
  return unwrap(
    await api.POST("/api/v1/funds/legacy-bindings", { ...options(key), body }),
  );
}

export async function recordCash(body: CashInput, key: string) {
  return unwrap(
    await api.POST("/api/v1/funds/cash", { ...options(key), body }),
  );
}

export async function previewCash(body: CashInput, signal?: AbortSignal) {
  return unwrap(await api.POST("/api/v1/funds/cash/preview", { body, signal }));
}

export async function reverse(
  id: string,
  cash: boolean,
  reason: string,
  key: string,
) {
  const request = options(key);
  const params = { ...request.params, path: { id } };
  return cash
    ? unwrap(
        await api.POST("/api/v1/funds/cash/{id}/reverse", {
          ...request,
          params,
          body: { reason },
        }),
      )
    : unwrap(
        await api.POST("/api/v1/funds/sources/{id}/reverse", {
          ...request,
          params,
          body: { reason },
        }),
      );
}
