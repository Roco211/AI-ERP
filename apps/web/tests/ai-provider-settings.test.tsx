import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi, type Mock } from "vitest";
import { ProviderSettings } from "@/features/ai/provider-settings";
import type { ProviderSettingsRead } from "@/features/ai/client";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() },
  ApiError: class extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

type Options = {
  body?: Record<string, unknown>;
  params?: { header?: { "Idempotency-Key": string } };
};
type Result = { data?: unknown; error?: unknown; response: Response };
const get = api.GET as unknown as Mock<(path: string, options?: Options) => Promise<Result>>;
const put = api.PUT as unknown as Mock<(path: string, options?: Options) => Promise<Result>>;
const post = api.POST as unknown as Mock<(path: string, options?: Options) => Promise<Result>>;
const ok = (data: unknown): Result => ({ data, response: new Response() });
const base: ProviderSettingsRead = {
  name: "CommandCode", base_url: "https://api.commandcode.ai/provider/v1",
  model: "deepseek-v4-flash-vision-exp", enabled: true, allow_private_network: false,
  key_set: true, version: 1, updated_at: null,
};
const permission = ["ai.provider.manage"];

function show(permissions = permission) {
  get.mockResolvedValue(ok(base));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let currentIdentity = "org-a:user-a";
  let currentPermissions = permissions;
  const node = () => <QueryClientProvider client={client}>
    <ProviderSettings permissions={currentPermissions} identityKey={currentIdentity} />
  </QueryClientProvider>;
  const view = render(node());
  return { ...view, client,
    permissions: (value: string[]) => { currentPermissions = value; view.rerender(node()); },
    identity: (value: string) => { currentIdentity = value; view.rerender(node()); },
  };
}

async function loaded() {
  return screen.findByRole("region", { name: "模型服务设置" });
}

afterEach(() => { cleanup(); vi.resetAllMocks(); });

test("provider management requires its own permission and never loads for other users", () => {
  show(["ai.use", "ai.draft.create"]);
  expect(screen.getByRole("alert")).toHaveTextContent("没有管理模型服务的权限");
  expect(get).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("服务密钥")).not.toBeInTheDocument();
});

test("blank secret keeps existing key and metadata never supplies a password", async () => {
  const forbiddenEcho = "server-extra-field-must-not-be-cached";
  get.mockResolvedValueOnce(ok({ ...base, api_key: forbiddenEcho }));
  put.mockResolvedValue(ok({ ...base, version: 2, api_key: forbiddenEcho }));
  const { client } = show();
  await loaded();
  const input = screen.getByLabelText("服务密钥");
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveValue("");
  expect(screen.getByText("已保存密钥，不会显示原文。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText("模型服务已保存。可以测试连接，或开始新对话。");
  expect(put.mock.calls[0][0]).toBe("/api/v1/ai/provider");
  expect(put.mock.calls[0][1]?.body).not.toHaveProperty("api_key");
  expect(put.mock.calls[0][1]?.body).toMatchObject({ expected_version: 1, clear_key: false });
  expect(input).toHaveValue("");
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(forbiddenEcho);
});

test("lost save response retries identical in-memory body and key without displaying or caching secrets", async () => {
  const secret = "test-only-provider-secret-DO-NOT-RENDER";
  put.mockRejectedValueOnce(new TypeError(secret));
  put.mockResolvedValueOnce(ok({ ...base, version: 2 }));
  const { client } = show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: secret } });
  const save = screen.getByRole("button", { name: "保存模型服务" });
  fireEvent.click(save);
  fireEvent.click(save);
  await screen.findByText(/提交结果待确认/);
  expect(put).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  expect(screen.getByLabelText("模型名称")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Ollama · 本机或服务器" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "允许连接本机或内网服务" })).toBeDisabled();
  expect(save).toBeDisabled();
  expect(screen.getByRole("alert")).not.toHaveTextContent(secret);
  const first = put.mock.calls[0][1];
  expect(first?.body?.api_key).toBe(secret);
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(secret);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
  await screen.findByText("模型服务已保存。可以测试连接，或开始新对话。");
  expect(put).toHaveBeenCalledTimes(2);
  expect(put.mock.calls[1][1]).toEqual(first);
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  expect(document.body.textContent).not.toContain(secret);
});

test("version conflict requires reload and cannot silently overwrite another settings version", async () => {
  put.mockResolvedValueOnce({ error: { detail: "conflict" }, response: new Response(null, { status: 409 }) });
  put.mockResolvedValueOnce(ok({ ...base, version: 4, model: "revised-model" }));
  show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: "temporary-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText(/设置已由其他操作更新/);
  expect(screen.getByRole("button", { name: "保存模型服务" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "测试已保存的连接" })).toBeDisabled();
  get.mockResolvedValue(ok({ ...base, model: "other-model", version: 3 }));
  fireEvent.click(screen.getByRole("button", { name: "重新读取设置" }));
  await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("other-model"));
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "revised-model" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
  expect(put.mock.calls[1][1]?.body?.expected_version).toBe(3);
  expect(put.mock.calls[1][1]?.body).not.toHaveProperty("api_key");
  expect(put.mock.calls[1][1]?.params).not.toEqual(put.mock.calls[0][1]?.params);
});

test("changing host requires a new secret or explicit clearing of old key", async () => {
  put.mockResolvedValue(ok({ ...base, key_set: false, base_url: "https://different.example/v1", version: 2 }));
  show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务地址"), { target: { value: "https://different.example/v1" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText(/更换服务地址后，请重新填写密钥/);
  expect(put).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText("清除已保存密钥"));
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText("尚未保存密钥。");
  expect(put.mock.calls[0][1]?.body).toMatchObject({ clear_key: true });
  expect(put.mock.calls[0][1]?.body).not.toHaveProperty("api_key");
});

