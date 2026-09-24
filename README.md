Start by downloading this repo and place it either in your Downloads folder or user folder and extract the zip.


<img width="1542" height="469" alt="image" src="https://github.com/user-attachments/assets/cb47dbd1-4956-465d-962c-955e215b5aab" />


Run the vc_redist.x64.exe first. Python changed how it looks for DLLs on the system and this adds/fixes the DLLs needed to run the pymupdf module. Without it, the program will error out at the beginning.

<img width="1114" height="257" alt="image" src="https://github.com/user-attachments/assets/ac06336e-f978-4206-9528-190290169be8" />

Ideally you'll have Python 3.14.6 or 3.14.7 installed. You can check it by opening a blank Powershell window and running the command " python " to start python and print out the version. If it's not at least 3.13 >, you'll need to uninstall it and reinstall a newer version.

<img width="890" height="85" alt="image" src="https://github.com/user-attachments/assets/32ac78ce-8eae-47cb-bdd2-954bf52299a8" />


Assuming you have admin privileges, you'll need to open a Powershell window as admin, run Set-ExecutionPolicy Bypass then run the run.ps1 file. The run.ps1 doesn't need to be run as admin, everything works in userland going forward, admin is just needed to change the execution policy.

<img width="951" height="135" alt="image" src="https://github.com/user-attachments/assets/42f7af01-e7f8-42a6-847d-b7c5e54e339b" />


If you don't, open run.ps1 in a text editor and copy the commands and run them manually in a normal Powershell window.


Submit any issues to the Issues tracker on this Github repo so I can keep track of any errors you're getting and you can upload logs and files as needed.
