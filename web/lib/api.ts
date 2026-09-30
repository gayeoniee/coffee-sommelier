import type { Brand, CaffeineRule, Card, CoffeeHit, Me, Profile, Sample, Today } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function errorFrom(r: Response): Promise<ApiError> {
  let msg = r.status === 502 ? "서버에 연결할 수 없어요" : `요청에 실패했어요 (${r.status})`;
  try {
    const j = await r.json();
    if (typeof j?.detail === "string") msg = j.detail;
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(r.status, msg);
}

// A guest session cookie can be missing or expired (private browsing, cleared cookies, a stale
// tab). Rather than surface a raw 401, make a fresh /session once and retry the request once.
async function recoverSession(): Promise<void> {
  const r = await fetch("/api/session", { method: "POST", headers: { "content-type": "application/json" } });
  if (!r.ok) throw await errorFrom(r);
}

async function req<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (r.status === 401 && !retried) {
    await recoverSession();
    return req<T>(path, init, true);
  }
  if (!r.ok) throw await errorFrom(r);
  return (await r.json()) as T;
}

export type ProfileInput = {
  caffeine_rule: CaffeineRule;
  milk_ok: boolean;
  acidity: number;
  body: number;
  sweetness: number;
  flavor_likes: string[];
  nickname?: string | null;
  daily_caffeine_limit_mg?: number | null;
};

export type TastingInput = {
  rating: number;
  note: string | null;
  coffee_id?: number;
  menu_item_id?: number;
  order_decaf?: boolean;
  input_text?: string;
  predicted?: { acidity: number | null; body: number | null; sweetness: number | null; tags: string[]; is_decaf: boolean };
};

export const api = {
  session: () => req<{ user_id: string; new: boolean; has_profile: boolean }>("/session", { method: "POST" }),
  me: () => req<Me>("/me"),
  today: () => req<Today>("/me/today"),
  putProfile: (body: ProfileInput) => req<{ profile: Profile }>("/me/profile", { method: "PUT", body: JSON.stringify(body) }),
  nickname: (nickname: string) =>
    req<{ nickname: string | null }>("/me/nickname", { method: "PUT", body: JSON.stringify({ nickname }) }),
  samples: () => req<Sample[]>("/onboarding/samples"),
  postSamples: (body: { coffee_id: number; liked: boolean }[]) =>
    req<{ profile: Profile }>("/onboarding/samples", { method: "POST", body: JSON.stringify(body) }),
  brands: () => req<Brand[]>("/brands"),
  search: (q: string) => req<CoffeeHit[]>(`/coffees/search?q=${encodeURIComponent(q)}`),
  tasting: (body: TastingInput) =>
    req<{ summary: string; changes: string[]; profile: Profile }>("/tastings", { method: "POST", body: JSON.stringify(body) }),
};

export async function openStream(
  path: "/recommend" | "/analyze",
  body: unknown,
  signal?: AbortSignal,
  retried = false,
): Promise<ReadableStream<Uint8Array>> {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    signal,
  });
  if (r.status === 401 && !retried) {
    await recoverSession();
    return openStream(path, body, signal, true);
  }
  if (!r.ok || !r.body) throw await errorFrom(r);
  return r.body;
}

export function tastingFor(card: Card, rating: number, note: string): TastingInput {
  const base = { rating, note: note.trim().slice(0, 200) || null };
  if (card.coffee_id != null) return { ...base, coffee_id: card.coffee_id };
  if (card.menu_item_id != null) return { ...base, menu_item_id: card.menu_item_id, order_decaf: card.order_decaf };
  const inputText = card.source === "predicted" ? card.name : `${card.brand ?? ""} ${card.name}`.trim();
  return {
    ...base,
    input_text: inputText.slice(0, 300),
    predicted: {
      acidity: card.acidity,
      body: card.body,
      sweetness: card.sweetness,
      tags: card.tags.slice(0, 10),
      is_decaf: card.is_decaf || card.order_decaf,
    },
  };
}
