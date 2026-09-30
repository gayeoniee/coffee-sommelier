"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import BeanInput from "@/components/BeanInput";
import BrandPicker from "@/components/BrandPicker";
import CaffeineToday from "@/components/CaffeineToday";
import LogSheet from "@/components/LogSheet";
import ResultCard from "@/components/ResultCard";
import WakeGate from "@/components/WakeGate";
import { useStream } from "@/hooks/useStream";
import { api } from "@/lib/api";
import type { Brand, Card, Profile, Today } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  const onReady = useCallback((hasProfile: boolean) => { if (!hasProfile) router.replace("/onboarding"); }, [router]);
  return <WakeGate onReady={onReady}><HomeBody /></WakeGate>;
}

function HomeBody() {
  const [tab, setTab] = useState<"franchise" | "indie">("franchise");
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brandsError, setBrandsError] = useState(false);
  const [brand, setBrand] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [today, setToday] = useState<Today | null>(null);
  const [logging, setLogging] = useState<Card | null>(null);
  const [brandsAttempt, setBrandsAttempt] = useState(0);
  const { state, start, reset } = useStream();

  const refreshToday = useCallback(() => {
    api.today().then(setToday).catch(() => setToday(null));
  }, []);

  useEffect(() => {
    api.brands().then(setBrands).catch(() => setBrandsError(true));
  }, [brandsAttempt]);

  useEffect(() => {
    api.me().then((m) => setProfile(m.profile)).catch(() => setProfile(null));
    refreshToday();
  }, [refreshToday]);

  const switchTab = (t: "franchise" | "indie") => { setTab(t); setBrand(null); reset(); };
  const retryBrands = () => { setBrandsError(false); setBrandsAttempt((n) => n + 1); };

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
      <CaffeineToday today={today} />
      {tab === "franchise" ? (
        brandsError ? (
          <div className="rounded-xl bg-warn/10 p-4 text-sm">
            <p aria-live="polite" className="text-warn">브랜드 목록을 불러오지 못했어요</p>
            <button onClick={retryBrands} className="mt-3 min-h-11 rounded-full bg-espresso px-4 text-cream">다시 시도</button>
          </div>
        ) : (
          <BrandPicker brands={brands} selected={brand} onPick={(b) => { setBrand(b.key); start("/recommend", { brand_key: b.key }); }} />
        )
      ) : (
        <BeanInput onPick={(h) => start("/analyze", { coffee_id: h.id })} onSubmit={(text) => start("/analyze", { text })} />
      )}
      <section className="mt-6 space-y-4" aria-busy={state.status === "loading"}>
        {state.status === "loading" && state.cards.length === 0 && (
          <p aria-live="polite" className="animate-pulse text-roast">취향에 맞는 커피를 고르는 중…</p>
        )}
        {state.empty && <p aria-live="polite" className="rounded-xl bg-white/70 p-4 text-sm">{state.empty}</p>}
        {state.error && <p aria-live="polite" className="rounded-xl bg-warn/10 p-4 text-sm text-warn">{state.error}</p>}
        {state.cards.map((c) => (
          <ResultCard key={c.key} card={c} explanation={state.explanations[c.key]} profile={profile} onLog={setLogging} />
        ))}
      </section>
      {logging && (
        <LogSheet card={logging} onClose={() => setLogging(null)} onSaved={(p) => { setProfile(p); refreshToday(); }} />
      )}
    </>
  );
}
