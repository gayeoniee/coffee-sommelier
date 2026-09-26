"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CoffeeHit } from "@/lib/types";

const DEBOUNCE_MS = 250;

export default function BeanInput({ onPick, onSubmit }: { onPick: (hit: CoffeeHit) => void; onSubmit: (text: string) => void }) {
  const [text, setText] = useState("");
  const [hits, setHits] = useState<CoffeeHit[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = text.trim();
    // Below 2 chars we neither fetch nor clear state here (that would be a
    // synchronous setState-in-effect); the render below hides stale hits instead.
    if (q.length < 2) return;
    let alive = true;
    const t = setTimeout(() => {
      api.search(q).then((h) => alive && setHits(h)).catch(() => alive && setHits([]));
    }, DEBOUNCE_MS);
    timerRef.current = t;
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [text]);

  // Submitting or picking a hit ends the search: a debounce still in flight from an earlier
  // keystroke must not land afterwards and bring the hit list back.
  function cancelPendingSearch() {
    if (timerRef.current) clearTimeout(timerRef.current);
  }

  const showHits = text.trim().length >= 2 && hits.length > 0;

  return (
    <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { cancelPendingSearch(); setHits([]); onSubmit(text.trim()); } }}>
      <label className="mb-1 block text-sm text-roast" htmlFor="bean">원두 카드에 적힌 내용을 입력하세요</label>
      <div className="flex gap-2">
        <input id="bean" type="search" value={text} onChange={(e) => setText(e.target.value)} maxLength={300}
          placeholder="예: 에티오피아 예가체프 워시드 디카페인"
          className="min-h-11 flex-1 rounded-xl bg-white px-3 ring-1 ring-crema focus:outline-none focus:ring-roast" />
        <button type="submit" className="min-h-11 rounded-xl bg-espresso px-4 text-cream">분석</button>
      </div>
      {showHits && (
        <ul className="mt-2 overflow-hidden rounded-xl bg-white ring-1 ring-crema">
          {hits.map((h) => (
            <li key={h.id}>
              <button type="button" onClick={() => { cancelPendingSearch(); setHits([]); onPick(h); }}
                className="block min-h-11 w-full px-3 py-2 text-left text-sm hover:bg-crema">
                {h.name}
                <span className="ml-1 text-xs text-roast">{[h.roaster, h.origin_country, h.is_decaf ? "디카페인" : null].filter(Boolean).join(" · ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
