import Dashboard from "@/components/dashboard";
import { notFound } from "next/navigation";
const sections = ["overview", "floor", "reservations", "waitlist", "kitchen", "menu", "orders", "calls", "attention", "customers", "integrations", "settings", "calendar", "staff", "services", "voice"];
export default async function Page({ params }: { params: Promise<{ vertical: string; section: string }> }) {
  const { vertical, section } = await params;
  if (!["restaurant", "salon"].includes(vertical) || !sections.includes(section)) notFound();
  return <Dashboard vertical={vertical as "restaurant" | "salon"} section={section} />;
}
