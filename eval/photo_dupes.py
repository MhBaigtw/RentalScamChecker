"""Evaluate photo-reuse detection: perceptual hashes and CLIP embeddings on a fixture set.

Run from apps/api (needs the embedding model files: python -m rentalscamchecker.photos.model_files
clip-vit-b32-int8 clip-vit-b32-fp32):
    .venv/Scripts/python.exe ../../eval/photo_dupes.py [--models clip-vit-b32-int8,clip-vit-b32-fp32]

Fixtures: eval/fixtures/photos/ (24 CC0 / public-domain interiors from Wikimedia Commons; licence
and source per image in manifest.json). For each base image the harness makes 12 deterministic
edits a re-poster might make (resize, recompress, crops, colour, greyscale, rotation, mirror,
watermark, screenshot border, combined). Ground truth is by construction:
- duplicate pair: an edited image and the base it came from (24 x 12 = 288 pairs);
- non-duplicate pair: an edited image and any OTHER base, and every pair of different bases. The
  bases include deliberately similar rooms (same style, same hotel type, same era), the hard
  negatives, reported separately.
Report: eval/reports/photo_dupes.md (committed; fixture images are public).
"""

import argparse
import json
import os
import platform
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "photos"
REPORT = HERE / "reports" / "photo_dupes.md"

Transform = Callable[[Image.Image], Image.Image]


def _crop(fraction: float, anchor: tuple[float, float]) -> Transform:
    def apply(im: Image.Image) -> Image.Image:
        w, h = im.size
        cw, ch = int(w * fraction), int(h * fraction)
        x, y = int((w - cw) * anchor[0]), int((h - ch) * anchor[1])
        return im.crop((x, y, x + cw, y + ch))

    return apply


def _jpeg(quality: int) -> Transform:
    def apply(im: Image.Image) -> Image.Image:
        import io

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")

    return apply


def _resize(fraction: float) -> Transform:
    return lambda im: im.resize((int(im.width * fraction), int(im.height * fraction)))


def _colour(im: Image.Image) -> Image.Image:
    im = ImageEnhance.Brightness(im).enhance(1.25)
    im = ImageEnhance.Contrast(im).enhance(0.85)
    return ImageEnhance.Color(im).enhance(1.35)


def _rotate(im: Image.Image) -> Image.Image:
    rotated = im.rotate(4, resample=Image.Resampling.BICUBIC, expand=False)
    return _crop(0.9, (0.5, 0.5))(rotated)  # cut away the blank corners


def _watermark(im: Image.Image) -> Image.Image:
    out = im.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    draw.rectangle((0, int(h * 0.85), w, h), fill=(20, 20, 20))
    draw.text((10, int(h * 0.88)), "CALL/TEXT 416-555-0199  AVAILABLE NOW", fill=(255, 255, 0))
    return out


def _screenshot(im: Image.Image) -> Image.Image:
    w, h = im.size
    canvas = Image.new("RGB", (w + 40, h + 160), (245, 245, 245))
    canvas.paste(im, (20, 90))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, w + 40, 60), fill=(66, 103, 178))
    draw.text((20, 20), "Marketplace  |  2 bed apartment  $1,450", fill=(255, 255, 255))
    return canvas


def _combo(im: Image.Image) -> Image.Image:
    return _jpeg(50)(_resize(0.6)(_crop(0.85, (0.3, 0.6))(im)))


TRANSFORMS: dict[str, Transform] = {
    "resize_50pct": _resize(0.5),
    "jpeg_q30": _jpeg(30),
    "crop_center_80pct": _crop(0.8, (0.5, 0.5)),
    "crop_corner_70pct": _crop(0.7, (0.0, 0.0)),
    "crop_tight_55pct": _crop(0.55, (0.6, 0.4)),
    "colour_shift": _colour,
    "greyscale": lambda im: ImageOps.grayscale(im).convert("RGB"),
    "rotate_4deg": _rotate,
    "mirror": ImageOps.mirror,
    "watermark_banner": _watermark,
    "screenshot_border": _screenshot,
    "crop_resize_jpeg": _combo,
}


@dataclass(frozen=True)
class Item:
    base: int  # index of the base image it comes from
    transform: str  # "original" for a base image
    image: Image.Image


@dataclass(frozen=True)
class Pair:
    a: int  # item index
    b: int
    duplicate: bool
    hard_negative: bool  # different bases from the same similarity group
    transform: str  # transform of the edited side ("original" for base-base pairs)


def build_items(manifest: dict[str, object]) -> tuple[list[Item], list[str]]:
    images = manifest["images"]
    assert isinstance(images, list)
    bases = [Image.open(FIXTURES / e["file"]).convert("RGB") for e in images]
    groups = [str(e["group"]) for e in images]
    items = [Item(i, "original", im) for i, im in enumerate(bases)]
    for i, im in enumerate(bases):
        for name, fn in TRANSFORMS.items():
            items.append(Item(i, name, fn(im)))
    return items, groups


