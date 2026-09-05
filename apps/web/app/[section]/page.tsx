import { notFound, redirect } from "next/navigation";
import { ERPShell } from "@/components/erp-shell";

const sections = [
  "dashboard",
  "ai",
  "sales",
  "purchase",
  "inventory",
  "products",
  "customers",
  "suppliers",
  "funds",
  "finance",
  "reports",
  "settings",
  "profile",
];
export default async function SectionPage({
  params,
  searchParams,
}: {
  params: Promise<{ section: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { section } = await params;
  if (!sections.includes(section)) notFound();
  if (section === "finance") {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(await searchParams)) {
      for (const item of Array.isArray(value)
        ? value
        : value === undefined
          ? []
          : [value])
        query.append(key, item);
    }
    redirect(`/funds${query.size ? `?${query}` : ""}`);
  }
  return <ERPShell section={section} />;
}
