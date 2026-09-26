import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "커피 소믈리에",
  description: "내 커피 취향을 알아주는 추천 앱",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#faf6f0" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-dvh font-sans antialiased">
        <main className="mx-auto min-h-dvh max-w-md px-4 pb-16 pt-6">{children}</main>
      </body>
    </html>
  );
}