test("connection test uses saved version only and is disabled while edits are unsaved", async () => {
  post.mockResolvedValue(ok({ ok: true, message: "ok", model: base.model, version: 1 }));
  show();
  await loaded();
  const testButton = screen.getByRole("button", { name: "测试已保存的连接" });
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "unsaved-model" } });
  expect(testButton).toBeDisabled();
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: base.model } });
  fireEvent.click(testButton);
  await screen.findByText("连接测试通过，可以开始新对话。");
  expect(post.mock.calls[0][0]).toBe("/api/v1/ai/provider/test");
  expect(post.mock.calls[0][1]?.body).toEqual({ expected_version: 1 });
  expect(post.mock.calls[0][1]?.params?.header?.["Idempotency-Key"]).toBeTruthy();
  expect(screen.getByText(/连接测试只发送固定测试文本/)).toBeInTheDocument();
});

test("presets fill only after selection and local model settings remain editable", async () => {
  show();
  await loaded();
  expect(screen.getByLabelText("服务地址")).toHaveValue(base.base_url);
  expect(screen.getByLabelText("允许连接本机或内网服务")).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Ollama · 本机或服务器" }));
  expect(screen.getByLabelText("服务地址")).toHaveValue("http://127.0.0.1:11438/v1");
  expect(screen.getByLabelText("允许连接本机或内网服务")).toBeChecked();
  expect(screen.getByLabelText("模型名称")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "my-installed-chat-model" } });
  expect(screen.getByLabelText("模型名称")).toHaveValue("my-installed-chat-model");
  expect(put).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

test("official configuration checkboxes update locally without an implicit save or connection test", async () => {
  show(); await loaded();
  const enabled = screen.getByRole("checkbox", { name: "启用对话模型" });
  expect(enabled).toBeChecked();
  fireEvent.click(enabled);
  expect(enabled).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "允许连接本机或内网服务" }));
  expect(screen.getByRole("checkbox", { name: "允许连接本机或内网服务" })).toBeChecked();
  expect(screen.getByText(/有未保存修改/)).toBeVisible();
  expect(put).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

test("revoked management permission removes form and its unsaved secret", async () => {
  const view = show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: "not-cached-secret" } });
  view.permissions([]);
  expect(screen.queryByLabelText("服务密钥")).not.toBeInTheDocument();
  view.permissions(permission);
  await loaded();
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  expect(put).not.toHaveBeenCalled();
});

test.each(["org-b:user-b", "org-a:user-b"])("identity change to %s clears unsaved secrets and isolates settings cache despite identical permissions", async (nextIdentity) => {
  const view = show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: "old-identity-unsaved-secret" } });
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "old-unsaved-model" } });
  let resolve!: (value: Result) => void;
  get.mockImplementationOnce(() => new Promise<Result>((done) => { resolve = done; }));
  view.identity(nextIdentity);
  expect(screen.queryByLabelText("服务密钥")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取模型服务设置");
  await act(async () => resolve(ok({ ...base, model: "new-identity-model", version: 7, key_set: false })));
  await loaded();
  expect(screen.getByLabelText("模型名称")).toHaveValue("new-identity-model");
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  await waitFor(() => expect(view.client.getQueryCache().getAll()).toHaveLength(1));
  expect(view.client.getQueryCache().getAll()[0].queryKey).toEqual(["ai", "provider-settings", nextIdentity + ":ai.provider.manage"]);
  expect(JSON.stringify(view.client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain("old-identity");
  expect(put).not.toHaveBeenCalled();
});

test("identity changes discard unresolved old submissions instead of replaying their credential under the new login", async () => {
  const next = { ...base, model: "second-user-model", version: 7 };
  put.mockRejectedValueOnce(new TypeError("network failure"));
  put.mockResolvedValueOnce(ok({ ...next, version: 8 }));
  const view = show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: "old-request-private-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText(/提交结果待确认/);
  expect(put).toHaveBeenCalledTimes(1);
  get.mockResolvedValue(ok(next));
  view.identity("org-a:user-b");
  await loaded();
  expect(screen.queryByRole("button", { name: "重试原提交" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  expect(screen.getByLabelText("模型名称")).toHaveValue("second-user-model");
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await screen.findByText("模型服务已保存。可以测试连接，或开始新对话。");
  expect(put).toHaveBeenCalledTimes(2);
  expect(put.mock.calls[1][1]?.body).toMatchObject({ expected_version: 7, model: "second-user-model" });
  expect(put.mock.calls[1][1]?.body).not.toHaveProperty("api_key");
  expect(put.mock.calls[1][1]?.params).not.toEqual(put.mock.calls[0][1]?.params);
});

test("a save response from the old identity cannot repopulate the new identity form or cache", async () => {
  let resolve!: (value: Result) => void;
  put.mockImplementationOnce(() => new Promise<Result>((done) => { resolve = done; }));
  const view = show();
  await loaded();
  fireEvent.change(screen.getByLabelText("服务密钥"), { target: { value: "old-inflight-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模型服务" }));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  get.mockResolvedValue(ok({ ...base, name: "新企业", model: "new-model", version: 3 }));
  view.identity("org-b:user-b");
  await loaded();
  await act(async () => resolve(ok({ ...base, model: "old-late-response", version: 2 })));
  expect(screen.getByLabelText("模型名称")).toHaveValue("new-model");
  expect(screen.getByLabelText("服务密钥")).toHaveValue("");
  expect(screen.queryByText("模型服务已保存。可以测试连接，或开始新对话。")).not.toBeInTheDocument();
  expect(JSON.stringify(view.client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain("old-late-response");
});
