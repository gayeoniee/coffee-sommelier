CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS coffees (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  name text NOT NULL,
  roaster text,
  origin_country text,
  origin_region text,
  process text,
  roast_level text,
  is_decaf boolean NOT NULL DEFAULT false,
  decaf_process text,
  acidity smallint CHECK (acidity BETWEEN 1 AND 5),
  body smallint CHECK (body BETWEEN 1 AND 5),
  sweetness smallint CHECK (sweetness BETWEEN 1 AND 5),
  flavor_tags text[] NOT NULL DEFAULT '{}',
  flavor_summary text,
  embedding vector(1024),
  source text NOT NULL,
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  coffee_id bigint NOT NULL REFERENCES coffees(id) ON DELETE CASCADE,
  text text NOT NULL,
  rating real,
  sub_scores jsonb NOT NULL DEFAULT '{}',
  source text NOT NULL,
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS brands (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  name text NOT NULL,
  decaf_available boolean NOT NULL,
  decaf_surcharge_krw integer,
  default_bean_coffee_id bigint REFERENCES coffees(id),
  decaf_bean_coffee_id bigint REFERENCES coffees(id),
  notes text,
  source_url text,
  verified_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS menu_items (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  brand_id bigint NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  name text NOT NULL,
  name_en text,
  category text,
  is_decaf boolean NOT NULL DEFAULT false,
  decaf_option boolean NOT NULL DEFAULT false,
  caffeine_mg real,
  coffee_id bigint REFERENCES coffees(id),
  source_url text,
  collected_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS flavor_taxonomy (
  id bigserial PRIMARY KEY,
  key text UNIQUE NOT NULL,
  parent_id bigint REFERENCES flavor_taxonomy(id),
  level smallint NOT NULL,
  name_en text NOT NULL,
  name_ko text
);

CREATE TABLE IF NOT EXISTS enrich_log (
  row_ref text NOT NULL,
  stage text NOT NULL,
  status text NOT NULL CHECK (status IN ('ok', 'failed')),
  error text,
  model text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (row_ref, stage)
);

CREATE INDEX IF NOT EXISTS coffees_embedding_idx ON coffees USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS coffees_is_decaf_idx ON coffees (is_decaf);
CREATE INDEX IF NOT EXISTS menu_items_brand_idx ON menu_items (brand_id);

-- ===== 2단계: 사용자 =====
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nickname text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taste_profiles (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  caffeine_rule text NOT NULL CHECK (caffeine_rule IN ('decaf_only', 'low', 'any')),
  milk_ok boolean NOT NULL,
  acidity real NOT NULL,
  body real NOT NULL,
  sweetness real NOT NULL,
  flavor_weights jsonb NOT NULL DEFAULT '{}',
  n_updates integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tastings (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  coffee_id bigint REFERENCES coffees(id),
  menu_item_id bigint REFERENCES menu_items(id),
  input_text text,
  predicted jsonb,
  rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
  note text,
  parsed_signals jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(coffee_id, menu_item_id, input_text) = 1)
);

CREATE TABLE IF NOT EXISTS profile_history (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot jsonb NOT NULL,
  tasting_id bigint REFERENCES tastings(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tastings_user_idx ON tastings (user_id, id DESC);
CREATE INDEX IF NOT EXISTS profile_history_user_idx ON profile_history (user_id, id DESC);

ALTER TABLE brands ADD COLUMN IF NOT EXISTS bean jsonb;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS decaf_bean jsonb;
-- 오픈판(DATA_VARIANT=open) 전용 브랜드 원두 값: 라이선스 제약 없는 출처만 (docs/adr/0012-official-brand-beans.md)
ALTER TABLE brands ADD COLUMN IF NOT EXISTS bean_open jsonb;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS decaf_bean_open jsonb;

-- 소스에서 사라졌지만 사용자 기록이 가리켜 지우지 못한 행: 카탈로그(검색·추천·이웃)에서 숨긴다.
ALTER TABLE coffees ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;

-- 로스터리 공개 게이지 라벨과 특징 모델 (docs/adr/0011-roaster-gauges-feature-model.md):
-- 재배 고도·품종(표기된 값만)과 속성별 라벨 출처 {"acidity": "gauge", "body": "korean_cue", ...}.
ALTER TABLE coffees ADD COLUMN IF NOT EXISTS altitude_m integer;
ALTER TABLE coffees ADD COLUMN IF NOT EXISTS variety text;
ALTER TABLE coffees ADD COLUMN IF NOT EXISTS attr_label_source jsonb NOT NULL DEFAULT '{}';

-- 자동 갱신(docs/adr/0015-automated-refresh.md): 우유 라벨(data/curated/menu_milk_labels.yaml)에 없는 새 메뉴는
-- 적재는 하되 사람이 라벨을 달 때까지 추천에서 뺀다(fail closed). 우유 여부는 그동안에도 키워드 판정(is_milk_drink)을 쓴다.
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false;

-- 식약처 식품영양성분DB(음식 DB) 교차검증/신규 브랜드 메뉴 (docs/adr/0023-mfds-food-db.md): 브랜드 자체 수집기가 만든
-- 메뉴는 NULL, 이 출처로 채운 메뉴는 'mfds_food'.
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS source text;

-- "오늘 마신 카페인" 하루 합계 기능: 사용자가 설정한 하루 한도(끄면 NULL, 300/400 또는 100-600 직접 입력)와,
-- 각 tasting을 기록한 시점에 카드가 보여준 카페인(mg) -- 메뉴 caffeine_mg, 디카페인 주문이면 그 추정치, 모르면 NULL
-- (오늘 합계 집계에서 "카페인 모름"으로 따로 센다). 원두(coffees) 기록은 항상 NULL.
ALTER TABLE taste_profiles ADD COLUMN IF NOT EXISTS daily_caffeine_limit_mg integer
  CHECK (daily_caffeine_limit_mg IS NULL OR daily_caffeine_limit_mg BETWEEN 100 AND 600);
ALTER TABLE tastings ADD COLUMN IF NOT EXISTS caffeine_mg real;
