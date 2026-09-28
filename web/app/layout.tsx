import type { Metadata, Viewport } from "next";
import "./globals.css";
import VariantBanner from "@/components/VariantBanner";
import SwRegister from "@/components/SwRegister";

export const metadata: Metadata = {
  title: "커피 소믈리에",
  description: "내 커피 취향을 알아주는 추천 앱",
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#faf6f0" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-dvh font-sans antialiased">
        <VariantBanner />
        <main className="mx-auto min-h-dvh max-w-md px-4 pb-16 pt-6">{children}</main>
        <footer className="mx-auto max-w-md px-4 pb-6 text-center text-[11px] leading-snug text-roast/70">
          의료적 조언이 아닙니다. 카페인 값은 브랜드 공시값이며 실제 음료와 다를 수 있어요.
        </footer>
        <SwRegister />
      </body>
    </html>
  );
}
