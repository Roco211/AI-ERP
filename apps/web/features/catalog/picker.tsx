"use client";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { resourceClients, unwrap, type ViewRow } from "./client";
import type { components } from "@/generated/api/schema";

type Product = components["schemas"]["ProductRead"];
type Snapshot = components["schemas"]["ConversionSnapshot"];
export function ProductPicker({ permissions }: { permissions: string[] }) {
  const [q, setQ] = useState("");
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<Product | null>(null);
  const [unit, setUnit] = useState("");
  const [qty, setQty] = useState("1");
  const [attributeKey, setAttributeKey] = useState("");
  const [attributeValue, setAttributeValue] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const {
    data,
    isFetching,
    error: searchError,
  } = useQuery({
    queryKey: ["catalog", "search", q, attributeKey, attributeValue],
    queryFn: async () =>
      unwrap<{ items: Product[]; total: number }>(
        await api.GET("/api/v1/catalog/search", {
          params: {
            query: {
              q,
              page_size: 20,
              attribute_key: attributeKey || undefined,
              attribute_value: attributeValue || undefined,
            },
          },
        }),
      ),
    enabled: permissions.includes("catalog.read"),
  });
  const units = useQuery({
    queryKey: ["catalog", "product-units", selected?.id],
    queryFn: async () =>
      unwrap(
        await resourceClients["product-units"].list({
          product_id: selected!.id,
          active: true,
          page_size: 100,
        }),
      ),
    enabled: !!selected,
  });
  const names = useQuery({
    queryKey: ["catalog", "units", "names"],
    queryFn: async () =>
      unwrap(await resourceClients.units.list({ page_size: 100 })),
    enabled: permissions.includes("catalog.read"),
  });
  const rows = data?.items ?? [];
  function select(product: Product) {
    setSelected(product);
    setUnit(product.default_sales_unit_id ?? product.base_unit_id);
    setSnapshot(null);
    setError("");
  }
  async function confirm() {
    if (!selected) return;
    setBusy(true);
    setError("");
    setSnapshot(null);
    try {
      setSnapshot(
        unwrap<Snapshot>(
          await api.GET("/api/v1/catalog/conversion", {
            params: { query: { product_id: selected.id, unit_id: unit, qty } },
          }),
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "换算失败");
    } finally {
      setBusy(false);
    }
  }
  if (!permissions.includes("catalog.read"))
    return <p role="alert">当前账户没有查看商品的权限。</p>;
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">快捷选品</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          搜索编码、条码或规格。↑ ↓ 选择，Enter 确认，Esc 返回搜索。
        </p>
      </div>
      <Input
        ref={searchRef}
        role="combobox"
        aria-label="搜索商品"
        aria-expanded={!!rows.length}
        aria-controls="product-results"
        aria-activedescendant={
          rows[index] ? `product-${rows[index].id}` : undefined
        }
        placeholder="例如：304 8*30"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setIndex(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setIndex((i) => Math.min(i + 1, rows.length - 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setIndex((i) => Math.max(0, i - 1));
          }
          if (e.key === "Enter" && rows[index]) {
            e.preventDefault();
            select(rows[index]);
          }
          if (e.key === "Escape") {
            setSelected(null);
            setSnapshot(null);
            searchRef.current?.focus();
          }
        }}
      />
      <div className="flex gap-2">
        <Input
          aria-label="属性名称筛选"
          placeholder="属性名称，例如 材质"
          value={attributeKey}
          onChange={(e) => {
            setAttributeKey(e.target.value);
            setIndex(0);
          }}
        />
        <Input
          aria-label="属性值筛选"
          placeholder="属性值，例如 304"
          value={attributeValue}
          onChange={(e) => {
            setAttributeValue(e.target.value);
            setIndex(0);
          }}
        />
      </div>
      {searchError && <p role="alert">{searchError.message}</p>}
      <p role="status" className="text-xs text-muted-foreground">
        {isFetching
          ? "正在搜索…"
          : `找到 ${data?.total ?? 0} 件商品，显示前 20 件`}
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <ul
          id="product-results"
          role="listbox"
          aria-label="商品搜索结果"
          className="max-h-96 overflow-y-auto rounded-xl border border-border bg-white"
        >
          {rows.map((p, i) => (
            <li
              key={p.id}
              id={`product-${p.id}`}
              role="option"
              aria-selected={index === i}
            >
              <button
                onClick={() => {
                  setIndex(i);
                  select(p);
                }}
                className={`w-full border-b border-border p-4 text-left ${index === i ? "bg-primary/10" : ""}`}
              >
                <span className="block text-sm font-medium">{p.name}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {p.sku} · {p.specification}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="rounded-xl border border-border bg-white p-5">
          {selected ? (
            <div className="space-y-4">
              <h3 className="font-medium">{selected.name}</h3>
              <label className="block text-sm">
                单位
                <select
                  aria-label="选品单位"
                  className="mt-2 block h-9 w-full rounded-lg border border-border px-2"
                  value={unit}
                  onChange={(e) => {
                    setUnit(e.target.value);
                    setSnapshot(null);
                  }}
                >
                  {units.data?.items.map((u: ViewRow) => (
                    <option key={u.id} value={String(u.unit_id)}>
                      {String(
                        names.data?.items.find((n) => n.id === u.unit_id)
                          ?.name ?? "单位",
                      )}{" "}
                      · 1 = {String(u.unit_to_base_factor)} 基础单位
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                数量
                <Input
                  aria-label="选品数量"
                  className="mt-2"
                  value={qty}
                  inputMode="decimal"
                  onChange={(e) => {
                    setQty(e.target.value);
                    setSnapshot(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void confirm();
                  }}
                />
              </label>
              <Button onClick={confirm} disabled={busy}>
                {busy ? "正在换算…" : "确认选择"}
              </Button>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              {snapshot && (
                <div
                  role="status"
                  className="rounded-lg bg-primary/10 p-4 text-sm"
                >
                  已选择 {snapshot.qty}，合计 {snapshot.base_qty} 基础单位。
                  <p className="mt-2 text-xs text-muted-foreground">
                    本次换算率 {snapshot.unit_to_base_factor} · 已保留换算快照
                  </p>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              选择左侧商品，再确认单位和数量。
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
