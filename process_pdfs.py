"""Overlay Chandra OCR 2 transcriptions (in red) onto handwritten lab-notebook PDFs.

Runs fully offline: the model is loaded from ./models/ (see download_model.py),
Hugging Face offline mode is forced, and all outbound network connections are
blocked for the lifetime of the process, so notebook contents can never leave
this machine.

Usage:
    python process_pdfs.py                          # prompts for folders
    python process_pdfs.py INPUT_DIR OUTPUT_DIR [--device auto|cuda|cpu] [--gpu-reserve-gb 2.5]
                           [--dpi 300] [--skip-existing] [--rotate [FILE:]PAGES=DEG]
"""
import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import time
import traceback

# --- Offline enforcement -----------------------------------------------------
# Must run before transformers / huggingface_hub are imported, because they
# read these variables at import time.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
# AMD ROCm: RDNA3 cards (RX 7000) only get fast/memory-efficient attention kernels
# with this flag. Without them the vision encoder needs ~36 GB VRAM or crashes.
# Ignored on NVIDIA/CPU.
os.environ.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")


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

# Overlay text sizes, in points on a letter-width (612 pt) page. Scans are often
# embedded in small pages (a 202 pt-wide page shows 2 pt text as big as a letter
# page shows 6 pt), so on pages narrower than letter the limits shrink in
# proportion. Wider pages keep them as is: their text is real-size, not zoomed.
MIN_FONT_LETTER_PT = 6.0   # never smaller than this; text spills out of its box instead
MAX_FONT_LETTER_PT = 14.0


def _insert_box(page, rect: pymupdf.Rect, text: str, fontsize: float, color) -> float:
    """insert_textbox taking `rect` as seen on screen, so it also works on rotated pages.

    PyMuPDF places text in unrotated page coordinates, so the box is mapped back
    through the page's derotation matrix and the text is turned to match the
    page's /Rotate, which makes it read upright in a viewer. Returns PyMuPDF's
    rc: >= 0 means drawn (spare height); < 0 means nothing was drawn and -rc is
    roughly the missing height.
    """
    if page.rotation:
        rect = (rect * page.derotation_matrix).normalize()
    return page.insert_textbox(rect, text, fontsize=fontsize, color=color, align=0, rotate=page.rotation)


def _fit_text(page, rect: pymupdf.Rect, text: str, color, lines: int = 1):
    """Draw `text` anchored at `rect`'s top-left, as large as fits, never below the minimum size.

    If it doesn't fit at the minimum size, the box grows (down to the page
    bottom, then right to the page edge, then up) so every word is drawn,
    overlapping neighbouring text if necessary. Returns (fontsize, grew).
    """
    scale = min(1.0, page.rect.width / 612)  # shrink limits on small pages only
    min_fs, max_fs = MIN_FONT_LETTER_PT * scale, MAX_FONT_LETTER_PT * scale
    fs = max(min_fs, min(max_fs, rect.height / max(1, lines) * 0.6))
    while True:  # shrink toward the minimum while the box is big enough
        if _insert_box(page, rect, text, fs, color) >= 0:
            return fs, False
        if fs <= min_fs:
            break
        fs = max(min_fs, fs * 0.85)

    bounds = page.rect
    r = pymupdf.Rect(rect)
    for _ in range(60):
        rc = _insert_box(page, r, text, fs, color)
        if rc >= 0:
            return fs, True
        need = -rc + fs  # a line of slack: rc is approximate
        if r.y1 < bounds.y1:
            r.y1 = min(bounds.y1, r.y1 + need)
        elif r.x1 < bounds.x1:
            r.x1 = bounds.x1
        elif r.y0 > bounds.y0:
            r.y0 = max(bounds.y0, r.y0 - need)
        elif r.x0 > bounds.x0:
            r.x0 = bounds.x0
        else:
            fs *= 0.9  # even the whole page can't hold it at the minimum size
    return fs, True


def _words(text: str) -> int:
    return len(text.split())


