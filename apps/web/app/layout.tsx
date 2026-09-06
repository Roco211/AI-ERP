import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = { title: "Forge ERP", description: "五金商贸工作台" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html suppressHydrationWarning lang="zh-CN" className={`${GeistSans.variable} ${GeistMono.variable}`}><body><Providers>{children}</Providers></body></html>;
}
