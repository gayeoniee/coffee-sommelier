"use client";
import { useEffect } from "react";

// Registers the app-shell service worker in production only, so local dev
// never serves stale cached responses.
export default function SwRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js");
    }
  }, []);
  return null;
}
