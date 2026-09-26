import type { Card, CaffeineRule } from "./types";

export const RULE_KO: Record<CaffeineRule, string> = { decaf_only: "디카페인만", low: "저카페인", any: "상관없음" };
export const ATTR_KO = { acidity: "산미", body: "바디", sweetness: "단맛" } as const;
export const CONFIDENCE_KO = { high: "높음", medium: "보통", low: "낮음" } as const;

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