def render_html_to_pdf_page(page, html_content: str, rect: pymupdf.Rect, color=RED):
    """Draw one OCR block's text into `rect` (visual page coordinates).

    Returns [(words, fontsize, grew_out_of_box), ...] for each piece drawn.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    drawn = []

    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        if not rows:
            text = soup.get_text(separator=" ", strip=True)
            if text:
                drawn.append((_words(text), *_fit_text(page, rect, text, color)))
            return drawn

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
                    # Left-aligned, as handwriting naturally starts left
                    drawn.append((_words(cell_text),
                                  *_fit_text(page, cell_rect, cell_text, color, cell_text.count("\n") + 1)))
                x += cell_width
            y += row_height
        return drawn

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_after("\n")

    text = soup.get_text(separator="", strip=False)
    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
    text = re.sub(r"[ \t]{2,}", "  ", text)
    if text:
        drawn.append((_words(text), *_fit_text(page, rect, text, color, text.count("\n") + 1)))
    return drawn


# --- Machine-readable progress (for gui.py) ----------------------------------

PROGRESS_JSON = False  # set by --progress-json


def emit(event: str, **fields):
    """With --progress-json, print one '@@{json}' line per event for the GUI to parse."""
    if PROGRESS_JSON:
        print("@@" + json.dumps({"event": event, **fields}), flush=True)


# --- OCR with cache ----------------------------------------------------------

def ocr_page(manager, input_pdf: str, page_index: int, cache_file: str, dpi: int, quantize: str, rotate: int = 0):
    """Return (chunks, (img_width, img_height), info) for one page, using the cache when valid.

    `rotate` turns the rendered page clockwise by that many degrees before OCR
    (for pages scanned sideways/upside down); boxes are in the turned image.
    `info` has cached / seconds / tokens / attempts / warning, for progress reporting.
    """
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # A page that ran into the token cap was cut off (usually a repetition
            # loop from before repeat_guard existed), so it's redone, not reused.
            from chandra.settings import settings
            if (cached.get("model_revision") == MODEL_REVISION and cached.get("image_dpi") == dpi
                    and cached.get("quantization", "none") == quantize
                    and cached.get("rotation", 0) == rotate
                    and cached.get("token_count", 0) < settings.MAX_OUTPUT_TOKENS):
                print("    Using cached OCR result")
                info = {"cached": True, "seconds": 0, "tokens": cached.get("token_count", 0),
                        "attempts": cached.get("attempts", 1), "warning": bool(cached.get("error"))}
                return cached["chunks"], tuple(cached["image_size"]), info
        except (OSError, ValueError, KeyError):
            pass  # corrupt/old cache: just redo the page

    from chandra.input import load_pdf_images
    from chandra.model.schema import BatchInputItem

    # Render one page at a time to keep memory down on long notebooks. DPI is
    # passed explicitly: chandra binds its defaults at import time.
    img = load_pdf_images(input_pdf, page_range=[page_index], image_dpi=dpi, min_pdf_image_dim=MIN_PDF_IMAGE_DIM)[0]
    if rotate:
        img = img.rotate(-rotate, expand=True)  # PIL turns counter-clockwise; we want clockwise
    start = time.perf_counter()
    result = manager.generate([BatchInputItem(image=img, prompt_type="ocr_layout")])[0]
    seconds = time.perf_counter() - start
    import repeat_guard
    stats = dict(repeat_guard.last_stats)
    print(f"    OCR took {seconds:.1f}s ({result.token_count} tokens, {stats['attempts']} attempt(s))")
    if result.error:
        print("    Warning: this page's output may be incomplete or contain a repeated passage")

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({
            "model_revision": MODEL_REVISION,
            "image_dpi": dpi,
            "quantization": quantize,
            "rotation": rotate,
            "image_size": list(img.size),
            "seconds": round(seconds, 2),
            "token_count": result.token_count,
            "attempts": stats["attempts"],
            "looped": stats["looped"],
            "error": bool(result.error),
            "chunks": result.chunks,
        }, f)
    info = {"cached": False, "seconds": round(seconds, 1), "tokens": result.token_count,
            "attempts": stats["attempts"], "warning": bool(result.error)}
    return result.chunks, img.size, info


def free_gpu_cache():
    """Release per-page GPU memory between pages (no-op if torch isn't loaded / no GPU)."""
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def process_pdf(input_pdf: str, output_pdf: str, cache_dir: str, manager, dpi: int, quantize: str = "none",
                rotations: dict | None = None, index: int = 1, total: int = 1, rel_path: str = ""):
    """`rotations` maps 1-based page numbers to clockwise degrees (see --rotate).

    Returns the number of pages that failed (-1 if the PDF couldn't be opened).
    """
    rotations = rotations or {}
    print(f"Processing {input_pdf}...")
    try:
        doc = pymupdf.open(input_pdf)
    except Exception as e:
        print(f"  Failed to open PDF: {e}")
        emit("pdf_error", index=index, total=total, name=rel_path, message=f"Couldn't open PDF: {e}")
        return -1

    failed = 0
    emit("pdf_start", index=index, total=total, name=rel_path, pages=len(doc))
    try:
        for page_index, page in enumerate(doc):
            rotate = rotations.get(page_index + 1, 0)
            print(f"  Page {page_index + 1}/{len(doc)}" + (f" (rotated {rotate}°)" if rotate else ""))
            emit("page_start", index=index, page=page_index + 1, pages=len(doc), rotated=rotate)
            cache_file = os.path.join(cache_dir, f"page_{page_index + 1:04d}.json")
            try:
                chunks, (img_w, img_h), info = ocr_page(manager, input_pdf, page_index, cache_file, dpi, quantize,
                                                        rotate)
            except Exception as e:
                failed += 1
                if "out of memory" in str(e).lower():
                    msg = ("GPU ran out of memory. Re-run with a larger GPU memory reserve "
                           "(more of the model goes to system RAM); finished pages are cached.")
                    print(f"  Page {page_index + 1}: {msg}")
                else:
                    msg = f"OCR failed: {type(e).__name__}: {e}"
                    print(f"  Error running OCR on page {page_index + 1}:")
                    traceback.print_exc()
                emit("page_error", index=index, page=page_index + 1, pages=len(doc), message=msg)
                continue
            finally:
                free_gpu_cache()

            if rotate:
                # Turn the output page the same way, so it displays upright and
                # page.rect (the on-screen size) matches the OCR'd image.
                page.set_rotation((page.rotation + rotate) % 360)

            # Chunk bboxes are in rendered-image pixels; map to on-screen PDF points.
            scale_x = page.rect.width / img_w
            scale_y = page.rect.height / img_h

            words = spilled = 0
            for chunk in chunks:
                content = chunk.get("content")
                if chunk.get("label") in ("Image", "Figure") and not content:
                    continue
                x0, y0, x1, y1 = chunk["bbox"]
                rect = pymupdf.Rect(x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y)
                try:
                    for n, _, grew in render_html_to_pdf_page(page, content or "", rect, color=RED):
                        words += n
                        spilled += n if grew else 0
                except Exception as e:
                    print(f"    Failed to render block at {rect}: {e}")
            print(f"    Overlay: {words} words" + (f", {spilled} of them spill past their box" if spilled else ""))
            emit("page_done", index=index, page=page_index + 1, pages=len(doc), words=words, spilled=spilled, **info)

        if failed == len(doc):
            # Saving would leave an overlay-less copy that --skip-existing then skips for good.
            print(f"  Not saving {output_pdf}: OCR failed on every page")
            emit("pdf_done", index=index, total=total, name=rel_path, saved=False, failed=failed)
            return failed
        os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
        doc.save(output_pdf)
        print(f"  Saved {output_pdf}")
        emit("pdf_done", index=index, total=total, name=rel_path, saved=True, failed=failed, output=output_pdf)
    except Exception as e:
        print(f"  Failed to save {output_pdf}: {e}")
        emit("pdf_done", index=index, total=total, name=rel_path, saved=False, failed=failed,
             message=f"Couldn't save: {e}")
    finally:
        doc.close()
    return failed


# --- Model loading -----------------------------------------------------------

GIB = 1024 ** 3


def pick_gpu(torch):
    """Index of the usable discrete GPU with the most memory, or None."""
    from gpu_select import is_usable
    usable = [i for i in range(torch.cuda.device_count()) if is_usable(torch, torch.cuda.get_device_properties(i))]
    if not usable:
        return None
    return max(usable, key=lambda i: torch.cuda.get_device_properties(i).total_memory)


def load_chandra(device: str, gpu_reserve_gb: float, quantize: str = "none"):
    """Load Chandra OCR 2 from MODEL_DIR and wrap it in chandra's InferenceManager.

    On a GPU, as much of the model as fits (leaving `gpu_reserve_gb` free for
    the page being processed) goes in VRAM; any remainder stays in system RAM
    and is streamed to the GPU as needed. Slower than all-in-VRAM, but exact
    same accuracy, and it lets the model run on 12 GB cards.
    """
    import psutil
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    from transformers.utils import logging as hf_logging
    from chandra.model import InferenceManager

    # Hides the load report listing the checkpoint's unused multi-token-prediction
    # ("mtp.*") weights, which inference doesn't need.
    hf_logging.set_verbosity_error()
    from chandra.settings import settings

    idx = pick_gpu(torch) if device in ("cuda", "auto") and torch.cuda.is_available() else None
    use_gpu = idx is not None
    if device == "cuda" and not use_gpu:
        raise RuntimeError("--device cuda requested but PyTorch sees no supported GPU. Re-run setup.ps1, or use --device cpu.")

    kwargs = {}
    if use_gpu:
        torch.cuda.set_device(idx)
        props = torch.cuda.get_device_properties(idx)
        backend = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
        free_bytes, total_bytes = torch.cuda.mem_get_info(idx)
        weights_bytes = os.path.getsize(os.path.join(MODEL_DIR, "model.safetensors"))  # bf16 on disk = bf16 in VRAM
        budget = free_bytes - gpu_reserve_gb * GIB
        print(f"GPU: {props.name} ({backend}), {total_bytes / GIB:.1f} GB total, {free_bytes / GIB:.1f} GB free")

        kwargs["dtype"] = torch.bfloat16
        if quantize == "int8":
            from quantization import model_bytes, quantize_int8
            print("Quantizing language model to int8 (vision encoder stays bf16)...")
            model = AutoModelForImageTextToText.from_pretrained(MODEL_DIR, dtype=torch.bfloat16, device_map={"": "cpu"})
            n = quantize_int8(model)
            size = model_bytes(model)
            print(f"Quantized {n / 1e9:.2f}B params; model is now {size / GIB:.1f} GB.")
            if size > budget:
                raise RuntimeError(f"int8 model ({size / GIB:.1f} GB) does not fit in free VRAM "
                                   f"({budget / GIB:.1f} GB after reserve). Lower --gpu-reserve-gb or close other GPU apps.")
            model = model.to(f"cuda:{idx}")
            kwargs = None
        elif budget >= weights_bytes:
            kwargs["device_map"] = {"": idx}
            print(f"Model ({weights_bytes / GIB:.1f} GB) fits entirely in VRAM.")
        else:
            if budget < 2 * GIB:
                raise RuntimeError(f"Only {free_bytes / GIB:.1f} GB VRAM free; not enough to run on GPU. Close other GPU apps or use --device cpu.")
            kwargs["device_map"] = "auto"
            kwargs["max_memory"] = {idx: int(budget), "cpu": int(psutil.virtual_memory().available * 0.8)}
            print(f"Model is {weights_bytes / GIB:.1f} GB: {budget / GIB:.1f} GB in VRAM, "
                  f"{(weights_bytes - budget) / GIB:.1f} GB in system RAM (slower, same accuracy).")
        settings.TORCH_DEVICE = f"cuda:{idx}"  # where chandra sends the page inputs
    else:
        if quantize != "none":
            raise RuntimeError("--quantize int8 needs a GPU; on CPU use the default full-precision model.")
        print("Running on CPU. Expect several minutes per page; 32 GB+ of RAM recommended.")
        kwargs["dtype"] = torch.float32
        kwargs["device_map"] = {"": "cpu"}
        settings.TORCH_DEVICE = "cpu"

    if kwargs is not None:
        model = AutoModelForImageTextToText.from_pretrained(MODEL_DIR, **kwargs)
    model = model.eval()
    if use_gpu and getattr(torch.version, "hip", None):
        from model_patches import replace_patch_convs
        replace_patch_convs(model)  # MIOpen Conv3d fails on ROCm/Windows; exact-math equivalent
    processor = AutoProcessor.from_pretrained(MODEL_DIR)
    processor.tokenizer.padding_side = "left"
    model.processor = processor

    import repeat_guard
    repeat_guard.install()  # stop + retry pages that fall into a repetition loop

    manager = InferenceManager.__new__(InferenceManager)  # skip its built-in loader
    manager.method = "hf"
    manager.model = model
    return manager, use_gpu


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
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto",
                        help="auto = GPU if available, else CPU. 'cuda' means any GPU, NVIDIA or AMD (default: auto)")
    parser.add_argument("--gpu-reserve-gb", type=float, default=2.5,
                        help="VRAM kept free for page processing; the rest holds the model, overflow goes to "
                             "system RAM. Raise this if you hit out-of-memory errors (default: 2.5)")
    parser.add_argument("--quantize", choices=["none", "int8"], default="none",
                        help="none = full bf16 model (default). int8 = ~6 GB model that fits fully on 12 GB GPUs; "
                             "faster there, possibly slightly less accurate")
    parser.add_argument("--dpi", type=int, default=300,
                        help="Page render resolution sent to the model (default: 300)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip PDFs whose output file already exists")
    parser.add_argument("--rotate", action="append", default=[], metavar="[FILE:]PAGES=DEG",
                        help="Manually turn pages clockwise by 0/90/180/270 degrees before OCR. Overrides the "
                             "automatic orientation check for those pages (0 = leave as is). The output page is "
                             "turned too. PAGES is e.g. 2 or 1,3-5. Without FILE it applies to every PDF. "
                             'Repeatable. Example: --rotate "DC pages 1-8.pdf:2=180"')
    parser.add_argument("--no-auto-rotate", action="store_true",
                        help="Don't check page orientation automatically before OCR")
    parser.add_argument("--progress-json", action="store_true",
                        help="Also print machine-readable '@@{json}' progress lines (used by gui.py)")
    args = parser.parse_args()
    try:
        args.rotate = [parse_rotate_spec(s) for s in args.rotate]
    except ValueError as e:
        parser.error(str(e))
    return args


