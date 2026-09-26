"use client";
import { useEffect } from "react";

// Registers the app-shell service worker in production only, so local dev
// never serves stale cached responses.
export default function SwRegister() {
  useEffect(() => {
    if (!(process.env.NODE_ENV === "production" && "serviceWorker" in navigator)) return;

    let cancelled = false;
    let onVisible: (() => void) | null = null;

    navigator.serviceWorker.register("/sw.js").then((registration) => {
      if (cancelled) return;
      // The browser only re-checks /sw.js for a new version occasionally (roughly on
      // navigation, at least every 24h). Ask explicitly right after registering (page load)
      // and again whenever the tab regains focus, so a shell that changed while this tab sat
      // in the background gets picked up instead of silently staying stale.
      registration.update();
      onVisible = () => {
        if (document.visibilityState === "visible") registration.update();
      };
      document.addEventListener("visibilitychange", onVisible);
    });

    return () => {
      cancelled = true;
      if (onVisible) document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  return null;
}
