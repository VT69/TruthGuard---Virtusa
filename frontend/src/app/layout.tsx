import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { TopBar } from "@/components/TopBar";

export const metadata: Metadata = {
  title: "TruthGuard",
  description: "Adversarial verification for multi-agent document Q&A over inconsistent corpora",
};

const themeScript = `try{var t=localStorage.getItem('tg-theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d)document.documentElement.classList.add('dark')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen font-sans antialiased">
        <Providers>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2"
          >
            Skip to content
          </a>
          <TopBar />
          <main id="main" className="mx-auto w-full max-w-[1500px] px-4 pb-16 pt-4 sm:px-6">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
