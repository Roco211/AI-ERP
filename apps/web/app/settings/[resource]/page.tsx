import { notFound } from "next/navigation";
import { ERPShell } from "@/components/erp-shell";
export default async function Page({
  params,
}: {
  params: Promise<{ resource: string }>;
}) {
  const { resource } = await params;
  if (!["categories", "brands", "units", "warehouses"].includes(resource))
    notFound();
  return <ERPShell section="settings" catalogResource={resource} />;
}
