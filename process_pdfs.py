"""Overlay Chandra OCR 2 transcriptions (in red) onto handwritten lab-notebook PDFs.

Runs fully offline: the model is loaded from ./models/ (see download_model.py),
Hugging Face offline mode is forced, and all outbound network connections are
blocked for the lifetime of the process, so notebook contents can never leave
this machine.

Usage:
    python process_pdfs.py                          # prompts for folders
    python process_pdfs.py INPUT_DIR OUTPUT_DIR [--device cpu|cuda|auto] [--dpi 300] [--skip-existing]
"""
import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import traceback

# --- Offline enforcement -----------------------------------------------------
# Must run before transformers / huggingface_hub are imported, because they
# read these variables at import time.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")


def _is_local_host(host) -> bool:
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def block_network():
    """Refuse any non-loopback network connection or DNS lookup in this process."""
    def _guard(address):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_local_host(host):
            raise ConnectionRefusedError(f"Network access is blocked (offline mode): attempted connection to {host!r}")

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_sendto = socket.socket.sendto
    orig_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        _guard(address)
        return orig_connect(self, address)

    def connect_ex(self, address):
        _guard(address)
        return orig_connect_ex(self, address)

    def sendto(self, data, *args):
        _guard(args[-1])
        return orig_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        _guard((host,))
        return orig_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo


block_network()

# Heavy imports happen after the guard is in place.
import pymupdf  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from model_config import MODEL_DIR, MODEL_REVISION, model_is_downloaded  # noqa: E402

RED = (1, 0, 0)
MIN_PDF_IMAGE_DIM = 2048  # shortest page side in pixels when rendering
CACHE_DIR_NAME = ".ocr_cache"


# --- Rendering ---------------------------------------------------------------

def render_html_to_pdf_page(page, html_content: str, rect: pymupdf.Rect, color=RED):
    soup = BeautifulSoup(html_content, "html.parser")

    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        if not rows:
            text = soup.get_text(separator=" ", strip=True)
            page.insert_textbox(rect, text, fontsize=6, color=color)
            return

        max_cols = max(
            (sum(int(cell.get("colspan", 1)) for cell in row.find_all(["td", "th"])) for row in rows),
            default=1,
        ) or 1
        row_height = rect.height / len(rows)
        col_width = rect.width / max_cols

        y = rect.y0
        for row in rows:
            x = rect.x0
            for cell in row.find_all(["td", "th"]):
                cell_width = col_width * int(cell.get("colspan", 1))
                cell_rect = pymupdf.Rect(x, y, x + cell_width, y + row_height)

                for br in cell.find_all("br"):
                    br.replace_with("\n")
                cell_text = cell.get_text(separator=" ", strip=True)

                if cell_text:
                    lines = cell_text.count("\n") + 1
                    fs = min(9.0, max(2.0, (cell_rect.height / lines) * 0.6))
                    rc = -1
                    while fs >= 2 and rc < 0:
                        # Left-aligned, as handwriting naturally starts left
                        rc = page.insert_textbox(cell_rect, cell_text, fontsize=fs, color=color, align=0)
                        if rc < 0:
                            fs *= 0.8
                x += cell_width
            y += row_height
        return

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_after("\n")

    text = soup.get_text(separator="", strip=False)
    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
    text = re.sub(r"[ \t]{2,}", "  ", text)
    if not text:
        return

    lines = text.count("\n") + 1
    fs = min(8.0, max(2.0, (rect.height / lines) * 0.6))
    rc = -1
    while fs >= 2 and rc < 0:
        rc = page.insert_textbox(rect, text, fontsize=fs, color=color, align=0)
        if rc < 0:
            fs *= 0.8
    if rc < 0:
        page.insert_text(rect.bottom_left, text, fontsize=2, fontname="helv", color=color)


# --- OCR with cache ----------------------------------------------------------

def ocr_page(manager, input_pdf: str, page_index: int, cache_file: str, dpi: int):
    """Return (chunks, (img_width, img_height)) for one page, using the cache when valid."""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("model_revision") == MODEL_REVISION and cached.get("image_dpi") == dpi:
                print("    Using cached OCR result")
                return cached["chunks"], tuple(cached["image_size"])
        except (OSError, ValueError, KeyError):
            pass  # corrupt/old cache: just redo the page

    from chandra.input import load_pdf_images
    from chandra.model.schema import BatchInputItem

    # Render one page at a time to keep memory down on long notebooks. DPI is
    # passed explicitly: chandra binds its defaults at import time.
    img = load_pdf_images(input_pdf, page_range=[page_index], image_dpi=dpi, min_pdf_image_dim=MIN_PDF_IMAGE_DIM)[0]
    result = manager.generate([BatchInputItem(image=img, prompt_type="ocr_layout")])[0]
    if result.error:
        print("    Warning: model reported an error for this page; output may be incomplete")

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({
            "model_revision": MODEL_REVISION,
            "image_dpi": dpi,
            "image_size": list(img.size),
            "chunks": result.chunks,
        }, f)
    return result.chunks, img.size


