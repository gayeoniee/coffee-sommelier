"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import BeanInput from "@/components/BeanInput";
import BrandPicker from "@/components/BrandPicker";
import LogSheet from "@/components/LogSheet";
import ResultCard from "@/components/ResultCard";
import WakeGate from "@/components/WakeGate";
import { useStream } from "@/hooks/useStream";
import { api } from "@/lib/api";
import type { Brand, Card, Profile } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  const onReady = useCallback((hasProfile: boolean) => { if (!hasProfile) router.replace("/onboarding"); }, [router]);
  return <WakeGate onReady={onReady}><HomeBody /></WakeGate>;
}

function HomeBody() {
  const [tab, setTab] = useState<"franchise" | "indie">("franchise");
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brand, setBrand] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [logging, setLogging] = useState<Card | null>(null);
  const { state, start, reset } = useStream();

  useEffect(() => {
    api.brands().then(setBrands).catch(() => setBrands([]));
    api.me().then((m) => setProfile(m.profile)).catch(() => setProfile(null));
  }, []);

  const switchTab = (t: "franchise" | "indie") => { setTab(t); setBrand(null); reset(); };

  return (
    <>
      <header className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-bold">지금 어디세요?</h1>
        <Link href="/me" className="min-h-11 rounded-full bg-white/70 px-4 py-2.5 text-sm ring-1 ring-crema">내 취향</Link>
      </header>
      <div role="tablist" className="mb-4 grid grid-cols-2 rounded-xl bg-crema p-1 text-sm">
        {(["franchise", "indie"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => switchTab(t)}
            className={`min-h-11 rounded-lg ${tab === t ? "bg-white font-semibold shadow-sm" : ""}`}>
            {t === "franchise" ? "프랜차이즈" : "개인 카페"}
          </button>
        ))}
      </div>
      {tab === "franchise" ? (
        <BrandPicker brands={brands} selected={brand} onPick={(b) => { setBrand(b.key); start("/recommend", { brand_key: b.key }); }} />
      ) : (
        <BeanInput onPick={(h) => start("/analyze", { coffee_id: h.id })} onSubmit={(text) => start("/analyze", { text })} />
      )}
      <section className="mt-6 space-y-4" aria-live="polite">
        {state.status === "loading" && state.cards.length === 0 && <p className="animate-pulse text-roast">취향에 맞는 커피를 고르는 중…</p>}
        {state.empty && <p className="rounded-xl bg-white/70 p-4 text-sm">{state.empty}</p>}
        {state.error && <p className="rounded-xl bg-warn/10 p-4 text-sm text-warn">{state.error}</p>}
        {state.cards.map((c) => (
          <ResultCard key={c.key} card={c} explanation={state.explanations[c.key]} profile={profile} onLog={setLogging} />
        ))}
      </section>
      {logging && (
        <LogSheet card={logging} onClose={() => setLogging(null)} onSaved={(p) => setProfile(p)} />
      )}
    </>
  );
}
