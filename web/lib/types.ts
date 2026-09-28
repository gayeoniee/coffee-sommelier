export type CaffeineRule = "decaf_only" | "low" | "any";

export type Profile = {
  caffeine_rule: CaffeineRule;
  milk_ok: boolean;
  acidity: number;
  body: number;
  sweetness: number;
  flavor_weights: Record<string, number>;
  n_updates: number;
};

export type Card = {
  key: string;
  name: string;
  brand: string | null;
  score: number;
  source: "db" | "brand_bean" | "predicted";
  confidence: "high" | "medium" | "low";
  acidity: number | null;
  body: number | null;
  sweetness: number | null;
  tags: string[];
  tags_ko: string[];
  is_decaf: boolean;
  order_decaf: boolean;
  decaf_surcharge_krw: number | null;
  caffeine_mg: number | null;
  caffeine_mg_note?: string | null; // set when caffeine_mg is the decaf-order estimate (or null: no estimate)
  is_milk: boolean;
  coffee_id: number | null;
  menu_item_id: number | null;
  violation: string | null;
  template: string;
  evidence?: string[];
  n_neighbors?: number;
};

export type Brand = {
  key: string;
  name: string;
  decaf_available: boolean;
  decaf_surcharge_krw: number | null;
  notes: string | null;
  has_menu: boolean;
};

export type CoffeeHit = { id: number; name: string; roaster: string | null; origin_country: string | null; is_decaf: boolean };

export type Sample = { coffee_id: number; name: string; tags: string[]; tags_ko: string[]; description: string };

export type Me = {
  user_id: string;
  nickname: string | null;
  profile: Profile | null;
  history: { snapshot: Profile; tasting_id: number | null; created_at: string }[];
  tastings: { id: number; rating: number; note: string | null; created_at: string; name: string }[];
};

export const CHIPS: { key: string; ko: string }[] = [
  { key: "fruity", ko: "과일" },
  { key: "floral", ko: "꽃" },
  { key: "sweet", ko: "단맛" },
  { key: "nutty/cocoa", ko: "견과/코코아" },
  { key: "roasted", ko: "로스팅" },
  { key: "spices", ko: "향신료" },
  { key: "sour/fermented", ko: "신맛/발효" },
];
