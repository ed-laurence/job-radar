import "@/lib/tokens.css";
import "./globals.css";
import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { fontVariables } from "./fonts";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Job Radar",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  colorScheme: "light dark",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Render per request so Next can stamp the CSP nonce from proxy.ts on its scripts.
  await connection();
  return (
    <html lang="en" className={fontVariables}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
