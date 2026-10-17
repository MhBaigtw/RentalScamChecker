# Photo duplicate detection: fixture evaluation

- Date: 2026-10-17T01:30:55+00:00
- Machine: AMD64 Family 23 Model 104 Stepping 1, AuthenticAMD, 12 logical CPUs, Windows 11, Python 3.12.6
- Fixtures: 24 base images (CC0/public domain, eval/fixtures/photos/manifest.json, 4.35 MB) x 12 edits = 288 edited images
- Pairs: 7188 = 288 duplicates + 6900 non-duplicates, of which 525 hard negatives (different photos from the same similarity group)
- Synthetic edits on public images: these numbers measure robustness to the listed edits, not performance on real re-posted listings.

## Latency per image (this machine, single image, after model load)

| step | median ms | p95 ms | max ms |
|---|---|---|---|
| pHash + dHash | 6.3 | 11.9 | 950.3 |
| embedding, clip-vit-b32-int8 | 100.5 | 125.0 | 145.7 |
| embedding, clip-vit-b32-fp32 | 95.0 | 209.0 | 632.8 |
- Model load, clip-vit-b32-int8 (89 MB file): 618 ms (first call includes it)
- Model load, clip-vit-b32-fp32 (352 MB file): 1875 ms (first call includes it)

## pHash (64 bits): predict duplicate when distance <= t

Closest non-duplicate: 20 bits (hard negatives: 22). Farthest duplicate: 40.

| t | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| 0 | 78 | 0 | 210 | 1.000 | 0.271 |
| 2 | 89 | 0 | 199 | 1.000 | 0.309 |
| 4 | 97 | 0 | 191 | 1.000 | 0.337 |
| 6 | 104 | 0 | 184 | 1.000 | 0.361 |
| 8 | 111 | 0 | 177 | 1.000 | 0.385 |
| 10 | 123 | 0 | 165 | 1.000 | 0.427 |
| 12 | 143 | 0 | 145 | 1.000 | 0.497 |
| 14 | 158 | 0 | 130 | 1.000 | 0.549 |
| 16 | 168 | 0 | 120 | 1.000 | 0.583 |
| 18 | 186 | 0 | 102 | 1.000 | 0.646 |
| 20 | 199 | 20 | 89 | 0.909 | 0.691 |
| 22 | 209 | 124 | 79 | 0.628 | 0.726 |
| 24 | 214 | 374 | 74 | 0.364 | 0.743 |

## dHash (64 bits): predict duplicate when distance <= t

Closest non-duplicate: 15 bits (hard negatives: 15). Farthest duplicate: 40.

| t | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| 0 | 77 | 0 | 211 | 1.000 | 0.267 |
| 2 | 96 | 0 | 192 | 1.000 | 0.333 |
| 4 | 111 | 0 | 177 | 1.000 | 0.385 |
| 6 | 125 | 0 | 163 | 1.000 | 0.434 |
| 8 | 130 | 0 | 158 | 1.000 | 0.451 |
| 10 | 142 | 0 | 146 | 1.000 | 0.493 |
| 12 | 155 | 0 | 133 | 1.000 | 0.538 |
| 14 | 170 | 0 | 118 | 1.000 | 0.590 |
| 16 | 188 | 6 | 100 | 0.969 | 0.653 |
| 18 | 200 | 9 | 88 | 0.957 | 0.694 |
| 20 | 210 | 39 | 78 | 0.843 | 0.729 |
| 22 | 220 | 136 | 68 | 0.618 | 0.764 |
| 24 | 226 | 401 | 62 | 0.360 | 0.785 |

## Embedding clip-vit-b32-int8: predict duplicate when cosine similarity >= s

Most similar non-duplicate: 0.9281 (hard negatives: 0.9281). Least similar duplicate: 0.7272.

| s | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| 0.80 | 284 | 530 | 4 | 0.349 | 0.986 |
| 0.84 | 278 | 220 | 10 | 0.558 | 0.965 |
| 0.86 | 271 | 111 | 17 | 0.709 | 0.941 |
| 0.88 | 249 | 34 | 39 | 0.880 | 0.865 |
| 0.90 | 221 | 8 | 67 | 0.965 | 0.767 |
| 0.92 | 199 | 1 | 89 | 0.995 | 0.691 |
| 0.94 | 157 | 0 | 131 | 1.000 | 0.545 |
| 0.96 | 117 | 0 | 171 | 1.000 | 0.406 |
| 0.98 | 54 | 0 | 234 | 1.000 | 0.188 |

