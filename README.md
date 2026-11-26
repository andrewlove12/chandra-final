# Chandra Notebook OCR

Reads scanned, handwritten lab-notebook PDFs on your own PC and saves a copy of each with the transcription overlaid in red. Everything runs offline; the notebooks never leave the computer.

## Installing and using it

1. Run `ChandraNotebookOCR-Setup-<version>.exe` (built as described at the end of this section). It runs as administrator (Windows asks for permission once) and installs into `C:\Program Files\Chandra Notebook OCR` by default.
2. When asked, say whether you have an **NVIDIA** or **AMD** graphics card (the one it finds is pre-selected). It installs the matching PyTorch; choose **Neither** if you have no graphics card or aren't sure, which installs the standard PyTorch that runs on the processor (much slower).
3. Setup then downloads a private copy of Python, PyTorch and the OCR models (about 15 GB). The installer shows the download progress and everything setup is doing in its own window, and **Cancel** stops it (run the installer again later to finish). This takes a while and needs internet only this once. It doesn't touch any Python already on the PC, and uninstalling removes everything it downloaded.
4. Open **Chandra Notebook OCR** from the Start menu or the desktop shortcut (both are created for all users): pick the folder with your notebook PDFs and a different folder for the results, and press Start. It shows progress per notebook and page, lists pages worth double-checking, and can be cancelled at any time (pages already read are kept, so the next run continues where it left off). It runs fully offline.

If setup fails (for example the internet dropped), the error says so and the log is in the install folder under `logs\setup.log`; running the installer again continues where it stopped.

### Log files (for reporting problems)

Every time the app or the OCR program runs, it writes a log file into the `logs` folder in the install folder (`C:\Program Files\Chandra Notebook OCR\logs`); the app's **Open logs folder** button opens it. If something isn't working, attach the newest files to an issue:

- `run-<date>-<time>.log`: one per OCR run (also from the command line). Settings, GPU and memory, each notebook and page, timings, warnings and errors, with the time on every line.
- `gui-<date>-<time>.log`: one per app start; errors in the app itself.
- `setup.log`: the installer's download and setup steps.

The logs contain folder paths and notebook file names but **never the transcribed text**. The last 30 of each kind are kept. If the install folder isn't writable, logs go to `%LOCALAPPDATA%\ChandraNotebookOCR\logs` instead.

To build the installer: install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then run `ISCC.exe installer\chandra-ocr.iss`. The result is `dist\ChandraNotebookOCR-Setup-<version>.exe`; the version is `AppVersion` at the top of the script, raised for every release (1.0.2, 1.0.3, ...) so each build can be told apart and uploaded to GitHub Releases. Silent installs accept `/GPU=nvidia`, `/GPU=amd` or `/GPU=none`.

## What setup installs

Everything except the VC++ runtimes goes into the install folder:

1. **Python:** the project's own copy of Python, at the exact version it was tested with (3.12.16 for AMD, 3.13.18 otherwise), downloaded by a pinned, checksum-verified copy of [uv](https://github.com/astral-sh/uv). Any Python already installed on the PC is never used or touched.
2. **PyTorch** for the graphics card you picked:
   - **NVIDIA:** the CUDA 13.0 build. RTX 50-series cards need NVIDIA driver 580 or newer.
   - **AMD Radeon RX 7000 / 9000:** AMD's ROCm build for Windows, with a recent Adrenalin driver.
   - **Neither:** the standard PyTorch package, which runs on the processor.
3. The other packages, and the **models**: Chandra OCR 2 (about 10 GB) and a small page-orientation model (7 MB).
4. The **Microsoft Visual C++ runtimes** (2015 and the current v14 build), which are bundled in the installer and needed by DLLs in PyMuPDF, PyTorch and onnxruntime. If a newer runtime is already installed it's left as it is. These are the only things installed outside the install folder.

Only public packages and model files are downloaded. No notebook data is involved. After setup the app works with no internet: the OCR engine loads the models from the install folder only, turns on Hugging Face offline mode and blocks all outbound network connections while it runs. Notebook contents can't leave the machine.

To change the graphics card choice later, run the installer again and pick a different answer.

## Command line (advanced)

The app is a front end for `process_pdfs.py`, which can also be run directly with the installed Python:

```
cd "C:\Program Files\Chandra Notebook OCR"
.venv\Scripts\python.exe process_pdfs.py C:\path\to\notebooks C:\path\to\output
.venv\Scripts\python.exe process_pdfs.py C:\path\to\notebooks C:\path\to\output --skip-existing
.venv\Scripts\python.exe process_pdfs.py C:\path\to\notebooks C:\path\to\output --rotate "notebook 3.pdf:5=90"
```

