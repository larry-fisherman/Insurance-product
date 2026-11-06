import type { Metadata } from "next";
import { DM_Sans, Nunito } from "next/font/google";
import Link from "next/link";
import { appOrigin, marketingOrigin } from "../lib/config";
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

export const metadata: Metadata = {
  metadataBase: new URL(marketingOrigin),
  title: {
    default: "CGL Workspace · From documents to a reviewed submission",
    template: "%s · CGL Workspace",
  },
  description:
    "Document intelligence and submission preparation for commercial insurance agencies, with evidence and broker review at every step.",
  robots: { index: false, follow: false },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable}`}>
      <body>
        <a className="skip" href="#content">
          Skip to content
        </a>
        <div className="environment">
          Local product preview · Fictional documents and inquiries only
        </div>
        <header className="site-header">
          <Link href="/" className="brand">
            <span aria-hidden>▱</span> CGL Workspace
          </Link>
          <nav aria-label="Main navigation">
            <Link href="/product">Product</Link>
            <Link href="/how-it-works">How it works</Link>
            <Link href="/security">Security</Link>
            <Link href="/pricing">Pricing</Link>
          </nav>
          <div className="header-actions">
            <a href={appOrigin + "/login"}>Sign in ↗</a>
            <Link className="button small" href="/request-demo">
              Request a demo
            </Link>
          </div>
        </header>
        <main id="content">{children}</main>
        <footer>
          <div>
            <Link className="brand" href="/">
              ▱ CGL Workspace
            </Link>
            <p>Evidence connected. Decisions recorded.</p>
            <small>Commercial general liability · Broker operations</small>
          </div>
          <nav aria-label="Footer">
            <Link href="/contact">Contact</Link>
            <Link href="/privacy">Privacy</Link>
            <Link href="/terms">Terms</Link>
            <Link href="/security">Data practices</Link>
            <a href={appOrigin + "/login"}>Open application ↗</a>
          </nav>
        </footer>
      </body>
    </html>
  );
}
