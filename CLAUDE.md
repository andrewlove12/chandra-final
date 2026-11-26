# CLAUDE.md: chandra-final

Handoff notes for Claude sessions working on this repo. Last updated 2026-11-25.

## What this project is

The program takes a folder of scanned **lab-notebook PDFs** (very messy handwriting) and runs local OCR on them. For each one it writes a copy with the transcription overlaid in **red** on the original page, positioned using the OCR model's layout bounding boxes.

- OCR model: **Chandra OCR 2** (`datalab-to/chandra-ocr-2`, about 5.2B params, Qwen3.5 architecture with mixed linear and full attention), run locally through the `chandra-ocr` Python package (0.3.x) using its Hugging Face backend.
- PDF rendering and overlay: PyMuPDF (`pymupdf`) and BeautifulSoup. Chandra returns HTML per layout block.
- Repo: `https://github.com/andrewlove12/chandra-final`, branch `main`. Local clone at `F:\chandra-final` on **WHITEKNIGHT**. Git Credential Manager is signed in.
- **Only commit or push when the user says so.** End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## HARD RULES: privacy

1. **The notebooks must never leave the machine.** The only allowed internet use is the one-time setup: pip packages, PyTorch wheels and the model downloads (Chandra + the 7 MB orientation model). Processing must be fully offline.
2. **Claude must not read notebook content.** That covers the input PDFs, the output PDFs, `.ocr_cache/*.json` (it holds the transcribed text), and `comparison_report.html`. Anything Claude reads is sent to Anthropic's API, which would defeat the purpose. Work only with numeric summaries:
   - `compare_runs.py` prints only numbers, and labels PDFs as "PDF 1", "PDF 2" rather than by file name.
   - `process_pdfs.py` prints only progress and timings.
   - The user opens the HTML diff report themselves.
3. For testing, use the **public** sample `models/chandra-ocr-2/handwritten_form.png`. It's already converted to `F:\chandra-test\in\sample_form.pdf`.

## Files