def build_pairs(items: list[Item], groups: list[str]) -> list[Pair]:
    originals = [k for k, it in enumerate(items) if it.transform == "original"]
    pairs: list[Pair] = []
    for a, b in combinations(originals, 2):
        ga, gb = groups[items[a].base], groups[items[b].base]
        hard = ga == gb and ga not in ("other-interiors",)
        pairs.append(Pair(a, b, False, hard, "original"))
    for k, it in enumerate(items):
        if it.transform == "original":
            continue
        for o in originals:
            same = items[o].base == it.base
            hard = (
                (not same)
                and groups[items[o].base] == groups[it.base]
                and groups[it.base] != "other-interiors"
            )
            pairs.append(Pair(k, o, same, hard, it.transform))
    return pairs


def pr_at(predicted: np.ndarray, truth: np.ndarray) -> tuple[int, int, int, float, float]:
    """TP, FP, FN, precision, recall for a boolean prediction per pair."""
    tp = int(np.sum(predicted & truth))
    fp = int(np.sum(predicted & ~truth))
    fn = int(np.sum(~predicted & truth))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return tp, fp, fn, precision, recall


def main() -> None:
    from rentalscamchecker.config import get_settings
    from rentalscamchecker.photos.embeddings import ClipEmbedder
    from rentalscamchecker.photos.hashing import hamming, hash_image
    from rentalscamchecker.photos.model_files import MODELS, model_path

    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="clip-vit-b32-int8,clip-vit-b32-fp32")
    args = parser.parse_args()
    model_names = [m for m in args.models.split(",") if m]

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    items, groups = build_items(manifest)
    pairs = build_pairs(items, groups)
    truth = np.array([p.duplicate for p in pairs])
    hard = np.array([p.hard_negative for p in pairs])
    n_dup, n_neg, n_hard = int(truth.sum()), int((~truth).sum()), int(hard.sum())

    # --- perceptual hashes ---
    hash_times: list[float] = []
    hashes = []
    for it in items:
        t = time.perf_counter()
        hashes.append(hash_image(it.image))
        hash_times.append((time.perf_counter() - t) * 1000)
    phash_d = np.array([hamming(hashes[p.a].phash, hashes[p.b].phash) for p in pairs])
    dhash_d = np.array([hamming(hashes[p.a].dhash, hashes[p.b].dhash) for p in pairs])

    # --- embeddings, per model ---
    settings = get_settings()
    sims: dict[str, np.ndarray] = {}
    embed_times: dict[str, list[float]] = {}
    load_ms: dict[str, float] = {}
    for name in model_names:
        embedder = ClipEmbedder(model_path(name, settings.photo_model_path))
        t = time.perf_counter()
        embedder.embed_images([items[0].image])  # loads the model
        load_ms[name] = (time.perf_counter() - t) * 1000
        vectors = []
        times = []
        for it in items:
            t = time.perf_counter()
            (v,) = embedder.embed_images([it.image])
            times.append((time.perf_counter() - t) * 1000)
            vectors.append(v)
        matrix = np.stack(vectors)
        sims[name] = np.array([float(matrix[p.a] @ matrix[p.b]) for p in pairs])
        embed_times[name] = times

    lines: list[str] = []
    out = lines.append
    out("# Photo duplicate detection: fixture evaluation")
    out("")
    out(f"- Date: {datetime.now(UTC).isoformat(timespec='seconds')}")
    out(
        f"- Machine: {platform.processor() or platform.machine()}, {os.cpu_count()} logical CPUs, {platform.system()} {platform.release()}, Python {platform.python_version()}"
    )
    out(
        f"- Fixtures: {len(groups)} base images (CC0/public domain, eval/fixtures/photos/manifest.json, {manifest['total_bytes'] / 1e6:.2f} MB) x {len(TRANSFORMS)} edits = {len(items) - len(groups)} edited images"
    )
    out(
        f"- Pairs: {len(pairs)} = {n_dup} duplicates + {n_neg} non-duplicates, of which {n_hard} hard negatives (different photos from the same similarity group)"
    )
    out(
        "- Synthetic edits on public images: these numbers measure robustness to the listed edits, not performance on real re-posted listings."
    )
    out("")
    out("## Latency per image (this machine, single image, after model load)")
    out("")
    out("| step | median ms | p95 ms | max ms |")
    out("|---|---|---|---|")

    def lat_row(label: str, xs: list[float]) -> None:
        xs_sorted = sorted(xs)
        p95 = xs_sorted[int(len(xs_sorted) * 0.95) - 1]
        out(f"| {label} | {statistics.median(xs):.1f} | {p95:.1f} | {max(xs):.1f} |")

    lat_row("pHash + dHash", hash_times)
    for name in model_names:
        lat_row(f"embedding, {name}", embed_times[name])
    for name in model_names:
        size = MODELS[name].size_bytes / 1e6
        out(
            f"- Model load, {name} ({size:.0f} MB file): {load_ms[name]:.0f} ms (first call includes it)"
        )
    out("")

    def sweep_distance(label: str, d: np.ndarray, thresholds: range) -> None:
        out(f"## {label}: predict duplicate when distance <= t")
        out("")
        out(
            f"Closest non-duplicate: {int(d[~truth].min())} bits (hard negatives: {int(d[hard].min())}). Farthest duplicate: {int(d[truth].max())}."
        )
        out("")
        out("| t | TP | FP | FN | precision | recall |")
        out("|---|---|---|---|---|---|")
        for t in thresholds:
            tp, fp, fn, p, r = pr_at(d <= t, truth)
            out(f"| {t} | {tp} | {fp} | {fn} | {p:.3f} | {r:.3f} |")
        out("")

    sweep_distance("pHash (64 bits)", phash_d, range(0, 25, 2))
    sweep_distance("dHash (64 bits)", dhash_d, range(0, 25, 2))

    for name in model_names:
        s = sims[name]
        out(f"## Embedding {name}: predict duplicate when cosine similarity >= s")
        out("")
        out(
            f"Most similar non-duplicate: {s[~truth].max():.4f} (hard negatives: {s[hard].max():.4f}). Least similar duplicate: {s[truth].min():.4f}."
        )
        out("")
        out("| s | TP | FP | FN | precision | recall |")
        out("|---|---|---|---|---|---|")
        for thr in [0.80, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]:
            tp, fp, fn, p, r = pr_at(s >= thr, truth)
            out(f"| {thr:.2f} | {tp} | {fp} | {fn} | {p:.3f} | {r:.3f} |")
        out("")

    # --- the combined rule used by the app (photos/matching.py) ---
    from rentalscamchecker.photos.matching import DEFAULT, Thresholds, classify

    chosen = model_names[0]
    s = sims[chosen]

    def combined(t: Thresholds) -> np.ndarray:
        return np.array(
            [
                classify(int(a), int(b), float(c), t) is not None
                for a, b, c in zip(phash_d, dhash_d, s, strict=True)
            ]
        )

    out(f"## Combined rule ({chosen}): single hash <= a, OR both hashes <= b, OR cosine >= c")
    out("")
    out(
        f"Chosen: a = {DEFAULT.single_hash}, b = {DEFAULT.both_hashes}, c = {DEFAULT.cosine}. "
        "Curve points around it (one parameter varied at a time):"
    )
    out("")
    out("| a | b | c | TP | FP | FP (hard) | FN | precision | recall |")
    out("|---|---|---|---|---|---|---|---|---|")
    variants = [Thresholds(a, DEFAULT.both_hashes, DEFAULT.cosine) for a in (6, 8, 10, 12, 14)]
    variants += [Thresholds(DEFAULT.single_hash, b, DEFAULT.cosine) for b in (14, 16, 18, 20, 22)]
    variants += [
        Thresholds(DEFAULT.single_hash, DEFAULT.both_hashes, c)
        for c in (0.92, 0.93, 0.94, 0.95, 0.96, 0.97)
    ]
    for th in variants:
        pred = combined(th)
        tp, fp, fn = (
            int((pred & truth).sum()),
            int((pred & ~truth).sum()),
            int((~pred & truth).sum()),
        )
        fp_hard = int((pred & hard).sum())
        mark = " **(chosen)**" if th == DEFAULT else ""
        out(
            f"| {th.single_hash} | {th.both_hashes} | {th.cosine:.2f}{mark} | {tp} | {fp} | {fp_hard} | {fn} | "
            f"{tp / (tp + fp) if tp + fp else 1:.3f} | {tp / (tp + fn):.3f} |"
        )
    out("")
    pred = combined(DEFAULT)
    transforms_of = np.array([p.transform for p in pairs])
    out("Recall by edit at the chosen thresholds (24 duplicates each):")
    out("")
    out("| edit | recall | matched by hash | matched by embedding only |")
    out("|---|---|---|---|")
    for name in TRANSFORMS:
        m = truth & (transforms_of == name)
        by_hash = int(
            sum(
                1
                for i in np.flatnonzero(m)
                if classify(int(phash_d[i]), int(dhash_d[i]), None) == "hash"
            )
        )
        out(f"| {name} | {pred[m].mean():.2f} | {by_hash} | {int(pred[m].sum()) - by_hash} |")
    out("")
    out(
        "Known gap: crops that remove 30% or more of the picture are mostly missed; their hashes "
        "change completely and their embeddings fall into the range of different-but-similar rooms."
    )

    print("\n".join(lines))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw = {
        "pairs": [p.__dict__ for p in pairs],
        "phash": phash_d.tolist(),
        "dhash": dhash_d.tolist(),
        "sims": {k: v.tolist() for k, v in sims.items()},
    }
    (HERE / "private").mkdir(exist_ok=True)
    (HERE / "private" / "photo_dupes_scores.json").write_text(json.dumps(raw), encoding="utf-8")


if __name__ == "__main__":
    main()
