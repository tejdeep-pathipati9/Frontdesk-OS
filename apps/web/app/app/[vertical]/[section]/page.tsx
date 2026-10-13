// Dynamic dashboard route: validates the vertical/section params and renders Dashboard (or redirects/404s).
import Dashboard from "@/components/dashboard";
import { notFound, redirect } from "next/navigation";
const sections = ["overview", "floor", "reservations", "waitlist", "kitchen", "menu", "orders", "calls", "attention", "assistant", "integrations", "settings", "calendar", "staff", "services", "voice"];
export default async function Page({ params }: { params: Promise<{ vertical: string; section: string }> }) {
  const { vertical, section } = await params;
  if (!["restaurant", "salon"].includes(vertical)) notFound();
  if (section === "customers") redirect(`/app/${vertical}/assistant`);
  if (!sections.includes(section)) notFound();
  return <Dashboard vertical={vertical as "restaurant" | "salon"} section={section} />;
}
