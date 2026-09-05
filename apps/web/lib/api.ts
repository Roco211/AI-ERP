import createClient from "openapi-fetch";
import type { paths } from "@/generated/api/schema";

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string) {
    super(message);
  }
}

export async function getProfile() {
  const { data, error, response } = await api.GET("/api/v1/auth/me");
  if (error || !data) throw new ApiError(response.status,
    error?.detail ?? "暂时无法连接，请稍后重试。", error?.request_id);
  return data;
}
