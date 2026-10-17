import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RentalScamChecker",
  description: "Check a rental listing for risk signals before you send any money.",
};

export const viewport: Viewport = {
  themeColor: "#f8fafc",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en-CA"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-10 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:shadow"
        >
          Skip to main content
        </a>
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-4">
            <Link
              href="/"
              className="flex items-center gap-2 rounded-md font-semibold tracking-tight text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-6 w-6 text-teal-700">
                <path
                  fill="currentColor"
                  d="M12 3 3 10v10a1 1 0 0 0 1 1h6v-6h4v6h6a1 1 0 0 0 1-1V10l-9-7Z"
                  opacity=".18"
                />
                <path
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                  d="M3 10 12 3l9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V10Z"
                />
                <path
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="m8.5 14 2.5 2.5 4.5-5"
                />
              </svg>
              RentalScamChecker
            </Link>
          </div>
        </header>
        <main id="main" className="flex-1">
          {children}
        </main>
        <footer className="border-t border-slate-200 bg-white">
          <div className="mx-auto max-w-3xl space-y-2 px-4 py-6 text-sm text-slate-600">
            <p>
              We report risk signals and the evidence behind them, not verdicts. A low-risk result
              can&apos;t prove a listing is genuine, and risk signals don&apos;t prove it isn&apos;t.
            </p>
            <p>
              Listings are only checked when you submit them. Phone numbers and emails are stored
              only as one-way fingerprints.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
