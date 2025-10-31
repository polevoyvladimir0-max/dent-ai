create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;
create extension if not exists pgvector;
create extension if not exists btree_gin;

create schema if not exists pricing;

create table if not exists pricing.price_catalog (
    id uuid primary key default uuid_generate_v4(),
    slug citext not null unique,
    title text not null,
    description text,
    valid_from timestamptz not null default now(),
    valid_to timestamptz,
    currency_code char(3) not null default 'RUB',
    pricebook_vector vector(1536),
    metadata jsonb not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    archived boolean not null default false,
    constraint chk_validity check (valid_to is null or valid_to >= valid_from)
);

create table if not exists pricing.price_category (
    id uuid primary key default uuid_generate_v4(),
    catalog_id uuid not null references pricing.price_catalog(id) on delete cascade,
    code text not null,
    name text not null,
    parent_id uuid references pricing.price_category(id) on delete cascade,
    ordering int not null default 100,
    metadata jsonb not null default '{}',
    unique (catalog_id, code)
);

create table if not exists pricing.price_item (
    id uuid primary key default uuid_generate_v4(),
    catalog_id uuid not null references pricing.price_catalog(id) on delete cascade,
    category_id uuid references pricing.price_category(id) on delete set null,
    external_code text,
    name text not null,
    clinical_guideline_ref text,
    base_price numeric(12,2) not null check (base_price >= 0),
    unit text not null default 'procedure',
    duration_minutes int check (duration_minutes > 0),
    contraindications text[],
    metadata jsonb not null default '{}',
    search_vector tsvector generated always as (
        to_tsvector('russian', coalesce(name,'') || ' ' || coalesce(clinical_guideline_ref,'') || ' ' || array_to_string(contraindications, ' '))
    ) stored,
    embeddings vector(1536),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    archived boolean not null default false,
    unique (catalog_id, coalesce(external_code, name))
);

create table if not exists pricing.price_modifier (
    id uuid primary key default uuid_generate_v4(),
    item_id uuid not null references pricing.price_item(id) on delete cascade,
    modifier_type text not null check (modifier_type in ('discount', 'surcharge')),
    label text not null,
    value numeric(8,4) not null,
    is_percentage boolean not null default true,
    valid_from timestamptz not null default now(),
    valid_to timestamptz,
    condition jsonb not null default '{}'
);

create table if not exists pricing.price_audit (
    id bigserial primary key,
    item_id uuid not null references pricing.price_item(id) on delete cascade,
    change_type text not null check (change_type in ('create','update','archive')),
    changed_by text not null,
    payload jsonb not null,
    changed_at timestamptz not null default now()
);

create or replace function pricing.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create trigger trg_price_catalog_updated
before update on pricing.price_catalog
for each row execute function pricing.touch_updated_at();

create trigger trg_price_item_updated
before update on pricing.price_item
for each row execute function pricing.touch_updated_at();

create index if not exists idx_price_item_catalog on pricing.price_item (catalog_id);
create index if not exists idx_price_item_category on pricing.price_item (category_id);
create index if not exists idx_price_item_search on pricing.price_item using gin (search_vector);
create index if not exists idx_price_item_embeddings on pricing.price_item using ivfflat (embeddings vector_cosine_ops) with (lists = 200);
create index if not exists idx_price_modifier_validity on pricing.price_modifier (item_id, valid_from, coalesce(valid_to, 'infinity'));
