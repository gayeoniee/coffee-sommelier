"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, tastingFor } from "@/lib/api";
import type { Card, Profile } from "@/lib/types";
import Stars from "./Stars";

export default function LogSheet({ card, onClose, onSaved }: { card: Card; onClose: () => void; onSaved: (p: Profile) => void }) {
  const [rating, setRating] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Focus the first star on open so keyboard/screen-reader users land inside the sheet, not on
  // whatever was focused behind it.
  useEffect(() => {
    sheetRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.tasting(tastingFor(card, rating, note));
      setSummary(r.summary);
      onSaved(r.profile);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "저장하지 못했어요");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-10 flex items-end bg-espresso/40" role="dialog" aria-modal="true" aria-label="마신 기록">
      <div ref={sheetRef} className="mx-auto w-full max-w-md rounded-t-3xl bg-cream p-5 pb-8">
        <h2 className="text-lg font-bold">{card.name}</h2>
        {summary ? (
          <>
            <p className="mt-3 text-sm">기록했어요. 취향이 이렇게 바뀌었어요:</p>
            <p className="mt-2 rounded-xl bg-white/80 p-3 text-sm font-medium">{summary}</p>
            <button onClick={onClose} className="mt-4 min-h-11 w-full rounded-full bg-espresso text-cream">닫기</button>
          </>
        ) : (
          <>
            <p className="mt-3 text-sm text-roast">어땠어요?</p>
            <Stars value={rating} onChange={setRating} />
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} rows={2}
              placeholder="한 줄 후기 (선택) — 예: 산미가 너무 셌어요"
              className="mt-3 w-full rounded-xl bg-white p-3 text-base ring-1 ring-crema focus:outline-none focus:ring-roast" />
            {error && <p className="mt-2 text-sm text-warn">{error}</p>}
            <div className="mt-4 flex gap-2">
              <button onClick={onClose} className="min-h-11 flex-1 rounded-full bg-white ring-1 ring-crema">취소</button>
              <button onClick={save} disabled={rating === 0 || busy}
                className="min-h-11 flex-1 rounded-full bg-accent font-semibold text-white disabled:opacity-40">저장</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
