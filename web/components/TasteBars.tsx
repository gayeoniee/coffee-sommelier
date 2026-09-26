import { ATTR_KO } from "@/lib/labels";
import type { Card, Profile } from "@/lib/types";

const KEYS = ["acidity", "body", "sweetness"] as const;

export default function TasteBars({ card, profile }: { card: Card; profile?: Profile | null }) {
  return (
    <div className="space-y-1.5 text-xs">
      {KEYS.map((k) => {
        const v = card[k];
        return (
          <div key={k} className="flex items-center gap-2">
            <span className="w-8 shrink-0 text-roast">{ATTR_KO[k]}</span>
            <div className="relative h-2 flex-1 rounded-full bg-crema">
              {v != null && <div className="absolute h-2 rounded-full bg-roast" style={{ width: `${(v / 5) * 100}%` }} />}
              {profile && (
                <div className="absolute -top-0.5 h-3 w-0.5 bg-accent" style={{ left: `${(profile[k] / 5) * 100}%` }}
                     aria-label={`내 선호 ${profile[k].toFixed(1)}`} />
              )}
            </div>
            <span className="w-7 text-right tabular-nums">{v == null ? "?" : v.toFixed(1)}</span>
          </div>
        );
      })}
      {profile && <p className="text-[11px] text-roast/80"><span className="text-accent">|</span> 내 선호</p>}
    </div>
  );
}
