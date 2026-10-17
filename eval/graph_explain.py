"""EXPLAIN ANALYZE for the check-graph queries (graph.py, signals/contact_reuse.py) at 50,000 checks.

Run from apps/api: .venv/Scripts/python.exe ../../eval/graph_explain.py
Creates (or refills) a separate database, "<dev database>_graphbench", migrated to head; the dev
and test databases are not touched. Writes eval/reports/graph_explain.md.

The data is SYNTHETIC (no real listings or contacts) and shaped like what the graph is for:
- one-off landlords: their own phone (85%) / email (40%), sometimes only in the listing text;
- reposts: the same listing submitted again (same address, contacts and photos);
- property managers: 5-30 listings at different addresses in one city sharing a phone and email,
  30% of them also sharing a stock photo;
- rings: 4-25 checks across 2-6 cities sharing a phone and the same photos;
- hotlines: 3 contacts used by 300 checks each (the worst case for "who else used this contact").
Photos have random hashes and no embeddings: the graph queries read photo_links, not the photos.
Contact hashes are random bytes (no real contact is hashed).
"""

import io
import random
import re
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from rentalscamchecker.config import get_settings
from rentalscamchecker.graph import (
    _CHECKS_LINKED_SQL,
    _CONTACTS_OF_SQL,
    _RELATED_SQL,
    cluster_size,
)
from rentalscamchecker.signals.contact_reuse import _LINKED_SQL

HERE = Path(__file__).resolve().parent
API_DIR = HERE.parent / "apps" / "api"
REPORT = HERE / "reports" / "graph_explain.md"
TOTAL_CHECKS = 50_000
PHOTOS_PER_CHECK = 3
RUNS = 20
_SAFE_NAME = re.compile(r"^[a-z0-9_]+_graphbench$")

CITIES = {
    "Toronto": 30,
    "Ottawa": 10,
    "Mississauga": 10,
    "Brampton": 8,
    "Hamilton": 6,
    "Waterloo": 6,
    "London": 6,
    "Kitchener": 5,
    "Markham": 4,
    "Kingston": 4,
    "Oakville": 3,
    "Vaughan": 3,
    "Guelph": 3,
    "Milton": 2,
}
STREETS = [
    f"{name} {kind}"
    for name in ("King", "Queen", "Main", "Bay", "Elm", "Oak", "Maple", "Church", "Park", "Lake")
    for kind in ("St", "Ave", "Rd")
]
RISK = {"low": 60, "some": 25, "high": 10, "incomplete": 5}


@dataclass
class Seed:
    city: str
    address: str
    # (kind, source, identity): checks with the same identity share the contact hash.
    contacts: list[tuple[str, str, str]] = field(default_factory=list)
    # Photo slots shared with other checks: equal labels are linked (e.g. "ring7:0").
    shared_photos: list[str] = field(default_factory=list)
    group: str = "individual"