def parse_rotate_spec(spec: str):
    """'[FILE:]PAGES=DEG' -> (file name or None, set of 1-based pages, degrees)."""
    # rpartition: Windows file names can't contain ':', so the last ':' ends FILE.
    file_part, _, rest = spec.rpartition(":")
    pages_part, eq, deg_part = rest.partition("=")
    if not eq:
        raise ValueError(f"--rotate {spec!r}: expected [FILE:]PAGES=DEG, e.g. 2=180")
    try:
        deg = int(deg_part) % 360
    except ValueError:
        raise ValueError(f"--rotate {spec!r}: DEG must be 0, 90, 180 or 270")
    if deg not in (0, 90, 180, 270):
        raise ValueError(f"--rotate {spec!r}: DEG must be 0, 90, 180 or 270")
    pages = set()
    try:
        for item in pages_part.split(","):
            a, _, b = item.strip().partition("-")
            pages.update(range(int(a), int(b or a) + 1))
    except ValueError:
        raise ValueError(f"--rotate {spec!r}: PAGES must look like 2 or 1,3-5")
    return (file_part.strip() or None), pages, deg


def detect_orientations(pdf_files, input_dir: str) -> dict:
    """Pre-pass before OCR: rel_path -> {page: clockwise degrees} for pages not upright.

    Renders each page small (short side 1024 px, as displayed, like chandra's
    renderer) and runs the CPU orientation classifier (orientation.py).
    """
    from orientation import OrientationDetector, orientation_model_is_downloaded
    if not orientation_model_is_downloaded():
        print("Warning: orientation model not found, so pages won't be turned automatically. "
              "Run `python download_model.py` once while online to enable it.")
        return {}
    from PIL import Image
    detector = OrientationDetector()
    print("Checking page orientation...")
    start = time.perf_counter()
    found, n_pages = {}, 0
    for i, input_pdf in enumerate(pdf_files, 1):
        rel_path = os.path.relpath(input_pdf, input_dir)
        try:
            doc = pymupdf.open(input_pdf)
        except Exception:
            continue  # process_pdf reports unreadable PDFs
        turns = {}
        with doc:
            for pno, page in enumerate(doc):
                zoom = 1024 / max(1.0, min(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                correction = detector.correction_for(img)
                n_pages += 1
                if correction:
                    turns[pno + 1] = correction
        if turns:
            found[rel_path] = turns
            desc = ", ".join(f"page {p} by {d}°" for p, d in sorted(turns.items()))
            print(f"  PDF {i}: turning {desc}")
        emit("orientation", index=i, total=len(pdf_files), name=rel_path,
             turned={str(p): d for p, d in turns.items()})
    n_turned = sum(len(t) for t in found.values())
    print(f"  Checked {n_pages} page(s) in {time.perf_counter() - start:.1f}s; {n_turned} need turning.")
    emit("orientation_done", pages=n_pages, turned=n_turned)
    return found


def rotations_for(rotate_specs, rel_path: str) -> dict:
    """Page -> degrees for one PDF; FILE matches the file name or path relative to the input folder."""
    out = {}
    name = os.path.basename(rel_path).lower()
    rel = os.path.normcase(os.path.normpath(rel_path))
    for file_part, pages, deg in rotate_specs:
        if file_part is None or file_part.lower() == name or os.path.normcase(os.path.normpath(file_part)) == rel:
            out.update({p: deg for p in pages})
    return out


def fail(message: str) -> int:
    print(f"Error: {message}")
    emit("error", message=message)
    return 1


def main():
    global PROGRESS_JSON
    args = parse_args()
    PROGRESS_JSON = args.progress_json
    print("=== Chandra OCR 2 PDF Annotator (offline) ===")

    input_dir = args.input_dir or input("Enter the input directory containing PDF files: ").strip().strip('"')
    if not os.path.isdir(input_dir):
        return fail(f"input folder '{input_dir}' does not exist.")
    output_dir = args.output_dir or input("Enter the output directory for processed PDFs: ").strip().strip('"')
    if os.path.normcase(os.path.abspath(output_dir)) == os.path.normcase(os.path.abspath(input_dir)):
        # Outputs keep the input file names, so this would overwrite the original notebooks.
        return fail("the output folder must be different from the input folder.")

    if not model_is_downloaded():
        print("Run `python download_model.py` once while connected to the internet.")
        return fail(f"Chandra OCR 2 model not found in {MODEL_DIR}. Re-run setup while online.")

    pdf_files = find_pdfs(input_dir, output_dir)
    if not pdf_files:
        print(f"No PDF files found in '{input_dir}'.")
        emit("found", pdfs=0)
        emit("done", pdfs=0, failed_pages=0)
        return 0
    print(f"Found {len(pdf_files)} PDF(s) to process.")
    emit("found", pdfs=len(pdf_files))

    for file_part, _, _ in args.rotate:
        if file_part and not any(rotations_for([(file_part, {1}, 90)], os.path.relpath(p, input_dir))
                                 for p in pdf_files):
            print(f"Warning: --rotate names '{file_part}', which isn't among the input PDFs")

    # Orientation pre-pass: every page is checked before the OCR model is even loaded.
    auto_turns = {} if args.no_auto_rotate else detect_orientations(pdf_files, input_dir)

    try:
        import chandra  # noqa: F401
    except ImportError:
        return fail("chandra-ocr is not installed. Run the installer (or setup.ps1) first.")

    import logging
    # Harmless kwargs warning, see https://github.com/vllm-project/vllm/issues/39768
    logging.getLogger("transformers.processing_utils").setLevel(logging.ERROR)

    if args.device != "cpu":
        from gpu_select import restrict_to_usable_gpus
        restrict_to_usable_gpus()  # must happen before torch is imported

    print("Loading Chandra OCR 2 model... (this may take a moment)")
    emit("loading_model")
    try:
        manager, _ = load_chandra(args.device, args.gpu_reserve_gb, args.quantize)
    except Exception as e:
        return fail(f"Failed to load model: {e}")
    emit("model_loaded")

    failed_pages = 0
    for index, input_pdf in enumerate(pdf_files, 1):
        rel_path = os.path.relpath(input_pdf, input_dir)
        output_pdf = os.path.join(output_dir, rel_path)
        if args.skip_existing and os.path.exists(output_pdf):
            print(f"Skipping {input_pdf} (output exists)")
            emit("pdf_skipped", index=index, total=len(pdf_files), name=rel_path)
            continue
        cache_dir = os.path.join(output_dir, CACHE_DIR_NAME, rel_path)
        rotations = dict(auto_turns.get(rel_path, {}))
        rotations.update(rotations_for(args.rotate, rel_path))  # manual --rotate wins
        failed = process_pdf(input_pdf, output_pdf, cache_dir, manager, args.dpi, args.quantize,
                             {p: d for p, d in rotations.items() if d}, index, len(pdf_files), rel_path)
        failed_pages += max(0, failed)

    print("Done!")
    emit("done", pdfs=len(pdf_files), failed_pages=failed_pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
