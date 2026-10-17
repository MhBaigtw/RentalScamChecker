"""Image embeddings from a PRETRAINED MODEL WE RUN on CPU: OpenAI's CLIP ViT-B/32 image encoder
(MIT licence) in ONNX format, executed with onnxruntime. We did not train it (ED-025).

Two photos of the same scene give nearby 512-dimensional vectors even after crops, mirroring,
colour changes or overlays that break perceptual hashes. Vectors are L2-normalised, so cosine
similarity is a dot product, and pgvector's cosine distance is 1 - similarity.

Preprocessing reproduces CLIPImageProcessor (the model's preprocessor_config.json): resize the
shortest side to 224 (bicubic), centre-crop 224x224, scale to [0, 1], normalise with CLIP's mean
and standard deviation, channels first.
"""

import io
import threading
from pathlib import Path

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
from PIL import Image

SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
DIMENSIONS = 512

Vector = npt.NDArray[np.float32]


def preprocess(image: Image.Image) -> npt.NDArray[np.float32]:
    """One image -> float32 array of shape (3, 224, 224)."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    scale = SIZE / min(width, height)
    resized = rgb.resize(
        (max(SIZE, round(width * scale)), max(SIZE, round(height * scale))),
        Image.Resampling.BICUBIC,
    )
    left = (resized.width - SIZE) // 2
    top = (resized.height - SIZE) // 2
    cropped = resized.crop((left, top, left + SIZE, top + SIZE))
    pixels = np.asarray(cropped, dtype=np.float32) / 255.0
    normalised = (pixels - MEAN) / STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1))


class ClipEmbedder:
    """Loads the ONNX model on first use (the API process never needs it) and is safe to share
    between worker threads (onnxruntime sessions support concurrent run calls)."""

    def __init__(self, model_file: Path, threads: int = 0) -> None:
        self.model_file = model_file
        self._threads = threads
        self._session: ort.InferenceSession | None = None
        self._lock = threading.Lock()

    def _get_session(self) -> ort.InferenceSession:
        with self._lock:
            if self._session is None:
                if not self.model_file.exists():
                    raise FileNotFoundError(
                        f"embedding model not found at {self.model_file}; run "
                        "`python -m rentalscamchecker.photos.model_files` from apps/api"
                    )
                options = ort.SessionOptions()
                if self._threads:
                    options.intra_op_num_threads = self._threads
                self._session = ort.InferenceSession(
                    str(self.model_file), options, providers=["CPUExecutionProvider"]
                )
            return self._session

    def embed_images(self, images: list[Image.Image]) -> list[Vector]:
        if not images:
            return []
        batch = np.stack([preprocess(i) for i in images])
        (embeds,) = self._get_session().run(["image_embeds"], {"pixel_values": batch})
        array = np.asarray(embeds, dtype=np.float32)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        return list(array / np.maximum(norms, 1e-12))

    def embed_bytes(self, data: bytes) -> Vector:
        with Image.open(io.BytesIO(data)) as image:
            return self.embed_images([image])[0]


def cosine_similarity(a: Vector, b: Vector) -> float:
    """For L2-normalised vectors."""
    return float(np.dot(a, b))
