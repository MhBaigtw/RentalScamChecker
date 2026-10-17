# Hash search benchmark: Postgres scan vs in-process BK-tree

Rule: (pHash <= 18 AND dHash <= 18) OR pHash <= 10 OR dHash <= 10. Random 64-bit hashes, 30 queries per size, median time per query. Measured on the dev machine (Postgres 16 in Docker Desktop, Windows 11).

| stored hashes | Postgres scan (ms) | BK-tree query (ms) | BK-tree build (s) | BK-tree nodes visited (median) |
|---|---|---|---|---|
| 10,000 | 7.2 | 23.7 | 0.1 | 9,968 (100%) |
| 100,000 | 46.9 | 256.7 | 1.3 | 99,503 (100%) |

With a radius of 18 out of 64 bits, the BK-tree's triangle-inequality pruning removes little,
so it visits most nodes while paying Python per-node overhead, and it must be rebuilt in
every worker process. Decision in ED-025.
