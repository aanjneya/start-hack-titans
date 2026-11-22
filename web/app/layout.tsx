import type { Metadata } from "next";
import localFont from "next/font/local";
import { Suspense } from "react";
import WorkspaceNav, { NavigationBar } from "./workspace-nav";
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
        <Suspense fallback={<NavigationBar />}><WorkspaceNav /></Suspense>
        <div id="workspace-content" tabIndex={-1}>{children}</div>
      </body>
    </html>
  );
}
