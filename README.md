Start by downloading this repo and place it either in your Downloads folder or user folder and extract the zip.


<img width="1542" height="469" alt="image" src="https://github.com/user-attachments/assets/cb47dbd1-4956-465d-962c-955e215b5aab" />





Run both vc_redist.x64.exe first. Python changed how it looks for DLLs on the system and this adds/fixes the DLLs needed to run the pymupdf module. Without it, the program will error out at the beginning and again after inputting the input and output directories if only the 1st is run.

<img width="1106" height="387" alt="image" src="https://github.com/user-attachments/assets/329d3638-5b0a-4888-bf14-a237e6c1d8db" />


<img width="1114" height="257" alt="image" src="https://github.com/user-attachments/assets/ac06336e-f978-4206-9528-190290169be8" />





You need Python installed. You can check it by opening a blank Powershell window and running the command " python " to start python and print out the version. `setup.ps1` picks a compatible version for the virtual environment on its own (through the `py` launcher, or `uv` if needed): NVIDIA and CPU setups can use 3.14, but AMD GPUs need Python 3.12 or 3.13 because AMD has no 3.14 build of PyTorch yet.

<img width="890" height="85" alt="image" src="https://github.com/user-attachments/assets/32ac78ce-8eae-47cb-bdd2-954bf52299a8" />





Assuming you have admin privileges, you'll need to open a Powershell window as admin, run Set-ExecutionPolicy Bypass then run the run.ps1 file. The run.ps1 doesn't need to be run as admin, everything works in userland going forward, admin is just needed to change the execution policy.

<img width="951" height="135" alt="image" src="https://github.com/user-attachments/assets/42f7af01-e7f8-42a6-847d-b7c5e54e339b" />





If you don't, open run.ps1 in a text editor and copy the commands and run them manually in a normal Powershell window.


## What run.ps1 does

The first run needs internet. `run.ps1` calls `setup.ps1`, which:

1. Detects your graphics card and installs the matching version of PyTorch:
   - **NVIDIA:** the CUDA 13.0 build. RTX 50-series cards need NVIDIA driver 580 or newer.
   - **AMD Radeon RX 7000 / 9000:** AMD's ROCm build for Windows. This needs Python 3.12 or 3.13 (AMD has no 3.14 build yet) and a recent Adrenalin driver.
   - **Anything else:** the CPU-only build.
2. Creates a `.venv` folder with a suitable Python version and installs the other packages.
3. Downloads the Chandra OCR 2 model (about 10 GB) and a small page-orientation model (7 MB) into `models\`.

Only public packages and model files are downloaded. No notebook data is involved. To force a particular build, run `.\setup.ps1 -Backend cpu` (or `cuda` / `rocm`) once. The choice is remembered for later runs.

Every run after that works with no internet. `process_pdfs.py` loads the model from `models\` only, turns on Hugging Face offline mode and blocks all outbound network connections while it runs. Notebook contents can't leave the machine.

You can pass the folders directly instead of typing them at the prompts:

```
.\run.ps1 C:\path\to\notebooks C:\path\to\output
.\run.ps1 C:\path\to\notebooks C:\path\to\output --skip-existing
.\run.ps1 C:\path\to\notebooks C:\path\to\output --device cpu    # ignore the GPU
.\run.ps1 C:\path\to\notebooks C:\path\to\output --gpu-reserve-gb 4
.\run.ps1 C:\path\to\notebooks C:\path\to\output --rotate "notebook 3.pdf:5=90"
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

`python gpu_select.py` lists the GPUs PyTorch can see and which ones the program will use. Integrated graphics and GPUs that PyTorch's build doesn't support are hidden automatically; on AMD, leaving the integrated GPU visible makes every GPU fail.

### GPU memory

The model needs about 10.6 GB. The program uses the GPU with the most memory and keeps 2.5 GB of VRAM free (`--gpu-reserve-gb`) for working on the current page.

- **16 GB cards** (e.g. RX 7600 XT): the whole model fits in VRAM.
- **12 GB cards** (e.g. RTX 5070): most of the model goes in VRAM and the rest stays in system RAM, sent to the GPU as needed. Accuracy is the same. It's slower than having everything in VRAM, but much faster than running on CPU.

If a page fails with "out of memory", run again with a larger `--gpu-reserve-gb` (e.g. 4). More of the model then stays in system RAM. Pages that already finished are cached and won't be redone.

OCR results are cached in `<output folder>\.ocr_cache\`, so a rerun only redraws the overlay and doesn't OCR the pages again. Delete that folder to force a fresh OCR. The cache holds the transcribed notebook text, so treat it as being as sensitive as the notebooks.

Without a supported GPU the model runs on CPU. That's slow (minutes per page) and needs a lot of RAM (32 GB+ recommended).


Submit any issues to the Issues tracker on this Github repo so I can keep track of any errors you're getting and you can upload logs and files as needed. I'm also taking this route so I can make updates to the program as needed and it's easier for you to grab a copy of it.
