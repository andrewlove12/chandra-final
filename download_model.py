"""One-time download of the Chandra OCR 2 model weights and the page-orientation model.

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
from orientation import (ORIENTATION_DIR, ORIENTATION_FILE, ORIENTATION_REPO, ORIENTATION_REVISION,
                         orientation_model_is_downloaded)

# Hugging Face anonymous usage telemetry off.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
# When the output isn't a console (the installer shows it in its window), progress
# bars that redraw one line don't work: print a plain progress line instead.
INTERACTIVE = sys.stdout.isatty()
if not INTERACTIVE:
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"


def _folder_bytes(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def start_progress_lines(repo: str, revision: str, folder: str, every: float = 15.0):
    """Print 'Downloaded X of Y GB' every few seconds (non-console output only). Returns a stop function."""
    if INTERACTIVE:
        return lambda: None
    import threading
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, revision=revision, files_metadata=True)
        expected = sum(s.size or 0 for s in info.siblings)
    except Exception:
        expected = 0
    done = threading.Event()

    def run():
        while not done.wait(every):
            got = _folder_bytes(folder) / 1e9
            if expected:
                print(f"Downloaded {got:.1f} of {expected / 1e9:.1f} GB ({min(100, 100 * got * 1e9 / expected):.0f}%)",
                      flush=True)
            else:
                print(f"Downloaded {got:.1f} GB", flush=True)

    threading.Thread(target=run, daemon=True).start()
    return done.set


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Download again even if the model is present.")
    args = parser.parse_args()

    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        print("huggingface_hub is not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    if model_is_downloaded() and not args.force:
        print(f"Chandra OCR 2 model already present in {MODEL_DIR}")
    else:
        print(f"Downloading {MODEL_REPO} (revision {MODEL_REVISION[:12]}) to {MODEL_DIR}")
        print("This is roughly 10 GB and only needs to happen once...", flush=True)
        stop = start_progress_lines(MODEL_REPO, MODEL_REVISION, MODEL_DIR)
        try:
            snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, local_dir=MODEL_DIR)
        finally:
            stop()
        with open(os.path.join(MODEL_DIR, "REVISION"), "w") as f:
            f.write(MODEL_REVISION)

    # Small (~7 MB) page-orientation classifier used before OCR (orientation.py).
    if orientation_model_is_downloaded() and not args.force:
        print(f"Page orientation model already present in {ORIENTATION_DIR}")
    else:
        print(f"Downloading {ORIENTATION_REPO} (revision {ORIENTATION_REVISION[:12]}) to {ORIENTATION_DIR}")
        for name in (ORIENTATION_FILE, "inference.yml"):
            hf_hub_download(repo_id=ORIENTATION_REPO, filename=name, revision=ORIENTATION_REVISION,
                            local_dir=ORIENTATION_DIR)
        with open(os.path.join(ORIENTATION_DIR, "REVISION"), "w") as f:
            f.write(ORIENTATION_REVISION)

    print("Model downloads complete. Processing can now run offline.")


if __name__ == "__main__":
    main()
