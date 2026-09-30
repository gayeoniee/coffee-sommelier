"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import WakeGate from "@/components/WakeGate";
import { ApiError, api } from "@/lib/api";
import { ATTR_KO, RULE_KO, caffeineLimitLabel, fmt1 } from "@/lib/labels";
import { CHIPS, type Me } from "@/lib/types";

export default function MePage() {
  return <WakeGate><MeBody /></WakeGate>;
}

function MeBody() {
  const [me, setMe] = useState<Me | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nick, setNick] = useState("");
  const [nickMsg, setNickMsg] = useState<{ text: string; kind: "success" | "error" } | null>(null);
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(() => {
    api.me()
      .then((m) => { setMe(m); setNick(m.nickname ?? ""); setLoadError(null); })
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : "불러오지 못했어요"));
  }, []);

  useEffect(() => { load(); }, [load, attempt]);

  if (!me) {
    if (loadError) {
      return (
        <div className="mt-24 text-center">
          <p className="mb-4 text-warn">{loadError}</p>
          <button onClick={() => setAttempt((n) => n + 1)} className="min-h-11 rounded-full bg-espresso px-6 text-cream">다시 시도</button>
        </div>
      );
    }
    return <p className="text-roast">불러오는 중…</p>;
  }
  const p = me.profile;
  const snapshots = [...me.history].reverse().slice(-10);

  async function saveNick() {
    try {
      const r = await api.nickname(nick);
      setNick(r.nickname ?? "");
      setNickMsg({ text: "저장했어요", kind: "success" });
    } catch (e) {
      setNickMsg({ text: e instanceof ApiError ? e.message : "저장하지 못했어요", kind: "error" });
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">내 취향</h1>
        <Link href="/" className="min-h-11 rounded-full bg-white/70 px-4 py-2.5 text-sm ring-1 ring-crema">추천 받기</Link>
      </header>
      <p className="text-xs text-roast">게스트 기록은 이 브라우저에만 저장돼요. 기기를 바꾸거나 브라우저 데이터를 지우면 사라져요.</p>
      <section className="flex gap-2">
        <input value={nick} onChange={(e) => setNick(e.target.value)} maxLength={30} placeholder="닉네임 (선택)" aria-label="닉네임"
          className="min-h-11 flex-1 rounded-xl bg-white px-3 ring-1 ring-crema" />
        <button onClick={saveNick} className="min-h-11 rounded-xl bg-espresso px-4 text-cream">저장</button>
      </section>
      {nickMsg && <p className={`text-sm ${nickMsg.kind === "error" ? "text-warn" : "text-leaf"}`}>{nickMsg.text}</p>}
      {p && (
        <section className="rounded-2xl bg-white/80 p-4 ring-1 ring-crema">
          <p className="text-sm">카페인: <b>{RULE_KO[p.caffeine_rule]}</b> · 우유: <b>{p.milk_ok ? "가능" : "불가"}</b> · 기록 {p.n_updates}회</p>
          <p className="mt-1 text-sm">오늘 카페인 한도: <b>{caffeineLimitLabel(p.daily_caffeine_limit_mg)}</b></p>
          {(["acidity", "body", "sweetness"] as const).map((k) => (
            <div key={k} className="mt-3 flex items-center gap-2 text-sm">
              <span className="w-10 text-roast">{ATTR_KO[k]}</span>
              <div className="h-2 flex-1 rounded-full bg-crema"><div className="h-2 rounded-full bg-roast" style={{ width: `${(p[k] / 5) * 100}%` }} /></div>
              <span className="w-8 text-right tabular-nums">{fmt1(p[k])}</span>
            </div>
          ))}
          <p className="mt-3 text-sm">
            좋아하는 향미: {CHIPS.filter((c) => (p.flavor_weights[c.key] ?? 0) > 0.2).map((c) => c.ko).join(", ") || "아직 없어요"}
          </p>
          <Link href="/onboarding?redo=1" className="mt-3 inline-block text-sm text-accent underline">취향 다시 설정</Link>
        </section>
      )}
      <section>
        <h2 className="mb-2 font-semibold">취향 변화</h2>
        {snapshots.length === 0 ? <p className="text-sm text-roast">아직 기록이 없어요.</p> : (
          <div className="overflow-x-auto rounded-xl bg-white/80 ring-1 ring-crema">
            <table className="w-full text-xs tabular-nums">
              <thead>
                <tr className="text-roast">
                  <th className="p-1.5 text-left font-normal">#</th>
                  <th className="p-1.5 text-right font-normal">{ATTR_KO.acidity}</th>
                  <th className="p-1.5 text-right font-normal">{ATTR_KO.body}</th>
                  <th className="p-1.5 text-right font-normal">{ATTR_KO.sweetness}</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((h, i) => (
                  <tr key={i} className="border-t border-crema">
                    <td className="p-1.5">{i + 1}</td>
                    <td className="p-1.5 text-right">{fmt1(h.snapshot.acidity)}</td>
                    <td className="p-1.5 text-right">{fmt1(h.snapshot.body)}</td>
                    <td className="p-1.5 text-right">{fmt1(h.snapshot.sweetness)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
