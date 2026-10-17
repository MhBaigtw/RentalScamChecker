"""The embedding model files: where they come from, their checksums, and a fetch command.

A PRETRAINED MODEL WE RUN (not trained by us): OpenAI's CLIP ViT-B/32 image encoder, released
under the MIT licence (github.com/openai/CLIP), exported to ONNX by third parties. Files are
pinned to a repository revision and verified by SHA-256 after download (ED-025).

Download (from apps/api, ~89 MB for the default):
    .venv/Scripts/python.exe -m rentalscamchecker.photos.model_files [name ...]
Files go to var/models/ (gitignored, PHOTO_MODEL_DIR).
"""

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from rentalscamchecker.config import get_settings


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size_bytes: int
    filename: str
    licence: str
    note: str


MODELS: dict[str, ModelFile] = {
    m.name: m
    for m in (
        ModelFile(
            name="clip-vit-b32-int8",
            url=(
                "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/"
                "d15189d7028b43f1d3e65039190477f6af591c2a/onnx/vision_model_int8.onnx"
            ),
            sha256="0ab0c1b3ace708e539633af1744d5a95247fe4e14d3e08ff197ef82a6cb9bd93",
            size_bytes=88_648_877,
            filename="clip-vit-b32-vision-int8.onnx",
            licence="MIT (OpenAI CLIP weights); the Xenova export has no licence tag of its own",
            note="8-bit quantized vision tower; outputs image_embeds (512)",
        ),
        ModelFile(
            name="clip-vit-b32-fp32",
            url=(
                "https://huggingface.co/Qdrant/clip-ViT-B-32-vision/resolve/"
                "e0c24ed0fa57fa3e4f97f30de74c51d944036ace/model.onnx"
            ),
            sha256="c68d3d9a200ddd2a8c8a5510b576d4c94d1ae383bf8b36dd8c084f94e1fb4d63",
            size_bytes=351_686_194,
            filename="clip-vit-b32-vision-fp32.onnx",
            licence="MIT (repository tag; OpenAI CLIP weights)",
            note="full-precision vision tower; reference for the int8 comparison",
        ),
    )
}


def model_path(name: str, model_dir: Path) -> Path:
    return model_dir / MODELS[name].filename


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str, model_dir: Path) -> Path:
    """Download unless a file with the right checksum is already there."""
    spec = MODELS[name]
    target = model_path(name, model_dir)
    if target.exists() and sha256_of(target) == spec.sha256:
        print(f"{name}: already present ({target})")
        return target
    model_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    with httpx.stream("GET", spec.url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with partial.open("wb") as f:
            for chunk in response.iter_bytes(1 << 20):
                f.write(chunk)
    actual = sha256_of(partial)
    if actual != spec.sha256:
        partial.unlink()
        raise RuntimeError(f"{name}: checksum mismatch ({actual} != {spec.sha256}); deleted")
    partial.replace(target)
    print(f"{name}: downloaded {target.stat().st_size / 1e6:.1f} MB to {target}, sha256 OK")
    return target


def main() -> None:
    settings = get_settings()
    names = sys.argv[1:] or [settings.photo_embedding_model]
    for name in names:
        if name not in MODELS:
            raise SystemExit(f"unknown model {name!r}; known: {', '.join(MODELS)}")
        fetch(name, settings.photo_model_path)


if __name__ == "__main__":
    main()
