"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PanelLeft,
  X,
  ChevronsUpDown,
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
  FileSpreadsheet,
  PackagePlus,
} from "lucide-react";
import { ThemeToggle } from "@/components/motion/theme-toggle";
import { CommandPalette } from "@/components/motion/command-palette";
import {
  AnimatedSidebarProvider, AnimatedSidebar, AnimatedSidebarHeader,
  AnimatedSidebarContent, AnimatedSidebarFooter, AnimatedSidebarGroup,
  AnimatedSidebarGroupLabel, AnimatedSidebarGroupContent, AnimatedSidebarMenu,
  AnimatedSidebarMenuItem, AnimatedSidebarMenuButton, AnimatedSidebarTrigger,
  AnimatedSidebarClose, AnimatedSidebarRail, AnimatedSidebarInset,
} from "@/components/motion/animated-sidebar";
import { api, ApiError, getProfile } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { SalesWorkspace } from "@/features/sales/workspace";
import { runGuardedNavigation } from "@/features/sales/navigation";
import { FundsWorkspace } from "@/features/funds/workspace";
import { ReplenishmentWorkspace } from "@/features/replenishment/workspace";
import { ImportsWorkspace } from "@/features/imports/workspace";
import { DashboardWorkspace } from "@/features/dashboard/workspace";
import { AssistantWorkspace } from "@/features/ai/workspace";
import { ProviderSettings } from "@/features/ai/provider-settings";
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
  const [mobileOpen, setMobileOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [navigationBlocked, setNavigationBlocked] = useState(false);
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
  function navigate(href: string) {
    const allowed = runGuardedNavigation(() => router.push(href));
    setNavigationBlocked(!allowed);
  }
  function logout() {
    const allowed = runGuardedNavigation(() => { void performLogout(); });
    setNavigationBlocked(!allowed);
  }
  async function performLogout() {
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
  const current = section === "llm"
    ? { label: "模型服务", path: "settings", icon: Settings }
    : navigation.find((item) => item.path === section);
  const isCurrent = (slug: string) => path === `/${slug}` ||
    (slug === "settings" && path.startsWith("/settings/"));
  return (
    <AnimatedSidebarProvider openMobile={mobileOpen} onOpenMobileChange={setMobileOpen}>
      <a href="#workspace" className="sr-only z-[200] rounded-full bg-primary px-5 py-3 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3">跳到主要内容</a>
      <AnimatedSidebar ariaLabel="工作空间侧栏" collapsible="icon">
        <AnimatedSidebarHeader className="p-3 pb-2">
          <div className="flex min-h-11 items-center gap-2.5 overflow-hidden px-2">
            <Link href="/dashboard" aria-label="Forge ERP 工作台" className="grid size-7 shrink-0 place-items-center rounded-lg bg-foreground text-sm font-semibold text-background">F</Link>
            <div className="min-w-0 flex-1 group-data-[state=collapsed]/sidebar:hidden">
              <p className="truncate text-sm font-semibold">{me.organization_name}</p>
              <p className="truncate text-[11px] text-muted-foreground" title={me.organization_code}>Forge ERP · {me.organization_code}</p>
            </div>
            <ChevronsUpDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground group-data-[state=collapsed]/sidebar:hidden" />
            <AnimatedSidebarClose aria-label="关闭导航" className="md:hidden"><X className="size-4" /></AnimatedSidebarClose>
          </div>
        </AnimatedSidebarHeader>
        <AnimatedSidebarContent className="px-2 pt-2">
          <nav aria-label="主导航">
            {[
              { label: "工作空间", paths: ["dashboard", "ai"] },
              { label: "业务", paths: ["sales", "purchase", "inventory", "replenishment", "funds"] },
              { label: "资料与管理", paths: ["products", "customers", "suppliers", "imports", "reports", "settings"] },
            ].map((group) => <AnimatedSidebarGroup key={group.label}>
              <AnimatedSidebarGroupLabel>{group.label}</AnimatedSidebarGroupLabel>
              <AnimatedSidebarGroupContent><AnimatedSidebarMenu>
                {navigation.filter((item) => group.paths.includes(item.path)).map(({ path: slug, label, icon: Icon }) => (
                  <AnimatedSidebarMenuItem key={slug}>
                    <AnimatedSidebarMenuButton href={`/${slug}`} icon={<Icon className="size-4" />} isActive={isCurrent(slug)}
                      onNavigate={(event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); navigate(`/${slug}`); } }}>
                      {label}
                    </AnimatedSidebarMenuButton>
                  </AnimatedSidebarMenuItem>
                ))}
              </AnimatedSidebarMenu></AnimatedSidebarGroupContent>
            </AnimatedSidebarGroup>)}
          </nav>
        </AnimatedSidebarContent>
        <AnimatedSidebarFooter className="border-t border-border p-3">
          <Link href="/profile" className="flex min-w-0 items-center gap-2.5 overflow-hidden rounded-xl p-1.5 hover:bg-muted">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[#d5ff66] text-sm font-semibold text-black">{me.display_name.slice(0, 1)}</span>
            <span className="min-w-0 group-data-[state=collapsed]/sidebar:hidden"><span className="block truncate text-sm font-medium">{me.display_name}</span><span className="block truncate text-xs text-muted-foreground">{me.email}</span></span>
          </Link>
          <p className="px-1.5 pt-2 text-[10px] text-muted-foreground group-data-[state=collapsed]/sidebar:hidden">Forge ERP · v{packageMetadata.version}</p>
        </AnimatedSidebarFooter>
        <AnimatedSidebarRail aria-label="收起或展开导航" />
      </AnimatedSidebar>
      <AnimatedSidebarInset className="min-w-0">
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-border px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <AnimatedSidebarTrigger aria-label="切换导航"><PanelLeft className="size-4" /></AnimatedSidebarTrigger>
            <span aria-hidden className="h-4 w-px bg-border" />
            <span className="truncate text-sm font-medium">{section === "profile" ? "个人信息" : current?.label}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Button variant="ghost" onClick={() => { setMobileOpen(false); setOpen(true); }} aria-label="快捷导航" className="gap-2">
              <Search className="size-4" /><span className="hidden sm:inline">搜索工作空间</span><kbd className="ml-3 hidden rounded border border-border px-1.5 text-[10px] sm:inline">⌘ K</kbd>
            </Button>
            <ThemeToggle aria-label="切换明暗主题" variant="circle" className="size-8 rounded-lg text-muted-foreground hover:bg-muted" iconClassName="size-4" />
            <Button variant="ghost" size="icon" aria-label="退出登录" onClick={logout}><LogOut className="size-4" /></Button>
          </div>
        </header>
        <main id="workspace" tabIndex={-1} className={section === "ai" ? "h-[calc(100dvh-4rem)] min-h-0 min-w-0 w-full overflow-hidden outline-none" : "mx-auto w-full max-w-[1600px] min-w-0 p-4 outline-none sm:p-7 lg:p-8"}>
          {logoutError && section !== "ai" && (
            <p role="alert" className="mb-4 text-sm text-destructive">
              {logoutError}
            </p>
          )}
          <h1 className={section === "ai" ? "sr-only" : "text-2xl font-medium tracking-tight"}>
            {section === "profile"
              ? "个人信息"
              : section === "dashboard"
                ? `欢迎回来，${me.display_name}`
                : current?.label}
          </h1>
          <p className={section === "ai" ? "sr-only" : "mt-2 text-sm leading-6 text-muted-foreground"}>
            {section === "profile"
              ? "你的账户与所属企业。"
              : section === "llm"
                ? "为企业选择对话模型，直接在此保存设置并测试连接。"
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
                            : section === "ai"
                              ? "查询有来源，开单先复核。"
                              : section === "products"
                                ? "管理商品规格、单位换算与价格。"
                                : section === "customers"
                                  ? "维护客户资料，连接销售与往来记录。"
                                  : section === "suppliers"
                                    ? "维护供应商资料，协同采购与往来业务。"
                                    : section === "settings"
                                      ? "管理基础资料与企业工作空间设置。"
                                      : "按权限查看和维护工作空间资料。"}
          </p>
          {section === "ai" ? (
            <AssistantWorkspace permissions={me.permissions} identityKey={`${me.organization_id}:${me.user_id}`} />
          ) : section === "llm" ? (
            <ProviderSettings permissions={me.permissions} identityKey={`${me.organization_id}:${me.user_id}`} />
          ) : section === "replenishment" ? (
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
              {me.permissions.includes("ai.provider.manage") && (
                <Link href="/settings/llm" className="group rounded-2xl border border-border bg-card/50 p-6 transition-colors hover:bg-card">
                  <h2 className="text-sm font-semibold">模型服务</h2>
                  <p className="mt-2 text-xs text-muted-foreground">选择对话模型服务，保存密钥并测试连接。</p>
                </Link>
              )}
              {Object.entries(configs)
                .filter(([key]) =>
                  ["categories", "brands", "units", "warehouses"].includes(key),
                )
                .map(([key, c]) => (
                  <Link
                    key={key}
                    href={`/settings/${key}`}
                    className="group rounded-2xl border border-border bg-card/50 p-6 transition-colors hover:bg-card"
                  >
                    <h2 className="text-sm font-semibold">{c.title}</h2>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {c.description}
                    </p>
                  </Link>
                ))}
            </section>
          ) : section === "profile" ? (
            <section className="mt-8 max-w-2xl rounded-2xl border border-border bg-card/50 p-7">
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
            <section className="mt-8 flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-card/40 p-8 text-center">
              <BarChart3 className="mb-4 size-7 text-muted-foreground" />
              <h2 className="text-base font-medium">{current?.label ?? "此功能"}尚未开放</h2>
              <p className="mt-2 text-sm text-muted-foreground">当前经营数据可在工作台查看。</p>
              <Link href="/dashboard" className="mt-5 rounded-full bg-primary px-5 py-2.5 text-sm text-primary-foreground">前往工作台</Link>
            </section>
          )}
        </main>
      </AnimatedSidebarInset>
      {(navigationBlocked || (section === "ai" && logoutError)) && (
        <div className="fixed top-20 right-4 left-4 z-[250] flex flex-col gap-2 sm:left-auto sm:max-w-md">
          {navigationBlocked && (
            <div role="alert" className="flex items-start gap-3 rounded-2xl border border-border bg-card p-4 shadow-lg">
              <p className="text-sm leading-6">当前提交的结果尚未确认，请先等待结果或重试原提交，再离开此页面。</p>
              <Button variant="ghost" size="icon" aria-label="关闭导航提示" className="shrink-0" onClick={() => setNavigationBlocked(false)}><X className="size-4" /></Button>
            </div>
          )}
          {section === "ai" && logoutError && (
            <div role="alert" className="flex items-start gap-3 rounded-2xl border border-border bg-card p-4 shadow-lg">
              <p className="text-sm leading-6 text-destructive">{logoutError}</p>
              <Button variant="ghost" size="icon" aria-label="关闭退出提示" className="shrink-0" onClick={() => setLogoutError("")}><X className="size-4" /></Button>
            </div>
          )}
        </div>
      )}
      <CommandPalette open={open} onOpenChange={(next) => { if (next) setMobileOpen(false); setOpen(next); }}
        dialogLabel="快捷导航" inputLabel="搜索页面" closeLabel="关闭快捷导航" listLabel="页面"
        placeholder="搜索页面…" emptyMessage="没有匹配的页面"
        items={navigation.map((item) => ({ id: item.path, label: item.label, icon: item.icon, group: "工作空间", keywords: [item.path], onSelect: () => navigate(`/${item.path}`) }))} />
    </AnimatedSidebarProvider>
  );
}
