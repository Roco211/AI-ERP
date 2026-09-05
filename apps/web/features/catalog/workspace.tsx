"use client";
import { useState } from "react";
import Link from "next/link";
import { CatalogManager } from "./manager";
import { ProductPicker } from "./picker";
import { configs } from "./config";

export function CatalogWorkspace({
  section,
  permissions,
}: {
  section: string;
  permissions: string[];
}) {
  const [tab, setTab] = useState(section);
  const tabs =
    section === "products"
      ? ["products", "product-units", "product-prices", "picker"]
      : section === "suppliers"
        ? ["suppliers", "supplier-products"]
        : ["customers"];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-lg px-3 py-2 text-sm ${tab === t ? "bg-primary text-primary-foreground" : "bg-white text-muted-foreground"}`}
          >
            {t === "picker" ? "快捷选品" : configs[t].title}
          </button>
        ))}
        {section === "products" && (
          <Link
            href="/settings/categories"
            className="ml-auto text-xs text-primary underline underline-offset-4"
          >
            管理分类、品牌和单位
          </Link>
        )}
      </div>
      {tab === "picker" ? (
        <ProductPicker permissions={permissions} />
      ) : (
        <CatalogManager key={tab} resource={tab} permissions={permissions} />
      )}
    </div>
  );
}