| File | Purpose |
|---|---|
| `setup.ps1` | Run by the installer (online; safe to re-run, only does work when something changed). `-Backend cuda\|rocm\|cpu` (the installer passes one; `auto` = previous choice, else detect). **Always uses a private Python:** downloads pinned uv 0.13.9 (SHA-256 checked) into `tools\uv`, which installs the exact pinned Python (3.12.16 for ROCm, 3.13.18 for CUDA/CPU) into `python\` (`UV_PYTHON_INSTALL_DIR`, only-managed). Rebuilds `.venv` if it isn't based on that Python/version or the backend changed. When its output is redirected it prints `[setup pid N]` and uses `pip --progress-bar raw`; every program runs through `Invoke-Logged` (output via PowerShell so it reaches setup.log; raw progress lines bypass the log). Installs torch from the backend's index (`cpu` = standard PyPI torch), then `requirements.txt`, then `compileall -l` on the app's .py files (the app can't write `__pycache__` in Program Files), then runs `download_model.py`. State in `.venv\setup-state.txt`. Logs to `logs\setup.log`; exits 0/1. Never uses `py` or a system Python. |
| `download_model.py` | Downloads the pinned Chandra revision into `models/chandra-ocr-2/` and the pinned orientation classifier into `models/doc-orientation/`, each with a `REVISION` marker. The only Python code that touches the network. |
| `model_config.py` | `MODEL_REPO`, the pinned `MODEL_REVISION` (`af93b47d…`), `MODEL_DIR`, and `model_is_downloaded()`. |
| `process_pdfs.py` | Main program. Forces HF offline mode, **monkeypatches `socket` to block all non-loopback connections**, runs the orientation pre-pass, loads the model from the local folder, OCRs page by page, caches results, draws the red overlay. |
| `orientation.py` | Page-orientation pre-pass. PaddleOCR's `PP-LCNet_x1_0_doc_ori` ONNX (Apache-2.0, 7 MB, pinned `7330ab70…`) on CPU via onnxruntime. Classifies each page in all 4 turns and combines the votes; acts only at ≥ 0.80 confidence. Label N = content turned N° clockwise; the model output is already softmaxed. |
| `repeat_guard.py` | Replaces chandra's HF `generate_hf` (via `chandra.model.generate_hf = ...`). Checks every 64 tokens for (a) an exact periodic tail ≥ 3000 chars or (b) < 50% distinct 4-word sequences in the last 300 words (tags stripped). Stops early, retries with sampling (T=0.2, 0.4, top_p 0.95), else keeps the last attempt and flags an error. Prints a numeric `[guard]` line on loops. |
| `gpu_select.py` | Runs a separate process to list the GPUs, then sets `HIP_VISIBLE_DEVICES` / `CUDA_VISIBLE_DEVICES` **before torch is imported** so unsupported GPUs are hidden. `python gpu_select.py` prints what it found. |
| `model_patches.py` | `PatchLinear`: swaps the vision patch-embed `Conv3d` (kernel == stride) for an exactly equivalent matmul. Applied only on ROCm. |
| `quantization.py` | Optional int8 weight-only quantization written in plain PyTorch (per-channel symmetric). Quantizes only `model.language_model.*` Linear layers (3.57B params), skips `in_proj_a`/`in_proj_b`. Model goes from 9.9 GB to 5.1 GB. |
| `run_log.py` | Troubleshooting logs. `run_log.start(kind)` tees stdout/stderr into `logs\<kind>-YYYYMMDD-HHMMSS.log` (`run` from `process_pdfs.main()`, `gui` from `gui.main()`), with a header (Windows, CPU, Python, command), `[HH:MM:SS]` per line, `\r` progress bars collapsed to their final state, `@@` GUI events left out, and `faulthandler` for native crashes. Keeps the last 30 per kind. Falls back to `%LOCALAPPDATA%\ChandraNotebookOCR\logs` if `logs\` isn't writable. Logs hold paths and file names but never transcribed text (process_pdfs prints none). |
| `gui.py` | PySide6 desktop app. Runs `process_pdfs.py --progress-json` as a subprocess (offline guard stays in the engine), parses `@@{json}` event lines (found, orientation, loading_model, pdf_start, page_start, page_done, page_error, pdf_done, done, error), shows overall/per-PDF progress, ETA, a "Pages to check" list, the log, and an **Open logs folder** button. Cancel = `taskkill /T /F` on the worker. Settings in QSettings (`HKCU\Software\ChandraNotebookOCR`). Refuses output == input. |
| `installer/chandra-ocr.iss` | Inno Setup 6 script → `dist\ChandraNotebookOCR-Setup-<ver>.exe`. **Versioning: bump the patch number of `#define AppVersion` (1.0.x) for every release build handed to the user** (they upload each to GitHub); current **1.0.2** (first versioned build: hidden setup with output in the wizard, Cancel). Earlier builds were all 1.0.0. **Runs entirely as admin** (`PrivilegesRequired=admin`), default dir `{autopf}` (Program Files); the post-install app launch uses `runasoriginaluser`. Graphics card page (NVIDIA / AMD / Neither, detected one pre-selected, warns on mismatch; silent: `/GPU=nvidia|amd|none`). Bundles **both** VC++ runtimes and always runs them silently (`_1` 2015 14.0.23026, then `_2` v14 14.51.36247); exit 0/1638 = OK, 3010/1641 = restart prompt via `NeedRestart`. Runs `setup.ps1 -Backend cuda|rocm|cpu` **hidden** via `ExecAndLogOutput`; output streams into the Installing page: latest line in `FilenameLabel`, all lines in a scrolling `TNewMemo` under the bar; pip `Progress X of Y` lines drive the bar as a % instead. **Cancel** works during this step (setup.ps1 prints `[setup pid N]`; Cancel → confirm → `taskkill /T /F`). Retry prompt on failure (not after cancel). Start menu **and desktop** shortcuts (always created, all users) run `.venv\Scripts\pythonw.exe gui.py` with `app.ico` and AppUserModelID `ChandraNotebookOCR.App` (`gui.py` sets the same ID so the taskbar shows the app icon, not Python's). Deletes a leftover `run.ps1` on upgrade. `[Dirs] {app}\logs` gets `users-modify` (Program Files is read-only for the non-admin app; verified with `runas /trustlevel:0x20000`). Uninstall deletes `.venv`, `python`, `tools`, `models`, `logs`. Build with `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` (installed per-user via winget). |
| `installer/app.ico`, `installer/installer.ico`, `installer/make_icon.py` | App icon (shortcuts, window, uninstall entry) and Setup.exe icon (same notebook + green download badge), and the script that draws them; each size 16–256 px is drawn separately so small sizes stay sharp. |
| `compare_runs.py` | Compares two output folders' `.ocr_cache`. Prints character and word similarity, changed/inserted/deleted word counts, block counts, box IoU and timings. Writes the HTML diff to `<run_b>/comparison_report.html`. |
| `requirements.txt` | Direct dependencies only. **torch and torchvision are deliberately left out**; `setup.ps1` installs them from the right index. Includes `onnxruntime` for the orientation model. |
| `vc_redist.x64_1.exe`, `vc_redist.x64_2.exe` | MSVC runtimes needed by DLLs in pymupdf/torch/onnxruntime; both bundled in the installer. Both are Microsoft-signed. `_2` was corrupted in commit 224b073 and restored from history in 70aa725. |
| `.gitignore` | Excludes `*.pdf`, `*.png`/`*.jpg` (screenshots; one was once committed by accident and erased from history), `.ocr_cache/`, `*_page_*.json`, `models/`, `.venv/` so notebooks can't be committed. `.gitattributes` marks `*.exe` as binary. |

