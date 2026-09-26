"use client";
import { useEffect } from "react";
import { useSession } from "@/hooks/useSession";

export default function WakeGate({ children, onReady }: { children: React.ReactNode; onReady?: (hasProfile: boolean) => void }) {
  const { state, hasProfile, error, retry } = useSession();

  useEffect(() => {
    if (state === "ready") onReady?.(hasProfile);
  }, [state, hasProfile, onReady]);

  if (state === "ready") return <>{children}</>;
  if (state === "error") {
    return (
      <div className="mt-24 text-center">
        <p className="mb-4">{error}</p>
        <button onClick={retry} className="min-h-11 rounded-full bg-espresso px-6 text-cream">다시 시도</button>
      </div>
    );
  }
  return (
    <div className="mt-24 text-center text-roast" role="status">
      <p className="animate-pulse text-4xl">☕</p>
      <p className="mt-4">{state === "slow" ? "서버를 깨우는 중이에요… (처음엔 30초쯤 걸려요)" : "불러오는 중…"}</p>
    </div>
  );
}
