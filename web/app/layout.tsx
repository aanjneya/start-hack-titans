import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import "./globals.css";

// One family; the width axis gives the condensed "shipping label" numbers.
const archivo = Archivo({ subsets: ["latin"], axes: ["wdth"], variable: "--font-archivo" });

export const metadata: Metadata = {
  title: "Line control",
  description: "What can stop the Grand Heist 6 packing line, and what to decide now.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${archivo.variable} h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
