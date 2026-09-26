"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import WakeGate from "@/components/WakeGate";
import { ApiError, api } from "@/lib/api";
import { ATTR_KO, RULE_KO } from "@/lib/labels";
import { CHIPS, type Me } from "@/lib/types";

export default function MePage() {
  return <WakeGate><MeBody /></WakeGate>;
}

function MeBody() {
  const [me, setMe] = useState<Me | null>(null);
  const [nick, setNick] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.me().then((m) => { setMe(m); setNick(m.nickname ?? ""); }).catch((e) => setMsg(e instanceof ApiError ? e.message : "불러오지 못했어요"));
  }, []);

  if (!me) return <p className="text-roast">{msg ?? "불러오는 중…"}</p>;
  const p = me.profile;

  async function saveNick() {
    try {
      const r = await api.nickname(nick);
      setNick(r.nickname ?? "");
      setMsg("저장했어요");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "저장하지 못했어요");
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">내 취향</h1>
        <Link href="/" className="min-h-11 rounded-full bg-white/70 px-4 py-2.5 text-sm ring-1 ring-crema">추천 받기</Link>
      </header>
      <section className="flex gap-2">
        <input value={nick} onChange={(e) => setNick(e.target.value)} maxLength={30} placeholder="닉네임 (선택)" aria-label="닉네임"
          className="min-h-11 flex-1 rounded-xl bg-white px-3 ring-1 ring-crema" />
        <button onClick={saveNick} className="min-h-11 rounded-xl bg-espresso px-4 text-cream">저장</button>
      </section>
      {msg && <p className="text-sm text-leaf">{msg}</p>}
      {p && (
        <section className="rounded-2xl bg-white/80 p-4 ring-1 ring-crema">
          <p className="text-sm">카페인: <b>{RULE_KO[p.caffeine_rule]}</b> · 우유: <b>{p.milk_ok ? "가능" : "불가"}</b> · 기록 {p.n_updates}회</p>
          {(["acidity", "body", "sweetness"] as const).map((k) => (
            <div key={k} className="mt-3 flex items-center gap-2 text-sm">
              <span className="w-10 text-roast">{ATTR_KO[k]}</span>
              <div className="h-2 flex-1 rounded-full bg-crema"><div className="h-2 rounded-full bg-roast" style={{ width: `${(p[k] / 5) * 100}%` }} /></div>
              <span className="w-8 text-right tabular-nums">{p[k].toFixed(1)}</span>
            </div>
          ))}
          <p className="mt-3 text-sm">
            좋아하는 향미: {CHIPS.filter((c) => (p.flavor_weights[c.key] ?? 0) > 0.2).map((c) => c.ko).join(", ") || "아직 없어요"}
          </p>
          <Link href="/onboarding?redo=1" className="mt-3 inline-block text-sm text-accent underline">취향 다시 설정</Link>
        </section>
      )}
      <section>
        <h2 className="mb-2 font-semibold">산미 선호 변화</h2>
        {/* backend returns history newest-first; reverse to chronological order, then
            keep the last 10 entries of that (= the 10 most recent, oldest of those first) */}
        <ol className="flex flex-wrap gap-1 text-xs tabular-nums">
          {[...me.history].reverse().slice(-10).map((h, i) => (
            <li key={i} className="rounded bg-crema px-2 py-1">{h.snapshot.acidity.toFixed(1)}</li>
          ))}
        </ol>
      </section>
      <section>
        <h2 className="mb-2 font-semibold">최근 기록</h2>
        {me.tastings.length === 0 ? <p className="text-sm text-roast">아직 기록이 없어요.</p> : (
          <ul className="space-y-2">
            {me.tastings.map((t) => (
              <li key={t.id} className="rounded-xl bg-white/80 p-3 text-sm ring-1 ring-crema">
                <span className="text-accent">{"★".repeat(t.rating)}</span> {t.name}
                {t.note && <p className="mt-1 text-roast">{t.note}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
