Start by downloading this repo and place it either in your Downloads folder or user folder and extract the zip.

<img width="1542" height="469" alt="image" src="https://github.com/user-attachments/assets/cb47dbd1-4956-465d-962c-955e215b5aab" />


Run the vc_redist.x64.exe first. Python changed how it looks for DLLs on the system and this adds/fixes the DLLs needed to run the pymupdf module. Without it, the program will error out at the beginning.

Assuming you have admin privileges, you'll need to open a Powershell window as admin, run Set-ExecutionPolicy Bypass then run the run.ps1 file. 

If you don't, open run.ps1 in a text editor and copy the commands and run them manually in a normal Powershell window.

