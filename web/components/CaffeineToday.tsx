import type { Today } from "@/lib/types";

/** "오늘 카페인" chip: today's running total, and — when a daily limit is set — a bar and remaining mg. */
export default function CaffeineToday({ today }: { today: Today | null }) {
  if (!today || today.today_mg == null) return null;
  const { today_mg, unknown_count, limit_mg, remaining_mg } = today;
  if (today_mg === 0 && unknown_count === 0 && limit_mg == null) return null;
  const over = remaining_mg != null && remaining_mg < 0;
  const pct = limit_mg ? Math.min(100, Math.round((today_mg / limit_mg) * 100)) : 0;
  return (
    <div className="mb-4 rounded-xl bg-white/70 p-3 text-sm ring-1 ring-crema" aria-live="polite">
      <div className="flex items-center justify-between">
        <span className="font-medium">오늘 카페인</span>
        <span className="tabular-nums">{Math.round(today_mg)}mg{limit_mg != null && ` / ${limit_mg}mg`}</span>
      </div>
      {limit_mg != null && (
        <>
          <div className="mt-2 h-2 rounded-full bg-crema">
            <div className={`h-2 rounded-full ${over ? "bg-warn" : "bg-roast"}`} style={{ width: `${pct}%` }} />
          </div>
          <p className={`mt-1 text-xs ${over ? "font-medium text-warn" : "text-roast"}`}>
            {over ? "오늘 한도를 넘었어요" : `남음 ${Math.round(remaining_mg ?? 0)}mg`}
          </p>
        </>
      )}
      {unknown_count > 0 && (
        <p className="mt-1 text-[11px] text-roast/80">카페인 정보 없는 기록 {unknown_count}건은 빼고 계산했어요</p>
      )}
    </div>
  );
}
