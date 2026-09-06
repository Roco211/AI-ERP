"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Bot, Check, KeyRound, Plug, RefreshCw, Server, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/motion/checkbox";
import { ApiError } from "@/lib/api";
import { usePendingNavigationGuard } from "@/features/sales/navigation";
import {
  getProviderSettings,
  saveProviderSettings,
  testProviderConnection,
  type ProviderSettingsInput,
  type ProviderSettingsRead,
  type ProviderConnectionInput,
} from "./client";

const presets = {
  commandcode: {
    name: "CommandCode", base_url: "https://api.commandcode.ai/provider/v1",
    model: "deepseek-v4-flash-vision-exp", allow_private_network: false,
  },
  deepseek: {
    name: "DeepSeek", base_url: "https://api.deepseek.com/v1",
    model: "", allow_private_network: false,
  },
  openai: {
    name: "OpenAI", base_url: "https://api.openai.com/v1",
    model: "", allow_private_network: false,
  },
  ollama: {
    name: "Ollama", base_url: "http://127.0.0.1:11438/v1",
    model: "", allow_private_network: true,
  },
};

type Fields = Pick<ProviderSettingsRead,
  "name" | "base_url" | "model" | "enabled" | "allow_private_network">;
type Pending = ({ kind: "save"; body: ProviderSettingsInput } |
  { kind: "test"; body: ProviderConnectionInput }) & { key: string; uncertain: boolean };

function fieldsOf(value: ProviderSettingsRead): Fields {
  return {
    name: value.name, base_url: value.base_url, model: value.model,
    enabled: value.enabled, allow_private_network: value.allow_private_network,
  };
}

function origin(value: string): string | null {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.origin : null;
  } catch {
    return null;
  }
}

export function ProviderSettings({ permissions, identityKey = "current" }: {
  permissions: string[]; identityKey?: string;
}) {
  if (!permissions.includes("ai.provider.manage")) {
    return <p role="alert" className="mt-8 text-sm text-muted-foreground">
      你没有管理模型服务的权限，请联系企业管理员。
    </p>;
  }
  const scope = identityKey + ":" + permissions.slice().sort().join("|");
  return <AuthorizedProviderSettings key={scope} scope={scope} />;
}

function AuthorizedProviderSettings({ scope }: { scope: string }) {
  const qc = useQueryClient();
  const queryKey = ["ai", "provider-settings", scope];
  const query = useQuery({
    queryKey, queryFn: ({ signal }) => getProviderSettings(signal),
    refetchOnWindowFocus: false, refetchOnReconnect: false, gcTime: 0,
  });
  if (query.isPending) return <p role="status" className="mt-8 text-sm">正在读取模型服务设置…</p>;
  if (query.error || !query.data) return <div className="mt-8 space-y-3">
    <p role="alert">暂时无法读取模型服务设置，请重试。</p>
    <Button onClick={() => query.refetch()}>重新读取设置</Button>
  </div>;
  return <ProviderForm initial={query.data}
    onSaved={(value) => qc.setQueryData(queryKey, value)}
    reload={async () => {
      const result = await query.refetch();
      if (result.error || !result.data) throw new Error("provider reload failed");
      return result.data;
    }} />;
}

