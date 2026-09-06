"use client";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useId, useState } from "react";
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
  const tabId = useId();
  const [tab, setTab] = useState(section);
  const tabs =
    section === "products"
      ? ["products", "product-units", "product-prices", "picker"]
      : section === "suppliers"
        ? ["suppliers", "supplier-products"]
        : ["customers"];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <Tabs value={tab} onValueChange={setTab} variant="pill">
          <TabsList aria-label="资料分类" className="flex flex-wrap">
            {tabs.map((value) => <TabsTrigger id={`${tabId}-records-tab-${value}`} aria-controls={`${tabId}-records-panel`} key={value} value={value}>
              {value === "picker" ? "快捷选品" : configs[value].title}
            </TabsTrigger>)}
          </TabsList>
        </Tabs>
        {section === "products" && (
          <Link
            href="/settings/categories"
            className="ml-auto text-xs text-primary underline underline-offset-4"
          >
            管理分类、品牌和单位
          </Link>
        )}
      </div>
      <div role="tabpanel" id={`${tabId}-records-panel`} aria-labelledby={`${tabId}-records-tab-${tab}`} tabIndex={0} className="min-w-0 space-y-5">
      {tab === "picker" ? (
        <ProductPicker permissions={permissions} />
      ) : (
        <CatalogManager key={tab} resource={tab} permissions={permissions} />
      )}
      </div>
    </div>
  );
}
