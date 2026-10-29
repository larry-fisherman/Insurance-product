import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "CGL Workspace",
  description: "Commercial general liability submission preparation with broker review.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
