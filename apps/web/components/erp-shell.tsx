"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Hexagon,
  LayoutDashboard,
  Sparkles,
  ShoppingBag,
  Truck,
  Boxes,
  Package,
  Users,
  Building2,
  Wallet,
  BarChart3,
  Settings,
  Search,
  LogOut,
  ArrowUpRight,
  Command,
  FileSpreadsheet,
  PackagePlus,
} from "lucide-react";
import { Dialog } from "@base-ui/react/dialog";
import { api, ApiError, getProfile } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { SalesWorkspace } from "@/features/sales/workspace";
import { FundsWorkspace } from "@/features/funds/workspace";
import { ReplenishmentWorkspace } from "@/features/replenishment/workspace";
import { ImportsWorkspace } from "@/features/imports/workspace";
import { DashboardWorkspace } from "@/features/dashboard/workspace";
import { PurchasingWorkspace } from "@/features/purchasing/workspace";
import { InventoryWorkspace } from "@/features/inventory/workspace";
import { CatalogWorkspace } from "@/features/catalog/workspace";
import { CatalogManager } from "@/features/catalog/manager";
import { configs } from "@/features/catalog/config";
import packageMetadata from "@/package.json";

export const navigation = [
  { path: "dashboard", label: "工作台", icon: LayoutDashboard },
  { path: "ai", label: "AI 助手", icon: Sparkles },
  { path: "sales", label: "销售", icon: ShoppingBag },
  { path: "purchase", label: "采购", icon: Truck },
  { path: "inventory", label: "库存", icon: Boxes },
  { path: "replenishment", label: "补货", icon: PackagePlus },
  { path: "products", label: "商品", icon: Package },
  { path: "customers", label: "客户", icon: Users },
  { path: "suppliers", label: "供应商", icon: Building2 },
  { path: "funds", label: "资金", icon: Wallet },
  { path: "imports", label: "资料导入", icon: FileSpreadsheet },
  { path: "reports", label: "报表", icon: BarChart3 },
  { path: "settings", label: "设置", icon: Settings },
];

