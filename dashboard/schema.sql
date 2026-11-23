-- Run once in the Supabase SQL editor.
-- One row per ranked match at the 15:00 mark, live-available stats only (training split, no held-out test matches).

create table if not exists public.matches (
    match_id          text primary key,
    rank_tier         text        not null,
    patch             text        not null,
    start_time        timestamptz not null,
    game_duration_s   integer     not null,
    blue_win          smallint    not null check (blue_win in (0, 1)),
    blue_first_blood  smallint    not null check (blue_first_blood in (0, 1)),
    level_sum_diff    integer     not null,
    cs_diff           integer     not null,
    kills_diff        integer     not null,
    assists_diff      integer     not null,
    towers_diff       integer     not null,
    dragons_diff      integer     not null,
    voidgrubs_diff    integer     not null
);

-- The deployed dashboard uses the public "anon" key, so lock the table down to read-only.
-- (The upload script uses the service-role key, which bypasses these rules.)
alter table public.matches enable row level security;

drop policy if exists "anyone can read matches" on public.matches;
create policy "anyone can read matches"
    on public.matches for select
    to anon
    using (true);
