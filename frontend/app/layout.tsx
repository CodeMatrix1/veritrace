import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Veritrace",
  description: "Evidence-backed verification for Indian public claims",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="sticky top-0 z-10 border-b border-white/10 bg-[#090d16]/80 backdrop-blur">
          <div className="mx-auto flex max-w-4xl items-center justify-between gap-3 px-5 py-3">
            <Link href="/" className="flex items-center gap-2 text-lg font-semibold text-white">
              <span aria-hidden className="logo-mark" />
              Veritrace
            </Link>
            <nav className="flex gap-4 text-sm text-slate-300">
              <Link href="/" className="hover:text-white">
                New check
              </Link>
              <Link href="/chat" className="hover:text-white">
                Ask government info
              </Link>
              <Link href="/audits" className="hover:text-white">
                Your audits
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-4xl px-5 py-8">{children}</main>
      </body>
    </html>
  );
}
