import { IBM_Plex_Sans, IBM_Plex_Sans_Condensed, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import Toaster from "./components/toaster";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
});

const plexCond = IBM_Plex_Sans_Condensed({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-plex-cond",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
});

export const metadata = {
  title: "AI Geotechnical Assistant",
  description: "AI-powered geotechnical assistant using RAG with Groq, MongoDB Atlas, and Redis.",
  icons: {
    icon: "/openai.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    /* The font variable classes MUST sit on <html>: globals.css declares
       --font-sans/--font-cond/--font-mono on :root, and a custom property's
       var() references resolve at the element that DECLARES it — with the
       classes on <body>, --font-plex-* didn't exist at :root, --font-sans
       computed to invalid, and font-family fell through to the browser's
       serif default. */
    <html lang="en" className={`${plexSans.variable} ${plexCond.variable} ${plexMono.variable}`}>
      <body>
        {children}
        <Toaster />
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}
