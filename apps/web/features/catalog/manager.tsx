"use client";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useForm, useWatch } from "react-hook-form";
import { Dialog } from "@base-ui/react/dialog";
import {
  Plus,
  Search,
  Pencil,
  ChevronLeft,
  ChevronRight,
  X,
  CircleCheck,
  CircleSlash,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { configs, type Field } from "./config";
import { resourceClients, unwrap, type ViewRow } from "./client";
import type { components } from "@/generated/api/schema";

type Attribute = components["schemas"]["AttributeDefinition"];

function AttributeTemplate({
  value,
  onChange,
}: {
  value: Attribute[];
  onChange: (v: Attribute[]) => void;
}) {
  return (
    <div className="space-y-2">
      {value.map((item, i) => (
        <div
          key={i}
          className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-muted/30 p-3"
        >
          <Input
            aria-label={`属性${i + 1}名称`}
            value={item.label}
            placeholder="属性名称，例如 材质"
            onChange={(e) =>
              onChange(
                value.map((v, n) =>
                  n === i
                    ? { ...v, key: e.target.value, label: e.target.value }
                    : v,
                ),
              )
            }
          />
          <select
            aria-label={`属性${i + 1}类型`}
            className="rounded-md border border-border p-1.5 text-sm"
            value={item.kind}
            onChange={(e) =>
              onChange(
                value.map((v, n) =>
                  n === i
                    ? { ...v, kind: e.target.value as Attribute["kind"] }
                    : v,
                ),
              )
            }
          >
            <option value="text">文本</option>
            <option value="decimal">数值</option>
            <option value="enum">选项</option>
            <option value="boolean">是 / 否</option>
          </select>
          {item.kind === "enum" ? (
            <Input
              className="col-span-2"
              aria-label={`属性${i + 1}选项`}
              placeholder="选项，用逗号分隔，如 304,316"
              value={item.options?.join(",") ?? ""}
              onChange={(e) =>
                onChange(
                  value.map((v, n) =>
                    n === i
                      ? {
                          ...v,
                          options: e.target.value
                            .split(/[,，]/)
                            .map((s) => s.trim())
                            .filter(Boolean),
                        }
                      : v,
                  ),
                )
              }
            />
          ) : (
            <Input
              aria-label={`属性${i + 1}单位`}
              placeholder="显示单位，例如 mm（可选）"
              value={item.unit ?? ""}
              onChange={(e) =>
                onChange(
                  value.map((v, n) =>
                    n === i ? { ...v, unit: e.target.value } : v,
                  ),
                )
              }
            />
          )}
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={item.required ?? false}
                onChange={(e) =>
                  onChange(
                    value.map((v, n) =>
                      n === i ? { ...v, required: e.target.checked } : v,
                    ),
                  )
                }
              />
              必填
            </label>
            <button
              type="button"
              aria-label={`删除属性${i + 1}`}
              onClick={() => onChange(value.filter((_, n) => n !== i))}
            >
              <X className="size-4" />
            </button>
          </div>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() =>
          onChange([
            ...value,
            {
              key: "",
              label: "",
              kind: "text",
              required: false,
              unit: "",
              options: [],
            },
          ])
        }
      >
        <Plus className="size-3" />
        添加属性
      </Button>
    </div>
  );
}

function ReferenceField({
  field,
  value,
  onChange,
  exclude,
}: {
  field: Field;
  value: string;
  onChange: (v: string) => void;
  exclude?: string;
}) {
  const [q, setQ] = useState("");
  const client = resourceClients[field.reference!];
  const { data } = useQuery({
    queryKey: ["catalog", field.reference, "lookup", q],
    queryFn: async () =>
      unwrap(await client.list({ q, page_size: 100, active: true })),
    enabled: !!client,
  });
  const { data: selected } = useQuery({
    queryKey: ["catalog", field.reference, value],
    queryFn: async () => unwrap<ViewRow>(await client.get(value)),
    enabled: !!value && !!client,
  });
  const rows = (data?.items ?? []) as ViewRow[];
  const options =
    selected && !rows.some((r) => r.id === value)
      ? [selected as ViewRow, ...rows]
      : rows;
  return (
    <div className="space-y-1.5">
      <Input
        aria-label={`搜索${field.label}`}
        value={q}
        placeholder={`搜索${field.label}…`}
        onChange={(e) => setQ(e.target.value)}
      />
      <select
        id={field.key}
        aria-label={field.label}
        value={value}
        required={field.required}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-lg border border-border bg-background px-2 text-sm"
      >
        <option value="">请选择{field.required ? "" : "（可不选）"}</option>
        {options
          .filter((r) => r.id !== exclude)
          .map((r) => (
            <option key={r.id} value={r.id}>
              {String(r.name ?? r.sku ?? r.id)}
              {r.code ? ` · ${r.code}` : ""}
            </option>
          ))}
      </select>
    </div>
  );
}

export function CatalogManager({
  resource,
  permissions,
  initialFilter = {},
}: {
  resource: string;
  permissions: string[];
  initialFilter?: Record<string, string>;
}) {
  const cfg = configs[resource];
  const client = resourceClients[resource];
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("all");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ViewRow | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [template, setTemplate] = useState<Attribute[]>([]);
  const [attributes, setAttributes] = useState<Record<string, unknown>>({});
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const form = useForm<Record<string, string>>();
  const formValues = useWatch({ control: form.control });
  const selectedCategory = formValues.category_id ?? "";
  const canRead = permissions.includes(cfg.permission + ".read");
  const canWrite = permissions.includes(cfg.permission + ".write");
  const list = useQuery({
    queryKey: ["catalog", resource, q, page, status, initialFilter],
    queryFn: async () =>
      unwrap(
        await client.list({
          q,
          page,
          page_size: 20,
          active: status === "all" ? undefined : status === "active",
          ...initialFilter,
        }),
      ),
    enabled: canRead,
  });
  const category = useQuery({
    queryKey: ["catalog", "categories", selectedCategory],
    queryFn: async () =>
      unwrap<ViewRow>(await resourceClients.categories.get(selectedCategory)),
    enabled: !!selectedCategory,
  });
  const attributeDefs = (category.data?.attribute_schema ?? []) as Attribute[];
  const refs = cfg.fields.filter((f) => f.kind === "reference");
  const lookups = useQueries({
    queries: refs.map((f) => ({
      queryKey: ["catalog", f.reference, "names"],
      queryFn: async () =>
        unwrap<{ items: ViewRow[]; total: number }>(
          await resourceClients[f.reference!].list({ page_size: 100 }),
        ),
      enabled: canRead,
    })),
  });
  const refNames = useMemo(
    () =>
      Object.fromEntries(
        refs.map((f, i) => [
          f.key,
          Object.fromEntries(
            ((lookups[i]?.data?.items ?? []) as ViewRow[]).map((r) => [
              r.id,
              String(r.name ?? r.sku ?? r.id),
            ]),
          ),
        ]),
      ),
    [refs, lookups],
  );
  function start(row: ViewRow | null) {
    setEditing(row);
    setError("");
    attempt.current = null;
    form.reset(
      Object.fromEntries(
        cfg.fields.map((f) => [
          f.key,
          row?.[f.key] == null
            ? (initialFilter[f.key] ?? "")
            : String(row[f.key]),
        ]),
      ),
    );
    setTemplate((row?.attribute_schema ?? []) as Attribute[]);
    setAttributes((row?.attributes ?? {}) as Record<string, unknown>);
    setOpen(true);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    await form.handleSubmit(async (values) => {
      setBusy(true);
      setError("");
      const body: Record<string, unknown> = {};
      for (const f of cfg.fields) {
        if (f.kind === "attribute_schema") body[f.key] = template;
        else if (f.kind === "attributes")
          body[f.key] = Object.fromEntries(
            Object.entries(attributes).filter(
              ([k, v]) => attributeDefs.some((a) => a.key === k) && v !== "",
            ),
          );
        else if (f.kind === "reference") body[f.key] = values[f.key] || null;
        else if (f.kind === "number")
          body[f.key] = values[f.key] ? Number(values[f.key]) : 0;
        else if (f.kind === "decimal") body[f.key] = values[f.key] || "0";
        else body[f.key] = values[f.key] ?? "";
      }
      if (editing) body.expected_version = editing.version;
      const signature = JSON.stringify(body);
      if (attempt.current?.body !== signature)
        attempt.current = { body: signature, key: crypto.randomUUID() };
      try {
        const response = editing
          ? await client.update(editing.id, body, attempt.current.key)
          : await client.create(body, attempt.current.key);
        if (!response.response.ok) attempt.current = null;
        unwrap(response);
        setOpen(false);
        setSuccess(editing ? "修改已保存" : "资料已创建");
        await qc.invalidateQueries({ queryKey: ["catalog"] });
      } catch (e) {
        setError(e instanceof Error ? e.message : "保存失败，请重试");
      } finally {
        setBusy(false);
      }
    })(event);
  }
  async function toggle(row: ViewRow) {
    setBusy(true);
    setError("");
    try {
      unwrap(
        await client.toggle(
          row.id,
          !row.active,
          row.version,
          crypto.randomUUID(),
        ),
      );
      setSuccess(row.active ? "资料已停用" : "资料已启用");
      await qc.invalidateQueries({ queryKey: ["catalog"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  const columns: ColumnDef<ViewRow>[] = [
    ...cfg.columns.map((key) => ({
      id: key,
      header: cfg.fields.find((f) => f.key === key)?.label ?? key,
      cell: ({ row }: { row: { original: ViewRow } }) => {
        const value = row.original[key];
        return (
          <span className={key === "name" ? "font-medium text-foreground" : ""}>
            {value == null || value === ""
              ? "—"
              : (cfg.fields
                  .find((f) => f.key === key)
                  ?.options?.find((o) => o.value === value)?.label ??
                refNames[key]?.[String(value)] ??
                String(value))}
          </span>
        );
      },
    })),
    {
      id: "active",
      header: "状态",
      cell: ({ row }) => (
        <span
          className={`rounded-full px-2 py-1 text-[11px] ${row.original.active ? "bg-emerald-50 text-emerald-800" : "bg-gray-100 text-gray-500"}`}
        >
          {row.original.active ? "启用" : "停用"}
        </span>
      ),
    },
    {
      id: "actions",
      header: "操作",
      cell: ({ row }) => (
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => start(row.original)}>
            {canWrite ? <Pencil className="size-3" /> : null}
            {canWrite ? "编辑" : "查看"}
          </Button>
          {canWrite && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => toggle(row.original)}
            >
              {row.original.active ? (
                <CircleSlash className="size-3" />
              ) : (
                <CircleCheck className="size-3" />
              )}
              {row.original.active ? "停用" : "启用"}
            </Button>
          )}
        </div>
      ),
    },
  ];
  const table = useReactTable({
    data: (list.data?.items ?? []) as ViewRow[],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  useEffect(() => {
    if (!open) form.clearErrors();
  }, [open, form]);
  if (!canRead)
    return (
      <p role="alert" className="rounded-lg bg-amber-50 p-5 text-sm">
        当前账户没有查看{cfg.title}的权限。
      </p>
    );
  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">{cfg.title}</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {cfg.description}
          </p>
        </div>
        {canWrite && (
          <Button onClick={() => start(null)}>
            <Plus className="size-4" />
            新建{cfg.singular}
          </Button>
        )}
      </div>
      {success && (
        <p role="status" className="text-sm text-primary">
          {success}
        </p>
      )}
      {error && !open && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="rounded-xl border border-border bg-white">
        <div className="flex flex-wrap items-center gap-3 border-b border-border p-4">
          <div className="relative min-w-48 flex-1 sm:max-w-sm">
            <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" />
            <Input
              aria-label="搜索资料"
              className="pl-8"
              placeholder="搜索编码或名称…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            aria-label="状态筛选"
            className="h-8 rounded-lg border border-border px-3 text-sm"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">全部状态</option>
            <option value="active">启用</option>
            <option value="inactive">停用</option>
          </select>
          <span className="ml-auto text-xs text-muted-foreground">
            共 {list.data?.total ?? 0} 条
          </span>
        </div>
        {list.isPending ? (
          <p
            role="status"
            className="p-10 text-center text-sm text-muted-foreground"
          >
            正在加载…
          </p>
        ) : list.error ? (
          <div role="alert" className="p-8 text-sm text-destructive">
            {list.error.message}
            <Button
              variant="outline"
              className="ml-4"
              onClick={() => list.refetch()}
            >
              重试
            </Button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full whitespace-nowrap text-left text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id}>
                    {group.headers.map((h) => (
                      <th key={h.id} className="px-5 py-3 font-medium">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className="border-t border-border hover:bg-muted/20"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className="max-w-80 truncate px-5 py-3 text-muted-foreground"
                      >
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!list.data?.items.length && (
              <div className="p-12 text-center">
                <p className="text-sm font-medium">
                  {q ? "没有找到匹配的资料" : "还没有资料"}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {q ? "试试其他编码或名称。" : "从新建第一条资料开始。"}
                </p>
              </div>
            )}
          </div>
        )}
        <div className="flex items-center justify-end gap-3 border-t border-border p-3 text-xs text-muted-foreground">
          <span>第 {page} 页</span>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="上一页"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft />
          </Button>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="下一页"
            disabled={page * 20 >= (list.data?.total ?? 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>
      <Dialog.Root
        open={open}
        onOpenChange={(value) => {
          if (!busy) setOpen(value);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/25" />
          <Dialog.Popup className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-border bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-6 py-5">
              <div>
                <Dialog.Title className="text-lg font-semibold">
                  {editing ? (canWrite ? "编辑" : "查看") : "新建"}
                  {cfg.singular}
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-xs text-muted-foreground">
                  {editing
                    ? `版本 ${editing.version} · 保存时检查并发修改`
                    : "填写基础资料，保存后即可使用。"}
                </Dialog.Description>
              </div>
              <Dialog.Close aria-label="关闭编辑" disabled={busy}>
                <X className="size-5" />
              </Dialog.Close>
            </div>
            <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
              <fieldset
                disabled={!canWrite || busy}
                className="flex-1 space-y-5 overflow-y-auto p-6"
              >
                {cfg.fields.map((f) => (
                  <div key={f.key} className="space-y-2">
                    <Label htmlFor={f.key}>
                      {f.label}
                      {f.required ? (
                        <span aria-hidden="true" className="text-destructive">
                          {" "}
                          *
                        </span>
                      ) : null}
                    </Label>
                    {f.kind === "reference" ? (
                      <ReferenceField
                        field={f}
                        value={formValues[f.key] ?? ""}
                        onChange={(v) => form.setValue(f.key, v)}
                        exclude={
                          f.reference === resource ? editing?.id : undefined
                        }
                      />
                    ) : f.kind === "attribute_schema" ? (
                      <AttributeTemplate
                        value={template}
                        onChange={setTemplate}
                      />
                    ) : f.kind === "attributes" ? (
                      <div className="space-y-3">
                        {attributeDefs.length ? (
                          attributeDefs.map((a) => (
                            <div key={a.key}>
                              <Label
                                htmlFor={`attr-${a.key}`}
                                className="mb-1.5 text-xs"
                              >
                                {a.label}
                                {a.unit ? ` (${a.unit})` : ""}
                                {a.required ? " *" : ""}
                              </Label>
                              {a.kind === "enum" || a.kind === "boolean" ? (
                                <select
                                  id={`attr-${a.key}`}
                                  required={a.required}
                                  className="w-full rounded-lg border border-border p-2 text-sm"
                                  value={
                                    attributes[a.key] === undefined
                                      ? ""
                                      : String(attributes[a.key])
                                  }
                                  onChange={(e) =>
                                    setAttributes({
                                      ...attributes,
                                      [a.key]:
                                        a.kind === "boolean" &&
                                        e.target.value !== ""
                                          ? e.target.value === "true"
                                          : e.target.value,
                                    })
                                  }
                                >
                                  <option value="">请选择</option>
                                  {(a.kind === "boolean"
                                    ? ["true", "false"]
                                    : (a.options ?? [])
                                  ).map((v) => (
                                    <option key={v} value={v}>
                                      {a.kind === "boolean"
                                        ? v === "true"
                                          ? "是"
                                          : "否"
                                        : v}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <Input
                                  id={`attr-${a.key}`}
                                  required={a.required}
                                  value={String(attributes[a.key] ?? "")}
                                  onChange={(e) =>
                                    setAttributes({
                                      ...attributes,
                                      [a.key]: e.target.value,
                                    })
                                  }
                                />
                              )}
                            </div>
                          ))
                        ) : (
                          <p className="text-xs text-muted-foreground">
                            选择配置了属性模板的分类后，可填写规格。
                          </p>
                        )}
                      </div>
                    ) : f.kind === "textarea" ? (
                      <textarea
                        id={f.key}
                        aria-label={f.label}
                        rows={3}
                        className="w-full rounded-lg border border-border p-2 text-sm"
                        {...form.register(f.key)}
                      />
                    ) : f.kind === "select" ? (
                      <select
                        id={f.key}
                        aria-label={f.label}
                        required={f.required}
                        className="h-9 w-full rounded-lg border border-border px-2 text-sm"
                        {...form.register(f.key)}
                      >
                        <option value="">请选择</option>
                        {f.options?.map((o) => (
                          <option value={o.value} key={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Input
                        id={f.key}
                        aria-label={f.label}
                        required={f.required}
                        type={f.kind === "number" ? "number" : "text"}
                        inputMode={f.kind === "decimal" ? "decimal" : undefined}
                        {...form.register(f.key)}
                      />
                    )}
                    {f.hint && (
                      <p className="text-xs text-muted-foreground">{f.hint}</p>
                    )}
                  </div>
                ))}
              </fieldset>
              <div className="space-y-3 border-t border-border p-5">
                {error && (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                )}
                <div className="flex justify-end gap-2">
                  <Dialog.Close
                    render={
                      <Button variant="outline" type="button" disabled={busy} />
                    }
                  >
                    取消
                  </Dialog.Close>
                  {canWrite && (
                    <Button type="submit" disabled={busy}>
                      {busy ? "正在保存…" : "保存"}
                    </Button>
                  )}
                </div>
              </div>
            </form>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