function ProviderForm({ initial, onSaved, reload }: {
  initial: ProviderSettingsRead;
  onSaved: (value: ProviderSettingsRead) => void;
  reload: () => Promise<ProviderSettingsRead>;
}) {
  const [saved, setSaved] = useState(initial);
  const [fields, setFields] = useState(() => fieldsOf(initial));
  const [secret, setSecret] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [conflict, setConflict] = useState(false);
  const pending = useRef<Pending | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const locked = busy || uncertain;
  const dirty = JSON.stringify(fields) !== JSON.stringify(fieldsOf(saved)) || !!secret || clearKey;
  usePendingNavigationGuard(locked);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; pending.current = null; };
  }, []);

  function accept(value: ProviderSettingsRead) {
    setSaved(value);
    setFields(fieldsOf(value));
    setSecret("");
    setClearKey(false);
    onSaved(value);
  }

  async function execute() {
    if (inFlight.current || !pending.current) return;
    const request = pending.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (request.kind === "save") {
        const result = await saveProviderSettings(request.body, request.key);
        if (!mounted.current) return;
        accept(result);
        setNotice("模型服务已保存。可以测试连接，或开始新对话。");
      } else {
        const result = await testProviderConnection(request.body, request.key);
        if (!mounted.current) return;
        setNotice(result.ok
          ? "连接测试通过，可以开始新对话。"
          : "连接测试未通过，请检查服务地址、模型名称及密钥。");
      }
      pending.current = null;
      setUncertain(false);
      setConflict(false);
    } catch (failure) {
      if (!mounted.current) return;
      const rejected = failure instanceof ApiError && failure.status >= 400 && failure.status < 500;
      if (!rejected) request.uncertain = true;
      if (rejected && !request.uncertain) {
        pending.current = null;
        setConflict(failure.status === 409);
        setError(failure.status === 409
          ? "设置已由其他操作更新，请重新读取设置后再修改。"
          : failure.status === 401 || failure.status === 403
            ? "当前账户不能完成此操作，请检查登录状态与管理权限。"
            : request.kind === "test"
              ? "连接测试未通过，请检查已保存的设置。"
              : "设置未保存，请检查填写内容；如需更换密钥，请重新填写。");
      } else {
        setError("提交结果待确认，请重试原提交。重试会沿用本次填写内容。");
      }
      setUncertain(pending.current !== null);
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || inFlight.current || conflict) return;
    setError("");
    if (!origin(fields.base_url)) {
      setError("请填写完整的服务地址，例如 https://example.com/v1。");
      return;
    }
    if (saved.key_set && origin(fields.base_url) !== origin(saved.base_url) && !secret.trim() && !clearKey) {
      setError("更换服务地址后，请重新填写密钥，或勾选清除已保存密钥。");
      return;
    }
    pending.current = {
      kind: "save", key: crypto.randomUUID(), uncertain: false,
      body: {
        ...fields, expected_version: saved.version,
        ...(secret.trim() ? { api_key: secret.trim() } : {}), clear_key: clearKey,
      },
    };
    // The original request is retained only in this mounted form's private ref.
    // No mutation cache or browser storage receives the credential or request key.
    setSecret("");
    void execute();
  }

  function testConnection() {
    if (pending.current || inFlight.current || dirty || conflict || !saved.enabled) return;
    pending.current = {
      kind: "test", key: crypto.randomUUID(), uncertain: false,
      body: { expected_version: saved.version },
    };
    void execute();
  }

  async function refresh() {
    if (pending.current || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setSecret("");
    try {
      const value = await reload();
      if (!mounted.current) return;
      accept(value);
      setConflict(false);
      setNotice("已读取最新设置，请重新核对后保存。");
    } catch {
      if (mounted.current) setError("暂时无法读取最新设置，请重试。");
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return <section className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_280px]" aria-label="模型服务设置">
    <div className="min-w-0 overflow-hidden rounded-3xl border border-border/70 bg-background">
      <header className="flex flex-wrap items-start gap-3 border-b border-border/60 p-5 sm:p-6">
        <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-muted"><Bot className="size-5" aria-hidden /></span>
        <div className="min-w-0 flex-1"><h2 className="text-lg font-medium tracking-tight">企业对话模型</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">对话所需资料会发送到所选服务，设置供当前企业使用。</p></div>
        <span className="rounded-full border border-border/70 px-3 py-1 text-xs text-muted-foreground">{saved.enabled ? "已启用" : "未启用"}{dirty ? " · 有未保存修改" : ""}</span>
      </header>
      <form onSubmit={save} className="space-y-6 p-5 sm:p-6">
        <fieldset disabled={locked || conflict} className="min-w-0 space-y-6">
          <div role="group" aria-label="服务商预设" className="space-y-3">
            <div><h3 className="text-sm font-medium">快速填写</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">选择现有服务商，或直接在下方填写自定义服务。</p></div>
            <div className="flex flex-wrap gap-2">{Object.entries(presets).map(([key, preset]) =>
              <Button key={key} type="button" variant="outline" size="sm" disabled={locked || conflict}
                onClick={() => { setFields((value) => ({ ...value, ...preset })); setError(""); setNotice(""); }}>
                {key === "ollama" && <Server className="size-3.5" aria-hidden />}{key === "ollama" ? "Ollama · 本机或服务器" : preset.name}
              </Button>)}</div>
          </div>
          <div className="grid gap-5 sm:grid-cols-2">
            <label className="block text-sm" htmlFor="provider-name">服务名称
              <Input id="provider-name" className="mt-2" value={fields.name} required maxLength={80}
                onChange={(e) => setFields({ ...fields, name: e.target.value })} />
            </label>
            <label className="block text-sm" htmlFor="provider-model">模型名称
              <Input id="provider-model" className="mt-2" value={fields.model} required maxLength={200}
                placeholder="填写服务商提供的对话模型名称"
                onChange={(e) => setFields({ ...fields, model: e.target.value })} />
            </label>
          </div>
          <label className="block text-sm" htmlFor="provider-url">服务地址
            <Input id="provider-url" aria-label="服务地址" className="mt-2" type="url" value={fields.base_url} required maxLength={2048}
              placeholder="https://example.com/v1" autoComplete="off"
              onChange={(e) => setFields({ ...fields, base_url: e.target.value })} />
            <span className="mt-2 block text-xs text-muted-foreground">填写基础地址，通常以 /v1 结尾。</span>
          </label>
          <label className="block text-sm" htmlFor="provider-key"><span className="inline-flex items-center gap-2"><KeyRound className="size-3.5 text-muted-foreground" aria-hidden />服务密钥</span>
            <Input id="provider-key" aria-label="服务密钥" className="mt-2" type="password" value={secret} maxLength={4096}
              autoComplete="new-password" spellCheck={false} placeholder={saved.key_set ? "留空保留已保存的密钥" : "填写密钥；本地免密服务可留空"}
              onChange={(e) => { setSecret(e.target.value); if (e.target.value) setClearKey(false); }} />
            <span className="mt-2 block text-xs text-muted-foreground">
              {saved.key_set ? "已保存密钥，不会显示原文。" : "尚未保存密钥。"}
            </span>
          </label>
          <Checkbox label="清除已保存密钥" checked={clearKey} disabled={locked || conflict}
            onCheckedChange={(checked) => { setClearKey(checked); if (checked) setSecret(""); }} />
          <div className="space-y-3 rounded-2xl bg-muted/45 p-4">
          <Checkbox className="flex w-full" label="允许连接本机或内网服务" checked={fields.allow_private_network}
            disabled={locked || conflict} onCheckedChange={(checked) => setFields({ ...fields, allow_private_network: checked })} />
          {fields.allow_private_network && <p className="text-xs leading-5 text-muted-foreground">
            请填写运行 Forge ERP 的电脑或服务器能够访问的地址。Ollama 需另行安装对话模型。
          </p>}
          <Checkbox className="flex w-full" label="启用对话模型" checked={fields.enabled} disabled={locked || conflict}
            onCheckedChange={(checked) => setFields({ ...fields, enabled: checked })} />
          </div>
        </fieldset>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={locked || conflict}><Check className="size-4" aria-hidden />保存模型服务</Button>
          <Button type="button" variant="outline" disabled={locked || conflict || dirty || !saved.enabled}
            onClick={testConnection}><Plug className="size-4" aria-hidden />测试已保存的连接</Button>
          <Button type="button" variant="ghost" disabled={locked} onClick={refresh}><RefreshCw className="size-4" aria-hidden />重新读取设置</Button>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">连接测试只发送固定测试文本。请先保存修改，再测试连接。</p>
      </form>
      <div className="space-y-3 px-5 pb-5 sm:px-6 sm:pb-6">
        {busy && <p role="status" className="text-sm text-muted-foreground">正在处理，请稍候…</p>}
        {error && <div role="alert" className="rounded-2xl border border-destructive/30 p-4 text-sm text-destructive">{error}</div>}
        {notice && <p role="status" className="rounded-2xl bg-muted/55 p-4 text-sm">{notice}</p>}
        {uncertain && <Button disabled={busy} onClick={() => void execute()}>重试原提交</Button>}
      </div>
    </div>
    <aside className="min-w-0 space-y-5 rounded-3xl bg-muted/35 p-5 xl:self-start">
      <ShieldCheck className="size-5 text-muted-foreground" aria-hidden />
      <div><h3 className="text-sm font-medium">服务与数据</h3>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">对话时，会把本轮问题所需、且你有权读取的资料发送到所选服务。</p>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">这些设置供当前企业使用；更换服务后请开始新对话，旧对话不会自动转交。</p></div>
      <div className="border-t border-border/60 pt-4"><h3 className="text-sm font-medium">本地语义搜索</h3>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">支持兼容 OpenAI 接口的服务。本地商品语义搜索继续使用独立的模型，不受这里的设置影响。</p></div>
      <Link href="/ai" className="inline-flex items-center gap-1 text-sm text-foreground underline decoration-border underline-offset-4">返回 AI 助手<ArrowUpRight className="size-3.5" aria-hidden /></Link>
    </aside>
  </section>;
}
