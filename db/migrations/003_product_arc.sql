-- Product arc on enrollment: which product(s) from contracts/products.yaml the sequence pitches.
-- Shape: {"all": "market_data"} or {"steps_1_3": "market_data", "steps_4_5": "compensation_planning"}.
set search_path = app, public;

alter table enrollment add column if not exists product_arc jsonb;