`process_pdfs.py` options: `INPUT_DIR OUTPUT_DIR` (prompts for them if missing), `--device auto|cuda|cpu` (`cuda` means any GPU, AMD included), `--gpu-reserve-gb 2.5`, `--quantize none|int8`, `--dpi 300`, `--skip-existing`, `--rotate [FILE:]PAGES=DEG` (repeatable; overrides auto, `=0` means leave alone), `--no-auto-rotate`, `--progress-json` (GUI events). Refuses output folder == input folder; a PDF whose pages all failed is not saved.

Order of work in `main()`: start `run_log` → find PDFs → orientation pre-pass (before Chandra loads) → `restrict_to_usable_gpus()` → load model (installs `repeat_guard`) → per page: cache or OCR → overlay.

OCR cache: `<output>/.ocr_cache/<relative pdf path>/page_NNNN.json`. Each entry records the model revision, DPI, quantization, rotation, image size, seconds, token count, attempts, looped, error and chunks. The cache is ignored if revision, DPI, quantization or rotation change, or if the entry hit the 12,384-token cap.

Overlay (`_fit_text`): text anchored at the block's top-left, size `0.6 × box height / lines` capped to 6–14 pt "letter-equivalent" (limits scale down on pages narrower than 612 pt; wider pages keep them). If it doesn't fit at the minimum, the box grows down → right to page edge → up → left. `_insert_box` maps visual coords through `page.derotation_matrix` and passes `rotate=page.rotation`, so overlays work on rotated pages. Each page prints `Overlay: N words, M spill past their box`.

## History

**Commit `70aa725`:** the move to pinned Chandra OCR 2 with enforced offline processing (offline guard and socket block, DPI fix, page-at-a-time rendering, cache in output folder, CLI args, trimmed requirements, restored redistributable, tightened `.gitignore`).

**Commit after that (2026-11-25):** GPU support (AMD ROCm + NVIDIA), int8 option, comparison tool, loop guard, automatic page orientation, `--rotate`, readable overlay sizing, README/CLAUDE.md updates (`ee5c393`).

**Next commit (2026-11-25):** PySide6 GUI, Inno Setup installer (admin, graphics card page, bundled VC++ runtimes), private pinned Python via uv, `run.ps1` removed.

## Hardware

- **WHITEKNIGHT (test machine):** AMD Radeon RX 7600 XT 16 GB (gfx1102), plus the Ryzen's integrated "AMD Radeon(TM) Graphics" (gfx1036, which reports 36 GB of shared memory). 62 GB RAM.
  - Python: 3.14 through `py`; 3.12.16 through uv/Astral.
  - The `.venv` is **Python 3.12.16, torch 2.11.0+rocm7.13.0** from `https://repo.amd.com/rocm/whl/gfx110X-all/`, transformers 5.24.1.
  - The model is downloaded (9.88 GB).