### All options

| Option | What it does |
|---|---|
| `--device auto\|cuda\|cpu` | `auto` (default) uses the GPU if there is one. `cuda` means any GPU, NVIDIA or AMD. |
| `--gpu-reserve-gb 2.5` | VRAM kept free for the page being processed. Raise it if you get "out of memory". |
| `--quantize none\|int8` | `none` (default) is the full model. `int8` is a ~5 GB version that fits fully on 12 GB cards. In our tests it was **less accurate** (it dropped about 15% of the words on one page) and, on a 16 GB card, about 1.8x slower, so only use it if the full model is too slow on your GPU. |
| `--dpi 300` | Resolution pages are rendered at. Rarely worth changing: what limits detail is the scan's own resolution, and the model caps its input at about 6.3 megapixels anyway. |
| `--rotate [FILE:]PAGES=DEG` | Turn pages clockwise (0/90/180/270) before OCR, overriding the automatic check. `PAGES` is like `2` or `1,3-5`; without `FILE:` it applies to every PDF. Can be given several times. |
| `--no-auto-rotate` | Skip the automatic orientation check. |
| `--skip-existing` | Skip PDFs whose output file already exists. |

### Pages scanned sideways or upside down

Before OCR starts, every page goes through a small (7 MB) orientation classifier that runs on the CPU, about 0.2 s per page. Pages that are sideways or upside down are turned before the OCR model sees them, and the output page is turned the same way so it, and the red text, display upright. Only confident results are acted on; use `--rotate` to fix a page it misses. (Upside-down pages matter: the OCR model tends to get stuck repeating itself on them.)

### If the model gets stuck repeating itself

On some pages the model falls into a loop, writing the same text over and over until it hits its length limit (20+ minutes for one page). The program watches for this, stops the loop early, and retries the page up to two more times with a little randomness, which usually gets it unstuck. If every attempt loops, it keeps the last one and prints a warning for that page. Upside-down pages are the most common cause, which the orientation check now prevents.

### The red overlay

Text is placed where the model found it on the page, as large as fits, but never below a readable minimum (6 pt on a letter-sized page, scaled down for smaller pages). If there's more text than fits in that spot, it spills past the box (downward first) rather than shrinking out of sight. Each page prints how many words were drawn and how many spilled.

### Comparing two runs

`compare_runs.py` compares two output folders (for example a full-model run and an int8 run) and prints only numbers: character and word similarity per page, changed/added/missing words, layout differences, and timings. It also writes `comparison_report.html` into the second folder with a side-by-side diff. That report contains notebook text, so keep it on this machine.

```
.venv\Scripts\python.exe compare_runs.py C:\output_full C:\output_int8
```

### Which GPU is used

`.venv\Scripts\python.exe gpu_select.py` (in the install folder) lists the GPUs PyTorch can see and which ones the program will use. Integrated graphics and GPUs that PyTorch's build doesn't support are hidden automatically; on AMD, leaving the integrated GPU visible makes every GPU fail.

### GPU memory

The model needs about 10.6 GB. The program uses the GPU with the most memory and keeps 2.5 GB of VRAM free (`--gpu-reserve-gb`) for working on the current page.

- **16 GB cards** (e.g. RX 7600 XT): the whole model fits in VRAM.
- **12 GB cards** (e.g. RTX 5070): most of the model goes in VRAM and the rest stays in system RAM, sent to the GPU as needed. Accuracy is the same. It's slower than having everything in VRAM, but much faster than running on CPU.

If a page fails with "out of memory", run again with a larger `--gpu-reserve-gb` (e.g. 4). More of the model then stays in system RAM. Pages that already finished are cached and won't be redone.

OCR results are cached in `<output folder>\.ocr_cache\`, so a rerun only redraws the overlay and doesn't OCR the pages again. Delete that folder to force a fresh OCR. The cache holds the transcribed notebook text, so treat it as being as sensitive as the notebooks.

Without a supported GPU the model runs on CPU. That's slow (minutes per page) and needs a lot of RAM (32 GB+ recommended).


Submit any issues to the Issues tracker on this Github repo so I can keep track of any errors you're getting and you can upload logs and files as needed. I'm also taking this route so I can make updates to the program as needed and it's easier for you to grab a copy of it.
