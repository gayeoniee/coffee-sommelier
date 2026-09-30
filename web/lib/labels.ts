import type { Card, CaffeineRule } from "./types";

export const RULE_KO: Record<CaffeineRule, string> = { decaf_only: "디카페인만", low: "저카페인", any: "상관없음" };
export const ATTR_KO = { acidity: "산미", body: "바디", sweetness: "단맛" } as const;
export const CONFIDENCE_KO = { high: "높음", medium: "보통", low: "낮음" } as const;

// "오늘 마신 카페인" 하루 한도 선택지: 임신 중 300mg, 일반 성인 400mg 권장, 끄면 하루 총량으로 추천을 거르지 않는다.
export const CAFFEINE_LIMIT_OPTIONS: { value: number | null; ko: string }[] = [
  { value: null, ko: "끄기" },
  { value: 300, ko: "300mg" },
  { value: 400, ko: "400mg" },
];

export function caffeineLimitLabel(limitMg: number | null): string {
  return limitMg == null ? "끄기" : (CAFFEINE_LIMIT_OPTIONS.find((o) => o.value === limitMg)?.ko ?? `${limitMg}mg`);
}

// The backend formats with Python's f"{x:.1f}"; JS's toFixed(1) can round the same float
// differently at the boundary (e.g. 3.25 → "3.2" vs "3.3"). Round explicitly first so every
// on-screen number uses one consistent rule (still fine if it edge-differs from the backend).
export function fmt1(x: number): string {
  return (Math.round(x * 10) / 10).toFixed(1);
}

export function sourceLabel(card: Card): string {
  if (card.source === "db") return "원두 DB 실측";
  if (card.source === "brand_bean") return "브랜드 원두 기준(추정)";
  return `유사 원두 ${card.n_neighbors ?? 0}개 기반 예측 · 신뢰도 ${CONFIDENCE_KO[card.confidence]}`;
}

/** An order-decaf card never shows the regular drink's caffeine: the API sends the decaf estimate plus a note. */
export function caffeineLabel(card: Card): string | null {
  if (card.caffeine_mg_note) {
    return card.caffeine_mg != null ? `카페인 ~${card.caffeine_mg}mg (${card.caffeine_mg_note})` : card.caffeine_mg_note;
  }
  if (card.order_decaf) return "디카페인 주문 시 카페인 ↓";
  return card.caffeine_mg != null ? `카페인 ${card.caffeine_mg}mg` : null;
}