- **Note:** `run.ps1` was removed (2026-11-25): everything goes through the installer and `gui.py`. Developers run `.venv\Scripts\python.exe process_pdfs.py ...` directly.
- **Production machine:** NVIDIA RTX 5070 12 GB (Blackwell, sm_120), 32 GB RAM. `setup.ps1` installs torch 2.14.x from `https://download.pytorch.org/whl/cu130`, which needs driver 580 or newer. **This path is untested.**
- AMD Windows wheels go up to Python 3.13 only (no 3.14). NVIDIA cu130 has cp314.

## AMD ROCm on Windows: problems found and fixed

1. **The integrated GPU breaks everything.** If gfx1036 is visible, kernels fail on *every* device with `hipErrorInvalidImage`. Fix: `gpu_select.restrict_to_usable_gpus()` hides GPUs that are integrated or whose architecture isn't in `torch.cuda.get_arch_list()`. Verified working.
2. **The MIOpen Conv3d** in the vision patch embed fails with `hipErrorInvalidValue`. Fix: `model_patches.PatchLinear`, verified to match within 4e-6 in fp32. Disabling cudnn/MIOpen instead was far too slow.
3. **Attention (SDPA):** without a flag, flash and memory-efficient attention return "No available kernel" on gfx1102. The math backend needs about 36 GB for about 24.5k vision patches, and the full run crashed with an access violation inside `sdpa_attention_forward`. Fix: set `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` at the top of `process_pdfs.py`. Verified in isolation: flash and efficient attention each take 0.33 s / 0.1 GB at n=24576.
4. **torchao** can't be imported on ROCm Windows because torch was built without `torch.distributed`. That's why int8 is hand-written. bitsandbytes doesn't support ROCm on Windows.
5. **Test scripts must call `gpu_select.restrict_to_usable_gpus()` themselves** before importing torch. `main()` does it, but importing `process_pdfs` doesn't, and without it model loading fails with `hipErrorInvalidImage` (problem 1).

## Test results (2026-11-24/25, RX 7600 XT)

- **Public sample (1 page):** BF16 327 s / 2,098 tokens; int8 607 s / 2,308 tokens; 99.07% word similarity, box IoU 0.98.
- **User's `DC pages 1-8.pdf` (8 pages):** BF16 ~180–260 s per page (about 6–9 tokens/s). int8 was 1.6–1.8× slower and dropped ~15% of words on page 3; 4 of 8 pages identical. **The user chose BF16.** int8 may still matter on the 12 GB RTX 5070.
- Page 2 of the DC PDF was scanned **upside down**: unrotated it looped on every attempt (both BF16 and int8). Rotated 180° it finished first try (233 words, 19 blocks). The orientation pre-pass now detects it (and correctly leaves the other 7 pages alone; a single-pass classifier would have wrongly flipped page 6).
- The DC PDF embeds scans in odd page sizes (202–631 pt wide). Native scan resolution (979–3,071 px) and chandra's ~6.3 MP input cap limit detail, not `--dpi`.
- Speed: a 12,384-token loop costs ~23 min; the guard now catches loops at ~1.5–3k tokens.

## Working in this repo: lessons

