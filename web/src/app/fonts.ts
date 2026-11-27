import { Bricolage_Grotesque, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";

// Self-hosted at build time by next/font, so the CSP can stay font-src 'self'.
// Each sets a CSS variable that src/lib/tokens.css reads into --font-display/--font-sans/--font-mono.
export const display = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["600", "700"],
  display: "swap",
  variable: "--font-bricolage",
});

export const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-plex-sans",
});

export const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-plex-mono",
});

export const fontVariables = `${display.variable} ${sans.variable} ${mono.variable}`;
