import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "커피 소믈리에",
    short_name: "커피소믈리에",
    description: "내 커피 취향을 알아주는 추천 앱",
    start_url: "/",
    display: "standalone",
    background_color: "#faf6f0",
    theme_color: "#faf6f0",
    lang: "ko",
    categories: ["food", "lifestyle"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    screenshots: [
      { src: "/screenshots/onboarding.png", sizes: "1170x1992", type: "image/png", form_factor: "narrow" },
      { src: "/screenshots/home.png", sizes: "1170x5010", type: "image/png", form_factor: "narrow" },
      { src: "/screenshots/log.png", sizes: "1170x1992", type: "image/png", form_factor: "narrow" },
      { src: "/screenshots/me.png", sizes: "1170x1992", type: "image/png", form_factor: "narrow" },
    ],
  };
}
