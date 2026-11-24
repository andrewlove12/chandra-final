"""Shared model settings for download_model.py and process_pdfs.py."""
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Chandra OCR 2 (Datalab). Pinned to a specific commit so every machine runs
# the exact same weights. To upgrade, change MODEL_REVISION and re-run
# `python download_model.py --force`.
MODEL_REPO = "datalab-to/chandra-ocr-2"
MODEL_REVISION = "af93b47dba1b47b6640c86ccf487ed2260ab9a09"
MODEL_DIR = os.path.join(PROJECT_DIR, "models", "chandra-ocr-2")

_REQUIRED_FILES = ("config.json", "model.safetensors", "tokenizer.json", "preprocessor_config.json")


def model_is_downloaded() -> bool:
    if not all(os.path.isfile(os.path.join(MODEL_DIR, f)) for f in _REQUIRED_FILES):
        return False
    try:
        with open(os.path.join(MODEL_DIR, "REVISION")) as f:
            return f.read().strip() == MODEL_REVISION
    except OSError:
        return False
