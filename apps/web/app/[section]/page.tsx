import { notFound } from "next/navigation";
import { ERPShell } from "@/components/erp-shell";

const sections = ["dashboard","ai","sales","purchase","inventory","products","customers","suppliers","finance","reports","settings","profile"];
export default async function SectionPage({ params }: { params: Promise<{section: string}> }) {
  const { section } = await params;
  if (!sections.includes(section)) notFound();
  return <ERPShell section={section}/>;
}
