-- 在 Supabase SQL Editor 里运行此文件建表
-- Dashboard -> SQL Editor -> New query -> 粘贴 -> Run

-- 商品表
create table if not exists products (
    product_id text primary key,
    name       text default '',
    url        text not null,
    price      numeric(10,2) default 0,
    original_price numeric(10,2) default 0,
    sold       integer default 0,
    shop_name  text default '',
    shop_score text default '',
    shop_fans  text default '',
    shop_total_sold text default '',
    last_checked timestamptz,
    created_at   timestamptz default now()
);

-- 价格/销量历史表
create table if not exists price_history (
    id         bigserial primary key,
    product_id text not null references products(product_id) on delete cascade,
    price      numeric(10,2) default 0,
    sold       integer default 0,
    recorded_at  timestamptz default now(),
    recorded_date date generated always as (recorded_at::date) stored
);
create index if not exists idx_ph_product on price_history(product_id, recorded_at desc);

-- 采集队列表
create table if not exists scrape_queue (
    id           bigserial primary key,
    url          text not null,
    status       text default 'pending',
    error        text,
    created_at   timestamptz default now(),
    processed_at timestamptz
);

-- 开放 RLS（演示用，不做鉴权）
alter table products    enable row level security;
alter table price_history enable row level security;
alter table scrape_queue enable row level security;

create policy if not exists "anon_all_products"  on products    for all to anon using (true) with check (true);
create policy if not exists "anon_all_history"   on price_history for all to anon using (true) with check (true);
create policy if not exists "anon_all_queue"     on scrape_queue for all to anon using (true) with check (true);
