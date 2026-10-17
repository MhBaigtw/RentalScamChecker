"""Benchmark: find stored photos matching a query's hashes under the rule in photos/matching.py,
(1) in Postgres with bit_count(XOR) over all rows, (2) in Python with BK-trees.

Run from apps/api: .venv/Scripts/python.exe ../../eval/hash_search_bench.py
Uses a TEMP table in the configured database (dropped when the session ends). Hashes are random
64-bit values: the worst case for a BK-tree (no clustering to prune) and a fair one for a scan.
Writes eval/reports/hash_search_bench.md.
"""

import random
import statistics
import time
from collections.abc import Iterator
from pathlib import Path

import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
REPORT = HERE / "reports" / "hash_search_bench.md"
SIZES = (10_000, 100_000)
QUERIES = 30

SQL = sa.text(
    """
    SELECT id FROM bench_hashes
    WHERE (bit_count((phash # :p)::bit(64)) <= 18 AND bit_count((dhash # :d)::bit(64)) <= 18)
       OR bit_count((phash # :p)::bit(64)) <= 10
       OR bit_count((dhash # :d)::bit(64)) <= 10
    """
)


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & 0xFFFF_FFFF_FFFF_FFFF).bit_count()


class Node:
    __slots__ = ("children", "ident", "value")

    def __init__(self, value: int, ident: int) -> None:
        self.value = value
        self.ident = ident
        self.children: dict[int, Node] = {}


class BKTree:
    """Metric tree over Hamming distance: each child edge is labelled with its distance to the
    parent, and the triangle inequality prunes subtrees that can't contain a match."""

    def __init__(self) -> None:
        self.root: Node | None = None

    def add(self, value: int, ident: int) -> None:
        node = Node(value, ident)
        if self.root is None:
            self.root = node
            return
        current = self.root
        while True:
            d = hamming(value, current.value)
            child = current.children.get(d)
            if child is None:
                current.children[d] = node
                return
            current = child

    def search(self, value: int, radius: int) -> Iterator[int]:
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop()
            d = hamming(value, node.value)
            if d <= radius:
                yield node.ident
            stack += [c for edge, c in node.children.items() if d - radius <= edge <= d + radius]

    def visited(self, value: int, radius: int) -> int:
        """Nodes a search touches (how much the pruning saves)."""
        count = 0
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop()
            count += 1
            d = hamming(value, node.value)
            stack += [c for edge, c in node.children.items() if d - radius <= edge <= d + radius]
        return count


def signed(u: int) -> int:
    return u - (1 << 64) if u >= 1 << 63 else u


def main() -> None:
    from rentalscamchecker.config import get_settings

    rng = random.Random(42)
    engine = sa.create_engine(get_settings().database_url)
    lines = ["# Hash search benchmark: Postgres scan vs in-process BK-tree", ""]
    lines.append(
        "Rule: (pHash <= 18 AND dHash <= 18) OR pHash <= 10 OR dHash <= 10. Random 64-bit hashes, "
        f"{QUERIES} queries per size, median time per query. Measured on the dev machine "
        "(Postgres 16 in Docker Desktop, Windows 11)."
    )
    lines += [
        "",
        "| stored hashes | Postgres scan (ms) | BK-tree query (ms) | BK-tree build (s) | BK-tree nodes visited (median) |",
        "|---|---|---|---|---|",
    ]
    with engine.connect() as conn:
        for n in SIZES:
            rows = [(i, signed(rng.getrandbits(64)), signed(rng.getrandbits(64))) for i in range(n)]
            conn.execute(sa.text("DROP TABLE IF EXISTS bench_hashes"))
            conn.execute(
                sa.text("CREATE TEMP TABLE bench_hashes (id int, phash bigint, dhash bigint)")
            )
            conn.execute(
                sa.text("INSERT INTO bench_hashes VALUES (:i, :p, :d)"),
                [{"i": i, "p": p, "d": d} for i, p, d in rows],
            )
            conn.execute(sa.text("ANALYZE bench_hashes"))
            queries = [rows[rng.randrange(n)] for _ in range(QUERIES)]
            sql_ms = []
            for _, p, d in queries:
                t = time.perf_counter()
                found = conn.execute(SQL, {"p": p, "d": d}).scalars().all()
                sql_ms.append((time.perf_counter() - t) * 1000)
                assert len(found) >= 1  # the query's own row

            t = time.perf_counter()
            by_phash, by_dhash = BKTree(), BKTree()
            for i, p, d in rows:
                by_phash.add(p, i)
                by_dhash.add(d, i)
            build_s = time.perf_counter() - t
            dhash_of = {i: d for i, _, d in rows}
            phash_of = {i: p for i, p, _ in rows}
            bk_ms = []
            for _, p, d in queries:
                t = time.perf_counter()
                hits = {
                    i
                    for i in by_phash.search(p, 18)
                    if hamming(dhash_of[i], d) <= 18 or hamming(phash_of[i], p) <= 10
                }
                hits |= set(by_dhash.search(d, 10))
                bk_ms.append((time.perf_counter() - t) * 1000)
            visited = [by_phash.visited(p, 18) for _, p, _ in queries[:5]]
            lines.append(
                f"| {n:,} | {statistics.median(sql_ms):.1f} | {statistics.median(bk_ms):.1f} | "
                f"{build_s:.1f} | {statistics.median(visited):,.0f} ({statistics.median(visited) / n:.0%}) |"
            )
            print(lines[-1])
    lines += [
        "",
        "With a radius of 18 out of 64 bits, the BK-tree's triangle-inequality pruning removes little,",
        "so it visits most nodes while paying Python per-node overhead, and it must be rebuilt in",
        "every worker process. Decision in ED-025.",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
