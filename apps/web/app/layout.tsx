// Root Next.js layout: sets site metadata, global styles and the html/body shell.
import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "FrontDesk OS · Your operations, connected", description: "Capacity-aware front desk for restaurants and salons" };
export default function RootLayout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
