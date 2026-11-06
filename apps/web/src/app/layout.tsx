import type { Metadata } from "next";
import { DM_Sans, Nunito } from "next/font/google";

import "../../../../packages/web-config/tokens.css";
import "./globals.css";

const display = Nunito({
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-display",
  fallback: ["Arial", "Helvetica", "sans-serif"],
});
const sans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  fallback: ["Arial", "Helvetica", "sans-serif"],
});

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "CGL Workspace",
  description: "Commercial general liability submission preparation with broker review.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable}`}>
      <body>{children}</body>
    </html>
  );
}
