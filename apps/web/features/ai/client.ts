import { api } from "@/lib/api";
import { unwrap } from "@/features/inventory/client";
import type { paths } from "@/generated/api/schema";

export type ProviderSettingsRead =
  paths["/api/v1/ai/provider"]["get"]["responses"][200]["content"]["application/json"];
export type ProviderSettingsInput =
  paths["/api/v1/ai/provider"]["put"]["requestBody"]["content"]["application/json"];
export type ProviderConnectionInput =
  paths["/api/v1/ai/provider/test"]["post"]["requestBody"]["content"]["application/json"];

function metadata(value: ProviderSettingsRead): ProviderSettingsRead {
  return {
    name: value.name, base_url: value.base_url, model: value.model,
    enabled: value.enabled, allow_private_network: value.allow_private_network,
    key_set: value.key_set, version: value.version, updated_at: value.updated_at,
  };
}

export async function getProviderSettings(signal?: AbortSignal) {
  return metadata(unwrap(await api.GET("/api/v1/ai/provider", { signal })));
}

export async function saveProviderSettings(body: ProviderSettingsInput, key: string) {
  return metadata(unwrap(await api.PUT("/api/v1/ai/provider", {
    params: { header: { "Idempotency-Key": key } }, body,
  })));
}

export async function testProviderConnection(body: ProviderConnectionInput, key: string) {
  return unwrap(await api.POST("/api/v1/ai/provider/test", {
    params: { header: { "Idempotency-Key": key } }, body,
  }));
}