export function ERPShell({
  section,
  catalogResource,
}: {
  section: string;
  catalogResource?: string;
}) {
  const router = useRouter();
  const path = usePathname();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [logoutError, setLogoutError] = useState("");
  const {
    data: me,
    error,
    isPending,
    refetch,
  } = useQuery({ queryKey: ["me"], queryFn: getProfile });
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      client.clear();
      router.replace("/login");
    }
  }, [error, router, client]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  async function logout() {
    setLogoutError("");
    try {
      const result = await api.POST("/api/v1/auth/logout");
      if (!result.response.ok) throw new Error("logout");
      client.clear();
      router.replace("/login");
    } catch {
      setLogoutError("退出失败，请重试。");
    }
  }
  if (isPending || (error instanceof ApiError && error.status === 401))
    return (
      <main
        className="flex min-h-screen items-center justify-center text-sm text-muted-foreground"
        role="status"
      >
        正在打开工作空间…
      </main>
    );
  if (error || !me)
    return (
      <main className="mx-auto max-w-md p-12">
        <h1 className="text-xl">暂时无法打开工作空间</h1>
        <p role="alert" className="my-4 text-sm text-muted-foreground">
          {error?.message}
        </p>
        <Button onClick={() => refetch()}>重试</Button>
      </main>
    );
  const current = navigation.find((item) => item.path === section);
  return (
    <div className="min-h-screen md:grid md:grid-cols-[220px_1fr]">
      <aside className="flex flex-col border-r border-border bg-[#eef1ec] p-4 md:sticky md:top-0 md:h-screen">
        <Link
          href="/dashboard"
          className="mb-7 flex items-center gap-2 px-3 pt-3 text-xl font-semibold tracking-tight"
        >
          <Hexagon className="text-primary" /> FORGE{" "}
          <span className="text-[10px] font-normal tracking-widest text-muted-foreground">
            ERP
          </span>
        </Link>
        <div className="mb-6 rounded-lg border border-[#d5ddd5] bg-white/60 p-3">
          <p className="truncate text-sm font-semibold">
            {me.organization_name}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            企业空间 · {me.organization_code}
          </p>
        </div>
        <p className="mb-2 px-3 text-[10px] tracking-[.16em] text-muted-foreground">
          工作空间
        </p>
        <nav
          aria-label="主导航"
          className="grid grid-cols-3 gap-1 md:flex md:flex-col"
        >
          {navigation.map(({ path: slug, label, icon: Icon }) => (
            <Link
              key={slug}
              href={`/${slug}`}
              aria-current={path === `/${slug}` ? "page" : undefined}
              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${path === `/${slug}` ? "bg-[#dce7dc] font-semibold text-[#224b34]" : "text-[#697269] hover:bg-white/60"}`}
            >
              <Icon className="size-4" />
              {label}
              {slug === "ai" && (
                <span className="ml-auto hidden rounded border border-[#cbd7ca] px-1 text-[9px] lg:block">
                  即将开放
                </span>
              )}
            </Link>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-border px-3 pt-4 md:block">
          <p className="text-xs font-medium">一步一步，把生意做好。</p>
          <p className="mt-2 text-[10px] text-muted-foreground">
            Forge ERP · v{packageMetadata.version}
          </p>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="flex h-[76px] items-center justify-between border-b border-border bg-white/70 px-6 lg:px-10">
          <div className="text-sm text-muted-foreground">
            工作空间 <span className="mx-3 text-border">/</span>
            <span className="text-foreground">
              {section === "profile" ? "个人信息" : current?.label}
            </span>
          </div>
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              onClick={() => setOpen(true)}
              aria-label="快捷导航"
            >
              <Search className="size-4" />
              <span className="hidden text-xs lg:inline">快捷导航</span>
              <kbd className="hidden rounded border border-border px-1.5 text-[10px] lg:inline">
                ⌘ K
              </kbd>
            </Button>
            <Link href="/profile" className="flex items-center gap-2 text-xs">
              <span className="flex size-8 items-center justify-center rounded-full bg-[#e1e9dd] font-semibold text-primary">
                {me.display_name.slice(0, 1)}
              </span>
              <span className="hidden lg:inline">{me.display_name}</span>
            </Link>
            <button
              aria-label="退出登录"
              onClick={logout}
              className="p-2 text-muted-foreground hover:text-foreground"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-[1440px] p-6 lg:p-10">
          {logoutError && (
            <p role="alert" className="mb-4 text-sm text-destructive">
              {logoutError}
            </p>
          )}
          <p className="mb-3 text-[11px] tracking-[.16em] text-muted-foreground">
            FORGE WORKSPACE
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            {section === "profile"
              ? "个人信息"
              : section === "dashboard"
                ? `欢迎回来，${me.display_name}`
                : current?.label}
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            {section === "profile"
              ? "你的账户与所属企业。"
              : section === "dashboard"
                ? "按实际业务来源核对期间经营与当前余额。"
                : section === "imports"
                  ? "先预览并核对资料，再逐行导入；执行结果可随时查看。"
                  : section === "replenishment"
                    ? "查看全组织库存与补货依据，复核后生成采购草稿。"
                    : section === "sales"
                      ? "从客户开单到出库退货，查看成交价格与经营毛利。"
                      : section === "purchase"
                        ? "从采购订单到分批收货，追溯价格与退货。"
                        : section === "inventory"
                          ? "按仓库查看库存，追溯每次变动。"
                          : section === "funds"
                            ? "按客户与供应商核对往来，记录收付款并追溯每笔来源。"
                            : "工作空间已就绪，业务功能将逐步开放。"}
          </p>
          {section === "replenishment" ? (
            <ReplenishmentWorkspace permissions={me.permissions} />
          ) : section === "imports" ? (
            <ImportsWorkspace permissions={me.permissions} />
          ) : section === "dashboard" ? (
            <>
              <Link
                href="/profile"
                className="mt-4 inline-flex items-center gap-2 text-sm text-primary"
              >
                查看我的账户 <ArrowUpRight className="size-4" />
              </Link>
              <DashboardWorkspace permissions={me.permissions} />
            </>
          ) : ["products", "customers", "suppliers"].includes(section) ? (
            <CatalogWorkspace
              key={section}
              section={section}
              permissions={me.permissions}
            />
          ) : section === "sales" ? (
            <SalesWorkspace permissions={me.permissions} />
          ) : section === "funds" ? (
            <FundsWorkspace permissions={me.permissions} />
          ) : section === "purchase" ? (
            <PurchasingWorkspace permissions={me.permissions} />
          ) : section === "inventory" ? (
            <InventoryWorkspace permissions={me.permissions} />
          ) : catalogResource ? (
            <CatalogManager
              resource={catalogResource}
              permissions={me.permissions}
            />
          ) : section === "settings" ? (
            <section className="mt-8 grid gap-4 sm:grid-cols-3">
              {Object.entries(configs)
                .filter(([key]) =>
                  ["categories", "brands", "units", "warehouses"].includes(key),
                )
                .map(([key, c]) => (
                  <Link
                    key={key}
                    href={`/settings/${key}`}
                    className="rounded-xl border border-border bg-white p-6"
                  >
                    <h2 className="text-sm font-semibold">{c.title}</h2>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {c.description}
                    </p>
                  </Link>
                ))}
            </section>
          ) : section === "profile" ? (
            <section className="mt-8 max-w-2xl rounded-xl border border-border bg-white p-7">
              <dl className="grid grid-cols-[100px_1fr] gap-5 text-sm">
                <dt className="text-muted-foreground">姓名</dt>
                <dd>{me.display_name}</dd>
                <dt className="text-muted-foreground">邮箱</dt>
                <dd className="break-all">{me.email}</dd>
                <dt className="text-muted-foreground">所属企业</dt>
                <dd>{me.organization_name}</dd>
                <dt className="text-muted-foreground">企业代码</dt>
                <dd>{me.organization_code}</dd>
              </dl>
            </section>
          ) : (
            <>
              <section className="relative mt-8 overflow-hidden rounded-xl border border-[#d9e2d6] bg-[#eaf0e5] p-7 lg:p-9">
                <div className="relative z-10 max-w-lg">
                  <span className="rounded-full border border-[#c9d7c2] bg-white/45 px-2.5 py-1 text-[10px] text-primary">
                    准备就绪
                  </span>
                  <h2 className="mt-5 text-2xl font-medium">
                    从清晰的工作空间开始。
                  </h2>
                  <p className="mt-3 text-sm leading-7 text-muted-foreground">
                    {section === "dashboard"
                      ? "销售、采购与库存工作台已开放，可从下方进入并按账户权限办理业务。"
                      : `${current?.label ?? "此功能"}将在后续版本开放。`}
                  </p>
                  <Link
                    href="/profile"
                    className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-primary"
                  >
                    查看我的账户 <ArrowUpRight className="size-4" />
                  </Link>
                </div>
                <Hexagon
                  aria-hidden
                  className="absolute -right-10 -bottom-14 size-72 text-[#d8e3d0]"
                  strokeWidth={0.6}
                />
              </section>
              {section === "dashboard" && (
                <section className="mt-9">
                  <div className="mb-4 flex items-center justify-between">
                    <h2 className="text-sm font-semibold">业务空间</h2>
                    <span className="text-xs text-muted-foreground">
                      按账户权限使用
                    </span>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                    {navigation
                      .filter((item) =>
                        ["sales", "purchase", "inventory"].includes(item.path),
                      )
                      .map(({ path, label, icon: Icon }) => (
                        <Link
                          href={`/${path}`}
                          key={path}
                          className="rounded-xl border border-border bg-white p-6 transition-colors hover:border-[#afc3aa]"
                        >
                          <div className="mb-5 flex items-center justify-between">
                            <Icon className="size-5 text-[#74886c]" />
                            <ArrowUpRight className="size-4 text-[#acb7a7]" />
                          </div>
                          <h3 className="text-sm font-semibold">{label}管理</h3>
                          <p className="mt-2 text-xs text-muted-foreground">
                            {path === "inventory"
                              ? "查看库存、流水与库存单据"
                              : path === "sales"
                                ? "客户开单、出库退货与成交历史"
                                : "采购开单、分批收货与退货"}
                          </p>
                        </Link>
                      ))}
                  </div>
                </section>
              )}
              <div className="mt-8 flex items-center gap-3 rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">
                <Command className="size-4" />
                <span>使用 Ctrl / ⌘ + K，快速前往工作空间中的页面。</span>
              </div>
            </>
          )}
        </main>
      </div>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 bg-black/25" />
          <Dialog.Popup className="fixed top-[18%] left-1/2 w-[min(90vw,460px)] -translate-x-1/2 rounded-xl border border-border bg-white p-5 shadow-xl">
            <Dialog.Title className="text-sm font-semibold">
              快捷导航
            </Dialog.Title>
            <Dialog.Description className="mt-1 text-xs text-muted-foreground">
              输入页面名称，快速前往。
            </Dialog.Description>
            <input
              aria-label="搜索页面"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="搜索页面…"
              className="my-4 w-full rounded-md border border-border p-2 text-sm"
            />
            <div className="max-h-72 overflow-auto">
              {navigation
                .filter((item) => item.label.includes(filter))
                .map((item) => (
                  <Link
                    href={`/${item.path}`}
                    key={item.path}
                    onClick={() => setOpen(false)}
                    className="block rounded-md px-3 py-2 text-sm hover:bg-muted"
                  >
                    {item.label}
                  </Link>
                ))}
            </div>
            <Dialog.Close className="mt-3 text-xs text-muted-foreground">
              关闭 · Esc
            </Dialog.Close>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