def pick(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def address(rng: random.Random) -> str:
    return f"{rng.randint(1, 3000)} {rng.choice(STREETS)}"


def build_seeds(rng: random.Random) -> list[Seed]:
    seeds: list[Seed] = []
    for m in range(400):
        city, stock = pick(rng, CITIES), rng.random() < 0.3
        for _ in range(rng.randint(5, 30)):
            seeds.append(
                Seed(
                    city,
                    address(rng),
                    [("phone", "field", f"pm{m}:p"), ("email", "field", f"pm{m}:e")],
                    [f"pm{m}:stock"] if stock else [],
                    "manager",
                )
            )
    for r in range(60):
        cities = rng.sample(list(CITIES), rng.randint(2, 6))
        source = rng.choice(["field", "text"])
        for _ in range(rng.randint(4, 25)):
            seeds.append(
                Seed(
                    rng.choice(cities),
                    address(rng),
                    [("phone", source, f"ring{r}:p")],
                    [f"ring{r}:{i}" for i in range(PHOTOS_PER_CHECK)],
                    "ring",
                )
            )
    for h in range(3):
        city = pick(rng, CITIES)
        seeds.extend(
            Seed(city, address(rng), [("phone", "field", f"hotline{h}")], [], "hotline")
            for _ in range(300)
        )
    individuals: list[Seed] = []
    n = 0
    while len(seeds) + len(individuals) < TOTAL_CHECKS:
        if individuals and rng.random() < 0.09:  # a repost of an earlier listing
            original = rng.choice(individuals)
            label = f"repost{n}"
            if not original.shared_photos:
                original.shared_photos = [f"{label}:{i}" for i in range(PHOTOS_PER_CHECK)]
            individuals.append(
                Seed(
                    original.city,
                    original.address,
                    list(original.contacts),
                    list(original.shared_photos),
                    "repost",
                )
            )
        else:
            contacts = []
            if rng.random() < 0.85:
                contacts.append(("phone", "text" if rng.random() < 0.15 else "field", f"i{n}:p"))
            if rng.random() < 0.40:
                contacts.append(("email", "field", f"i{n}:e"))
            individuals.append(Seed(pick(rng, CITIES), address(rng), contacts))
        n += 1
    seeds.extend(individuals)
    rng.shuffle(seeds)
    return seeds


def bench_url() -> str:
    url = make_url(get_settings().database_url)
    name = f"{url.database}_graphbench"
    if not _SAFE_NAME.match(name):
        raise RuntimeError(f"refusing to use {name!r}")
    return url.set(database=name).render_as_string(hide_password=False)


def recreate(url: str) -> None:
    name = make_url(url).database
    admin = sa.create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))  # name checked in bench_url
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def copy_rows(
    conn: Connection, table: str, columns: list[str], rows: list[tuple[Any, ...]]
) -> None:
    raw = conn.connection.driver_connection
    assert raw is not None
    with raw.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def to_signed64(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def load(conn: Connection, seeds: list[Seed], rng: random.Random) -> dict[str, list[uuid.UUID]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    checks, photos, contacts, links = [], [], [], []
    by_group: dict[str, list[uuid.UUID]] = {}
    photos_by_label: dict[str, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    contact_hash: dict[str, bytes] = {}
    for i, seed in enumerate(seeds):
        check_id = uuid.uuid4()
        by_group.setdefault(seed.group, []).append(check_id)
        created = start + timedelta(minutes=7 * i)
        checks.append(
            (
                check_id,
                created,
                "complete",
                pick(rng, RISK),
                "x",
                180000,
                2,
                seed.address,
                seed.city,
            )
        )
        for position in range(PHOTOS_PER_CHECK):
            photo_id = uuid.uuid4()
            photos.append(
                (
                    photo_id,
                    check_id,
                    position,
                    f"checks/{check_id}/{photo_id}.jpg",
                    to_signed64(rng.getrandbits(64)),
                    to_signed64(rng.getrandbits(64)),
                    800,
                    600,
                )
            )
            if position < len(seed.shared_photos):
                photos_by_label.setdefault(seed.shared_photos[position], []).append(
                    (photo_id, check_id)
                )
        seen = set()
        for kind, source, identity in seed.contacts:
            digest = contact_hash.setdefault(identity, rng.randbytes(32))
            if (kind, digest) not in seen:
                seen.add((kind, digest))
                contacts.append((uuid.uuid4(), check_id, kind, source, digest))
    for members in photos_by_label.values():
        for photo_a, check_a in members:
            for photo_b, check_b in members:
                if check_a != check_b:
                    links.append((photo_a, photo_b, check_a, check_b, "hash", 2, 3, 0.98))
    copy_rows(
        conn,
        "checks",
        [
            "id",
            "created_at",
            "status",
            "risk_level",
            "listing_text",
            "rent_cents",
            "bedrooms",
            "address_text",
            "city",
        ],
        checks,
    )
    copy_rows(
        conn,
        "check_photos",
        ["id", "check_id", "position", "storage_key", "phash", "dhash", "width", "height"],
        photos,
    )
    copy_rows(conn, "check_contacts", ["id", "check_id", "kind", "source", "hmac_hash"], contacts)
    copy_rows(
        conn,
        "photo_links",
        [
            "photo_id",
            "other_photo_id",
            "check_id",
            "other_check_id",
            "match_type",
            "phash_distance",
            "dhash_distance",
            "cosine",
        ],
        links,
    )
    return by_group


def explain(conn: Connection, sql: sa.TextClause, params: dict[str, Any]) -> str:
    rows = conn.execute(sa.text(f"EXPLAIN (ANALYZE, BUFFERS) {sql.text}"), params).scalars()
    return "\n".join(rows)


def median_ms(fn: Callable[[], object]) -> float:
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def query_ms(conn: Connection, sql: sa.TextClause, params: dict[str, Any]) -> float:
    return median_ms(lambda: conn.execute(sql, params).all())


def bfs_ms(session: Session, check_id: uuid.UUID) -> float:
    return median_ms(lambda: cluster_size(session, check_id))


def main() -> None:
    rng = random.Random(6)
    url = bench_url()
    t0 = time.perf_counter()
    recreate(url)
    engine = sa.create_engine(url)
    seeds = build_seeds(rng)
    with engine.begin() as conn:
        groups = load(conn, seeds, rng)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text("VACUUM ANALYZE"))
    seconds = time.perf_counter() - t0

    out = io.StringIO()
    with engine.connect() as conn:
        counts = {
            t: conn.scalar(sa.text(f"SELECT count(*) FROM {t}"))  # noqa: S608 (constant names)
            for t in ("checks", "check_photos", "check_contacts", "photo_links")
        }
        size = conn.scalar(sa.text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
        version = conn.scalar(sa.text("SHOW server_version"))

        def check_with_most_links(group: str) -> uuid.UUID:
            ids = [str(c) for c in groups[group]]
            return (
                conn.scalar(
                    sa.text(
                        "SELECT check_id FROM photo_links WHERE check_id = ANY(CAST(:ids AS uuid[])) "
                        "GROUP BY check_id ORDER BY count(*) DESC LIMIT 1"
                    ),
                    {"ids": ids},
                )
                or groups[group][0]
            )

        samples = {
            "one-off landlord": groups["individual"][0],
            "ring (largest)": check_with_most_links("ring"),
            "hotline (300 checks share the phone)": groups["hotline"][0],
            "property manager": groups["manager"][0],
        }
        print(f"# Check graph queries at {counts['checks']:,} checks: EXPLAIN ANALYZE\n", file=out)
        print(
            "Generated by `eval/graph_explain.py` (synthetic data, see its docstring) on "
            f"Postgres {version} in Docker on Humayun's Windows laptop, warm cache. Seeding and "
            f"VACUUM ANALYZE took {seconds:.0f} s.\n",
            file=out,
        )
        print("| table | rows |\n|---|---|", file=out)
        for t, n in counts.items():
            print(f"| {t} | {n:,} |", file=out)
        print(f"\nDatabase size: {size}.\n", file=out)

        print(
            f"## Timings (median of {RUNS} runs, from Python, including the round trip)\n", file=out
        )
        print(
            "| sample check | contact_reuse lookup | related checks | cluster size (BFS) |\n"
            "|---|---|---|---|",
            file=out,
        )
        with Session(bind=conn) as session:
            for label, cid in samples.items():
                p = {"check_id": cid, "limit": 50}
                linked = query_ms(conn, _LINKED_SQL, p)
                related = query_ms(conn, _RELATED_SQL, p)
                cluster = cluster_size(session, cid)
                bfs = bfs_ms(session, cid)
                n_linked = len(conn.execute(_LINKED_SQL, p).all())
                print(
                    f"| {label} | {linked:.2f} ms ({n_linked} rows) | {related:.2f} ms | "
                    f"{bfs:.2f} ms (size {cluster.size}{'+' if cluster.capped else ''}) |",
                    file=out,
                )

        print(
            "\nThe hotline's cluster search sends 299 ids as a parameter at level 2 (~11 kB). "
            "Round trip of `SELECT length(:p)` (no table access) with a text parameter of a "
            "given size, same connection (docs/problems-and-solutions.md P-009):\n\n"
            "| parameter size | median round trip |\n|---|---|",
            file=out,
        )
        length = sa.text("SELECT length(:p)")
        for size in (1_000, 7_400, 8_500, 11_000, 20_000):
            print(f"| {size:,} B | {query_ms(conn, length, {'p': 'x' * size}):.2f} ms |", file=out)

        ring = samples["ring (largest)"]
        hotline = samples["hotline (300 checks share the phone)"]
        sections: list[tuple[str, sa.TextClause, dict[str, Any]]] = [
            ("contact_reuse lookup, ring check", _LINKED_SQL, {"check_id": ring}),
            ("contact_reuse lookup, hotline check", _LINKED_SQL, {"check_id": hotline}),
            ("related checks, ring check", _RELATED_SQL, {"check_id": ring, "limit": 50}),
            ("related checks, hotline check", _RELATED_SQL, {"check_id": hotline, "limit": 50}),
        ]
        # The hotline's second BFS level: its 299 neighbours are the frontier, and the shared phone
        # hash was already expanded at level 1, so no new hashes are passed.
        frontier = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT o.check_id FROM check_contacts m JOIN check_contacts o "
                    "ON o.hmac_hash = m.hmac_hash WHERE m.check_id = :c AND o.check_id <> :c"
                ),
                {"c": hotline},
            )
        ]
        sections.append(
            (
                f"cluster BFS level 2 (hotline): contacts of a {len(frontier)}-check frontier",
                _CONTACTS_OF_SQL,
                {"checks": frontier},
            )
        )
        sections.append(
            (
                f"cluster BFS level 2 (hotline): checks linked to a {len(frontier)}-check "
                "frontier, no new hashes",
                _CHECKS_LINKED_SQL,
                {"hashes": [], "checks": frontier},
            )
        )
        print("\n## Plans with the M6 indexes\n", file=out)
        for title, sql, params in sections:
            print(f"### {title}\n\n```\n{explain(conn, sql, params)}\n```\n", file=out)

        conn.commit()  # end the transaction SQLAlchemy began, so each DROP gets its own
        print(
            "## The same queries without each index (dropped inside a rolled-back transaction)\n",
            file=out,
        )
        drops: list[tuple[str, str, sa.TextClause, dict[str, Any]]] = [
            (
                "ix_check_contacts_hmac_hash_check_id",
                "contact_reuse lookup, ring check",
                _LINKED_SQL,
                {"check_id": ring},
            ),
            (
                "ix_photo_links_check_id_other_check_id",
                "related checks, ring check",
                _RELATED_SQL,
                {"check_id": ring, "limit": 50},
            ),
        ]
        for index, title, sql, params in drops:
            trans = conn.begin()
            try:
                conn.execute(sa.text(f"DROP INDEX {index}"))  # names are the constants above
                plan = explain(conn, sql, params)
                no_index = query_ms(conn, sql, params)
            finally:
                trans.rollback()
            print(
                f"### {title}, without `{index}` (median {no_index:.2f} ms)\n\n```\n{plan}\n```\n",
                file=out,
            )
    REPORT.write_text(out.getvalue(), encoding="utf-8")
    print(out.getvalue())


if __name__ == "__main__":
    main()