- **Stopping a run:** kill the whole tree (`taskkill /PID <root> /T /F`). The venv `python.exe` is a launcher that spawns the real interpreter; killing processes one by one left an orphan that kept OCRing and let the chained script start the next step, so two runs shared the GPU for ~50 min.
- **Watching logs:** when two runs' logs are tailed together, show file headers; a merged stream led to misattributing an event once.
- `PowerShell ... | Tee-Object` captures the Python output only; an `"EXIT $LASTEXITCODE"` echoed afterwards isn't in that log.
- **Windows PowerShell 5.1 launched by a non-PowerShell parent (Setup.exe, cmd) that was itself started from pwsh 7 inherits pwsh 7's `PSModulePath`** and loses script-defined cmdlets (`Get-FileHash`, `Expand-Archive`). pwsh only cleans the path for its *direct* children. `setup.ps1` strips those entries under the Desktop edition and uses .NET for hashing/unzipping.
- **Inno Setup:** `/SUPPRESSMSGBOXES` only affects `SuppressibleMsgBox`; plain `MsgBox` blocks silent installs. Lines in `[Code]` that start with `#` (e.g. `#13#10`), even indented, are read as preprocessor directives: keep `#13#10` at the end of the previous line.
- **Qt:** worker-thread signals must go to bound methods of a GUI-thread QObject (queued). Connecting a plain function runs it on the worker thread and crashes when it touches widgets.
- **Inno `Exec`/`ExecAndLogOutput` disable the wizard window while waiting** (messages are still pumped, so the UI updates). Re-enable it with user32 `EnableWindow` from the output callback to make Cancel clickable. Its output callback is live (line by line, stderr too).
- **Windows PowerShell 5.1 with redirected stdout:** a native program called directly writes straight to the pipe and **skips `Start-Transcript`**. Pipe it through PowerShell (`& exe 2>&1 | ForEach-Object { Write-Host ... }`, with `$ErrorActionPreference = 'Continue'` so stderr lines don't throw). A function with a `param()` block would swallow `-m`; use `$args`.
- **Testing UI:** Inno's `TNewButton` shows up in UI Automation as a Pane without Invoke; click via Win32 `BM_CLICK`. **Screenshots: capture only the target window with `PrintWindow`**, never `CopyFromScreen` (an occluded installer window once captured another of the user's windows instead).
- **uv picks up the PC's own settings** (`UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR`, `UV_CACHE_DIR`, uv.toml). WHITEKNIGHT has `UV_*` vars pointing at `E:\local`; a test install tried to put `python3.12.exe` there and `uv python install` overwrote `HKCU\Software\Python\Astral\CPython3.12.16` (restored by hand to `E:\local\python\cpython-3.12.16-windows-x86_64-none`). `setup.ps1` now clears `UV_*`, sets `UV_NO_CONFIG=1` and uses `--no-bin --no-registry`. Also `uv venv --managed-python` errors if `UV_PYTHON_PREFERENCE` is set.
- Numeric-only helper scripts used for analysis (repeat stats, overlay word counts, orientation checks) were kept in the session scratchpad, not the repo.

## Current status and next steps

1. **Pipeline works end to end on BF16** (AMD RX 7600 XT, ROCm). Loop guard, orientation pre-pass and overlay sizing are verified on the public sample and on the user's 8-page PDF.
2. **Installer and GUI: built (2026-11-25).** `gui.py` verified end to end (cached and real OCR, cancel leaves no processes). Installer (admin, both VC++ runtimes bundled) verified with silent test installs into `F:\chandra-test\install-admin` (`/DIR=`, `/GPU=amd`; private Python 3.12.16 + torch 2.11.0+rocm7.13.0, GPU detected; models pre-copied to skip 10 GB) and a clean uninstall. Not yet tested: the interactive wizard pages by hand, the NVIDIA path, a clean PC that actually needs the VC runtimes (here both returned 1638 = already present), running the app from Program Files as a non-admin user. Possible later: code signing (SmartScreen will warn about an unsigned Setup.exe), versioning/release process (GitHub Releases), app-level update check (must not break offline rule: no network from the app).
3. **Test on the RTX 5070** (untested NVIDIA path). With bf16 and `--gpu-reserve-gb 2.5`, about 1.5–2 GB of the model is offloaded to system RAM via accelerate `device_map="auto"`; int8 (5.1 GB) fits entirely. Compare speed there before deciding.
4. Possible later: A/B test another local OCR model (olmOCR-2-7B is too big for 12 GB without quantization; PaddleOCR-VL-1.6 or GLM-OCR are ~0.9B and fast). Chandra OCR 2 was the strongest option found for handwriting as of 2026-11.

## GUI and installer: original requirements (now implemented, see Files)

The user wanted:
- **A standalone installer executable.** It asks where to install, then fully sets up that folder: downloads a portable Python of the right version (e.g. python-build-standalone or the embeddable distribution, matched to the GPU backend: ≤3.13 for AMD), creates the venv, installs the right torch build, and downloads the models. Essentially `setup.ps1` with a UI.
- **A separate UI app** to run everything: pick input and output folders, choose options (device, quantization, auto-rotate), start and cancel, a progress/status bar per PDF and page, and a log view. It must stay offline and keep the socket block.
- Likely approach: Python with Tkinter or PySide6, packaged with PyInstaller. The installer could also be a small PowerShell or Inno Setup wrapper.