def process_pdf(input_pdf: str, output_pdf: str, cache_dir: str, manager, dpi: int):
    print(f"Processing {input_pdf}...")
    try:
        doc = pymupdf.open(input_pdf)
    except Exception as e:
        print(f"  Failed to open PDF: {e}")
        return

    try:
        for page_index, page in enumerate(doc):
            print(f"  Page {page_index + 1}/{len(doc)}")
            cache_file = os.path.join(cache_dir, f"page_{page_index + 1:04d}.json")
            try:
                chunks, (img_w, img_h) = ocr_page(manager, input_pdf, page_index, cache_file, dpi)
            except Exception:
                print(f"  Error running OCR on page {page_index + 1}:")
                traceback.print_exc()
                continue

            # Chunk bboxes are in rendered-image pixels; map to PDF points.
            scale_x = page.rect.width / img_w
            scale_y = page.rect.height / img_h

            for chunk in chunks:
                content = chunk.get("content")
                if chunk.get("label") in ("Image", "Figure") and not content:
                    continue
                x0, y0, x1, y1 = chunk["bbox"]
                rect = pymupdf.Rect(x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y)
                try:
                    render_html_to_pdf_page(page, content or "", rect, color=RED)
                except Exception as e:
                    print(f"    Failed to render block at {rect}: {e}")

        os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
        doc.save(output_pdf)
        print(f"  Saved {output_pdf}")
    except Exception as e:
        print(f"  Failed to save {output_pdf}: {e}")
    finally:
        doc.close()


# --- Entry point -------------------------------------------------------------

def is_within(path: str, parent: str) -> bool:
    path, parent = os.path.abspath(path), os.path.abspath(parent)
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:  # different drives on Windows
        return False


def find_pdfs(input_dir: str, output_dir: str):
    pdfs = []
    for root, dirs, files in os.walk(input_dir):
        # Never descend into the output folder if it lives inside the input folder.
        dirs[:] = [d for d in dirs if not is_within(os.path.join(root, d), output_dir)]
        pdfs.extend(os.path.join(root, f) for f in files if f.lower().endswith(".pdf"))
    return sorted(pdfs)


def parse_args():
    parser = argparse.ArgumentParser(description="Overlay offline Chandra OCR 2 transcriptions onto PDFs.")
    parser.add_argument("input_dir", nargs="?", help="Folder containing notebook PDFs (searched recursively)")
    parser.add_argument("output_dir", nargs="?", help="Folder to write annotated PDFs to")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu",
                        help="Where to run the model (default: cpu)")
    parser.add_argument("--dpi", type=int, default=300,
                        help="Page render resolution sent to the model (default: 300)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip PDFs whose output file already exists")
    return parser.parse_args()


def main():
    args = parse_args()
    print("=== Chandra OCR 2 PDF Annotator (offline) ===")

    input_dir = args.input_dir or input("Enter the input directory containing PDF files: ").strip().strip('"')
    if not os.path.isdir(input_dir):
        print(f"Error: input directory '{input_dir}' does not exist.")
        return 1
    output_dir = args.output_dir or input("Enter the output directory for processed PDFs: ").strip().strip('"')

    if not model_is_downloaded():
        print(f"Error: Chandra OCR 2 model not found in {MODEL_DIR}.")
        print("Run `python download_model.py` once while connected to the internet.")
        return 1

    pdf_files = find_pdfs(input_dir, output_dir)
    if not pdf_files:
        print(f"No PDF files found in '{input_dir}'.")
        return 0
    print(f"Found {len(pdf_files)} PDF(s) to process.")

    # Device selection must happen before torch is imported.
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    try:
        from chandra.settings import settings
        from chandra.model import InferenceManager
    except ImportError:
        print("chandra-ocr is not installed. Run run.ps1, or: pip install -r requirements.txt")
        return 1

    settings.MODEL_CHECKPOINT = MODEL_DIR  # local folder only, never the Hub
    if args.device != "auto":
        settings.TORCH_DEVICE = args.device

    import logging
    # Harmless kwargs warning, see https://github.com/vllm-project/vllm/issues/39768
    logging.getLogger("transformers.processing_utils").setLevel(logging.ERROR)

    import torch
    if args.device == "cuda" and not torch.cuda.is_available():
        print("Error: --device cuda requested but no CUDA GPU is available to PyTorch.")
        return 1
    if args.device == "cpu" or not torch.cuda.is_available():
        print("Running on CPU. Expect several minutes per page; 32 GB+ of RAM recommended.")

    print("Loading Chandra OCR 2 model... (this may take a moment)")
    try:
        manager = InferenceManager(method="hf")
    except Exception as e:
        print(f"Failed to load model: {e}")
        return 1

    for input_pdf in pdf_files:
        rel_path = os.path.relpath(input_pdf, input_dir)
        output_pdf = os.path.join(output_dir, rel_path)
        if args.skip_existing and os.path.exists(output_pdf):
            print(f"Skipping {input_pdf} (output exists)")
            continue
        cache_dir = os.path.join(output_dir, CACHE_DIR_NAME, rel_path)
        process_pdf(input_pdf, output_pdf, cache_dir, manager, args.dpi)

    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
