create table if not exists doctors (
    id integer primary key,
    name text not null,
    telegram_id text unique,
    specialization text,
    experience_years real,
    preferences json,
    created_at text default (datetime('now'))
);

create table if not exists patients (
    id integer primary key,
    name text not null,
    card_number text,
    created_at text default (datetime('now'))
);

create table if not exists sessions (
    id integer primary key,
    doctor_id integer not null references doctors(id),
    patient_id integer not null references patients(id),
    status text default 'draft',
    transcript text,
    codes text,
    created_at text default (datetime('now')),
    updated_at text default (datetime('now'))
);

create table if not exists treatment_plans (
    id integer primary key,
    session_id integer not null references sessions(id),
    plan_json json not null,
    pdf_path text,
    status text default 'draft',
    created_at text default (datetime('now'))
);

create table if not exists doctor_profiles (
    id integer primary key,
    doctor_id integer not null references doctors(id) on delete cascade,
    profile_name text not null,
    llm_prompt text,
    pricing_bias json,
    protocol_overrides json,
    created_at text default (datetime('now')),
    updated_at text default (datetime('now'))
);

create table if not exists plan_feedback (
    id integer primary key,
    plan_id integer not null references treatment_plans(id) on delete cascade,
    doctor_id integer not null references doctors(id) on delete cascade,
    rating integer,
    accepted integer default 0,
    comments text,
    diff_json json,
    created_at text default (datetime('now'))
);
