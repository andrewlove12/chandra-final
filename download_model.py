"""One-time download of the Chandra OCR 2 model weights.

This is the ONLY part of the project that uses the internet. It downloads the
public model files from Hugging Face into ./models/ and never reads, uploads,
or otherwise touches any notebook PDFs. Once it has finished, process_pdfs.py
runs fully offline.

Usage:
    python download_model.py          # download if not already present
    python download_model.py --force  # re-download / repair
"""
import argparse
import os
import sys

from model_config import MODEL_DIR, MODEL_REPO, MODEL_REVISION, model_is_downloaded

# Hugging Face anonymous usage telemetry off.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Download again even if the model is present.")
    args = parser.parse_args()

    if model_is_downloaded() and not args.force:
        print(f"Chandra OCR 2 model already present in {MODEL_DIR}")
        return

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    print(f"Downloading {MODEL_REPO} (revision {MODEL_REVISION[:12]}) to {MODEL_DIR}")
    print("This is roughly 10 GB and only needs to happen once...")
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, local_dir=MODEL_DIR)

    with open(os.path.join(MODEL_DIR, "REVISION"), "w") as f:
        f.write(MODEL_REVISION)
    print("Model download complete. Processing can now run offline.")


if __name__ == "__main__":
    main()
