"""Detect pages scanned sideways or upside down, before OCR.

Uses PaddleOCR's document orientation classifier (PP-LCNet_x1_0_doc_ori,
Apache-2.0, ~7 MB ONNX), run on the CPU with onnxruntime: well under a second
per page. It's downloaded once by download_model.py and runs offline.

To be robust on messy handwriting, each page is classified in all four
orientations and the votes are combined: if the page is really upside down,
then turning it 90 degrees should make the model say "sideways", and so on.
Only a confident result turns a page; otherwise it's left as is.
"""
import os

import numpy as np
from PIL import Image

from model_config import PROJECT_DIR

ORIENTATION_REPO = "PaddlePaddle/PP-LCNet_x1_0_doc_ori_onnx"
ORIENTATION_REVISION = "7330ab7039123e46af2dc03154b9969aa412c61d"
ORIENTATION_DIR = os.path.join(PROJECT_DIR, "models", "doc-orientation")
ORIENTATION_FILE = "inference.onnx"

# Model output order (from the repo's inference.yml). Label N means the page's
# content is turned N degrees clockwise (verified on the public sample form), so
# turning it a further (360 - N) degrees clockwise makes it upright.
LABELS = (0, 90, 180, 270)
MIN_CONFIDENCE = 0.80       # combined probability needed before a page is turned
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def orientation_model_is_downloaded() -> bool:
    try:
        with open(os.path.join(ORIENTATION_DIR, "REVISION")) as f:
            ok = f.read().strip() == ORIENTATION_REVISION
    except OSError:
        return False
    return ok and os.path.isfile(os.path.join(ORIENTATION_DIR, ORIENTATION_FILE))


def _preprocess(img: Image.Image) -> np.ndarray:
    """Exactly the repo's inference.yml: resize short side to 256, center-crop 224, ImageNet normalize, CHW."""
    img = img.convert("RGB")
    w, h = img.size
    s = 256 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.Resampling.BILINEAR)
    w, h = img.size
    left, top = (w - 224) // 2, (h - 224) // 2
    img = img.crop((left, top, left + 224, top + 224))
    x = np.asarray(img, dtype=np.float32) / 255.0
    x = (x - _MEAN) / _STD
    return x.transpose(2, 0, 1)


class OrientationDetector:
    def __init__(self):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # hide harmless "unused initializer" warnings
        self.session = ort.InferenceSession(os.path.join(ORIENTATION_DIR, ORIENTATION_FILE), opts,
                                            providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def label_probs(self, images) -> np.ndarray:
        """Probabilities over LABELS for each image (N x 4). The model's output is already softmaxed."""
        batch = np.stack([_preprocess(im) for im in images])
        p = self.session.run(None, {self.input_name: batch})[0].astype(np.float64)
        return p / p.sum(axis=1, keepdims=True)

    def detect(self, img: Image.Image):
        """Return (clockwise degrees to make the page upright, confidence, per-correction scores).

        The page is classified as-is and turned 90/180/270 clockwise. If the page
        needs a clockwise turn of C to be upright, its content currently sits at
        -C, so the copy turned by k has content at (k - C) mod 360, which is the
        label the model should give it. Each copy's probability for that label
        is a vote for C; the four votes multiply (summed as logs).
        """
        copies = [img.rotate(-k, expand=True) for k in LABELS]  # k degrees clockwise
        probs = self.label_probs(copies)                        # [copy k][label]
        log_scores = np.zeros(4)
        for ci, correction in enumerate(LABELS):
            for ki, k in enumerate(LABELS):
                # copy k's content is turned (k - correction) mod 360 clockwise from upright
                label = (k - correction) % 360
                log_scores[ci] += np.log(probs[ki][LABELS.index(label)] + 1e-9)
        scores = np.exp(log_scores - log_scores.max())
        scores /= scores.sum()
        best = int(scores.argmax())
        return LABELS[best], float(scores[best]), scores

    def correction_for(self, img: Image.Image) -> int:
        """Clockwise degrees to turn `img` upright, or 0 if not confident."""
        correction, confidence, _ = self.detect(img)
        return correction if confidence >= MIN_CONFIDENCE else 0
