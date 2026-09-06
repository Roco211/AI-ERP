"use client";
import { ThemeProvider } from "next-themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLayoutEffect, useState } from "react";
import { installHistoryTracking } from "@/lib/navigation-history";

export function Providers({ children }: { children: React.ReactNode }) {
  useLayoutEffect(() => installHistoryTracking(), []);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: 0 } },
      }),
  );
  return <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} storageKey="forge-theme"><QueryClientProvider client={client}>{children}</QueryClientProvider></ThemeProvider>;
}
