import { sourceLabel } from "@/lib/labels";
import type { Explanation } from "@/lib/stream";
import type { Card, Profile } from "@/lib/types";
import TasteBars from "./TasteBars";

type Props = { card: Card; explanation?: Explanation; profile?: Profile | null; onLog: (card: Card) => void };

export default function ResultCard({ card, explanation, profile, onLog }: Props) {
  const text = explanation?.text ? explanation.text : card.template;
  const writing = explanation?.status === "streaming";
  return (
    <article className="rounded-2xl bg-white/80 p-4 shadow-sm ring-1 ring-crema">
      {card.violation && (
        <p role="alert" className="mb-3 rounded-lg bg-warn/10 px-3 py-2 text-sm font-medium text-warn">⚠ {card.violation}</p>
      )}
      <header className="flex items-start justify-between gap-3">
        <div>
          {card.brand && <p className="text-xs text-roast">{card.brand}</p>}
          <h3 className="text-lg font-bold leading-snug">{card.name}</h3>
        </div>
        <span className="shrink-0 rounded-full bg-espresso px-3 py-1 text-sm font-bold text-cream">{card.score}%</span>
      </header>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {card.is_decaf && <span className="rounded-full bg-leaf px-2 py-0.5 text-white">디카페인</span>}
        {card.order_decaf && (
          <span className="rounded-full bg-leaf/15 px-2 py-0.5 text-leaf">
            디카페인으로 변경{card.decaf_surcharge_krw ? ` +${card.decaf_surcharge_krw}원` : ""}
          </span>
        )}
        {card.caffeine_mg != null && <span className="rounded-full bg-crema px-2 py-0.5">카페인 {card.caffeine_mg}mg</span>}
        {card.tags_ko.slice(0, 4).map((t, i) => <span key={`${i}:${t}`} className="rounded-full bg-crema px-2 py-0.5">{t}</span>)}
      </div>
      <div className="mt-3"><TasteBars card={card} profile={profile} /></div>
      <p className={`mt-3 text-sm leading-relaxed ${writing && !explanation?.text ? "text-roast/80" : ""}`}>{text}</p>
      {writing && <p className="mt-1 animate-pulse text-[11px] text-roast">설명을 쓰는 중…</p>}
      {card.evidence && card.evidence.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-roast">
          {card.evidence.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
      <footer className="mt-3 flex items-center justify-between">
        <span className="text-[11px] text-roast/80">{sourceLabel(card)}</span>
        <button onClick={() => onLog(card)} className="min-h-11 rounded-full bg-accent px-4 text-sm font-semibold text-white">
          마셔봤어요
        </button>
      </footer>
    </article>
  );
}
