import type { Metadata } from "next";
import localFont from "next/font/local";
import { BackgroundBeams } from "@/components/ui/background-beams";
import "./globals.css";

// One family; the width axis gives the condensed "shipping label" numbers.
const archivo = localFont({
  src: "./fonts/Archivo.woff2", variable: "--font-archivo", weight: "100 900", display: "swap",
  declarations: [{ prop: "font-stretch", value: "62% 125%" }],
});

export const metadata: Metadata = {
  title: "Line control",
  description: "Packing forecasts, supplier risk and the procurement decisions that need your attention.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${archivo.variable} h-full antialiased`}>
      <body className="min-h-full">
        {/* The animated beams sit behind every page; content scrolls over them. */}
        <BackgroundBeams className="fixed" />
        <div className="relative z-10 min-h-full">{children}</div>
      </body>
    </html>
  );
}