## Embedding clip-vit-b32-fp32: predict duplicate when cosine similarity >= s

Most similar non-duplicate: 0.9208 (hard negatives: 0.9208). Least similar duplicate: 0.7432.

| s | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| 0.80 | 286 | 542 | 2 | 0.345 | 0.993 |
| 0.84 | 279 | 217 | 9 | 0.562 | 0.969 |
| 0.86 | 266 | 119 | 22 | 0.691 | 0.924 |
| 0.88 | 250 | 46 | 38 | 0.845 | 0.868 |
| 0.90 | 224 | 12 | 64 | 0.949 | 0.778 |
| 0.92 | 193 | 2 | 95 | 0.990 | 0.670 |
| 0.94 | 157 | 0 | 131 | 1.000 | 0.545 |
| 0.96 | 116 | 0 | 172 | 1.000 | 0.403 |
| 0.98 | 65 | 0 | 223 | 1.000 | 0.226 |

## Combined rule (clip-vit-b32-int8): single hash <= a, OR both hashes <= b, OR cosine >= c

Chosen: a = 10, b = 18, c = 0.95. Curve points around it (one parameter varied at a time):

| a | b | c | TP | FP | FP (hard) | FN | precision | recall |
|---|---|---|---|---|---|---|---|---|
| 6 | 18 | 0.95 | 221 | 0 | 0 | 67 | 1.000 | 0.767 |
| 8 | 18 | 0.95 | 222 | 0 | 0 | 66 | 1.000 | 0.771 |
| 10 | 18 | 0.95 **(chosen)** | 223 | 0 | 0 | 65 | 1.000 | 0.774 |
| 12 | 18 | 0.95 | 227 | 0 | 0 | 61 | 1.000 | 0.788 |
| 14 | 18 | 0.95 | 230 | 0 | 0 | 58 | 1.000 | 0.799 |
| 10 | 14 | 0.95 | 207 | 0 | 0 | 81 | 1.000 | 0.719 |
| 10 | 16 | 0.95 | 211 | 0 | 0 | 77 | 1.000 | 0.733 |
| 10 | 18 | 0.95 **(chosen)** | 223 | 0 | 0 | 65 | 1.000 | 0.774 |
| 10 | 20 | 0.95 | 230 | 0 | 0 | 58 | 1.000 | 0.799 |
| 10 | 22 | 0.95 | 240 | 2 | 0 | 48 | 0.992 | 0.833 |
| 10 | 18 | 0.92 | 249 | 1 | 1 | 39 | 0.996 | 0.865 |
| 10 | 18 | 0.93 | 240 | 0 | 0 | 48 | 1.000 | 0.833 |
| 10 | 18 | 0.94 | 233 | 0 | 0 | 55 | 1.000 | 0.809 |
| 10 | 18 | 0.95 **(chosen)** | 223 | 0 | 0 | 65 | 1.000 | 0.774 |
| 10 | 18 | 0.96 | 213 | 0 | 0 | 75 | 1.000 | 0.740 |
| 10 | 18 | 0.97 | 204 | 0 | 0 | 84 | 1.000 | 0.708 |

Recall by edit at the chosen thresholds (24 duplicates each):

| edit | recall | matched by hash | matched by embedding only |
|---|---|---|---|
| resize_50pct | 1.00 | 24 | 0 |
| jpeg_q30 | 1.00 | 24 | 0 |
| crop_center_80pct | 0.58 | 8 | 6 |
| crop_corner_70pct | 0.17 | 0 | 4 |
| crop_tight_55pct | 0.04 | 0 | 1 |
| colour_shift | 1.00 | 24 | 0 |
| greyscale | 1.00 | 24 | 0 |
| rotate_4deg | 0.92 | 22 | 0 |
| mirror | 1.00 | 0 | 24 |
| watermark_banner | 1.00 | 24 | 0 |
| screenshot_border | 0.83 | 19 | 1 |
| crop_resize_jpeg | 0.75 | 14 | 4 |

Known gap: crops that remove 30% or more of the picture are mostly missed; their hashes change completely and their embeddings fall into the range of different-but-similar rooms.
