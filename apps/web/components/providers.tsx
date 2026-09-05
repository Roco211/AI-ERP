"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { installHistoryTracking } from "@/lib/navigation-history";

export function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => installHistoryTracking(), []);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: 0 } },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
