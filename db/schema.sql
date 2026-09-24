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
