Start by downloading this repo and place it either in your Downloads folder or user folder and extract the zip.


<img width="1542" height="469" alt="image" src="https://github.com/user-attachments/assets/cb47dbd1-4956-465d-962c-955e215b5aab" />





Run both vc_redist.x64.exe first. Python changed how it looks for DLLs on the system and this adds/fixes the DLLs needed to run the pymupdf module. Without it, the program will error out at the beginning and again after inputting the input and output directories if only the 1st is run.

<img width="1106" height="387" alt="image" src="https://github.com/user-attachments/assets/329d3638-5b0a-4888-bf14-a237e6c1d8db" />


<img width="1114" height="257" alt="image" src="https://github.com/user-attachments/assets/ac06336e-f978-4206-9528-190290169be8" />





Ideally you'll have Python 3.14.6 or 3.14.7 installed. You can check it by opening a blank Powershell window and running the command " python " to start python and print out the version. If it's not at least 3.13 >, you'll need to uninstall it and reinstall a newer version.

<img width="890" height="85" alt="image" src="https://github.com/user-attachments/assets/32ac78ce-8eae-47cb-bdd2-954bf52299a8" />





Assuming you have admin privileges, you'll need to open a Powershell window as admin, run Set-ExecutionPolicy Bypass then run the run.ps1 file. The run.ps1 doesn't need to be run as admin, everything works in userland going forward, admin is just needed to change the execution policy.

<img width="951" height="135" alt="image" src="https://github.com/user-attachments/assets/42f7af01-e7f8-42a6-847d-b7c5e54e339b" />





If you don't, open run.ps1 in a text editor and copy the commands and run them manually in a normal Powershell window.


## What run.ps1 does

The first run needs internet. It creates a `.venv` folder, installs the Python packages and downloads the Chandra OCR 2 model (about 10 GB) into `models\`. Only public packages and model files are downloaded. No notebook data is involved.

Every run after that works with no internet. `process_pdfs.py` loads the model from `models\` only, turns on Hugging Face offline mode and blocks all outbound network connections while it runs. Notebook contents can't leave the machine.

You can pass the folders directly instead of typing them at the prompts:

```
.\run.ps1 C:\path\to\notebooks C:\path\to\output
.\run.ps1 C:\path\to\notebooks C:\path\to\output --skip-existing
.\run.ps1 C:\path\to\notebooks C:\path\to\output --device cuda   # only with a CUDA build of PyTorch
```

OCR results are cached in `<output folder>\.ocr_cache\`, so a rerun only redraws the overlay and doesn't OCR the pages again. Delete that folder to force a fresh OCR. The cache holds the transcribed notebook text, so treat it as being as sensitive as the notebooks.

The model runs on CPU by default. That's slow (minutes per page) and needs a lot of RAM (32 GB+ recommended).


Submit any issues to the Issues tracker on this Github repo so I can keep track of any errors you're getting and you can upload logs and files as needed. I'm also taking this route so I can make updates to the program as needed and it's easier for you to grab a copy of it.
