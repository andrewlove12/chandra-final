; Inno Setup script for Chandra Notebook OCR.
; Build: install Inno Setup 6 (https://jrsoftware.org/isinfo.php), then
;   ISCC.exe installer\chandra-ocr.iss
; The setup .exe is written to dist\.
;
; What the installer does:
;   1. Runs as administrator and copies the program files into the chosen folder (Program Files by default).
;   2. Asks whether the PC has an NVIDIA or AMD graphics card (the detected one is pre-selected)
;      and installs the matching PyTorch build; "Neither" installs the standard PyPI build.
;      Silent installs: add /GPU=nvidia, /GPU=amd or /GPU=none (default: the detected card).
;   3. Installs the bundled Microsoft VC++ runtimes (2015 and the current v14 build; needed by DLLs in pymupdf,
;      PyTorch and onnxruntime). An already-installed newer runtime is left as it is.
;   4. Runs setup.ps1 in a visible window: downloads the project's own Python version into the
;      install folder (never uses a Python already on the PC), the matching PyTorch build and the
;      OCR models (~15 GB in total; needs internet).
;      Nothing outside the install folder is changed. Notebooks are never involved.
;   5. Creates Start menu / desktop shortcuts to the app (gui.py).
; The uninstaller removes the folder including the private Python and the models.

#define AppName "Chandra Notebook OCR"
#define AppVersion "1.0.0"
#define AppPublisher "andrewlove12"
#define AppURL "https://github.com/andrewlove12/chandra-final"

[Setup]
AppId={{2F24BF4E-C784-439D-89FC-8CF8B644A076}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; The whole installer runs as administrator (the VC++ runtimes need it).
PrivilegesRequired=admin
; Window settings live in the current user's HKCU (see [Registry]); the admin is normally that same user.
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Python, PyTorch and the models need roughly 20 GB once installed.
ExtraDiskSpaceRequired=21474836480
OutputDir=..\dist
OutputBaseFilename=ChandraNotebookOCR-Setup-{#AppVersion}
SetupIconFile=installer.ico
UninstallDisplayIcon={app}\installer\app.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\process_pdfs.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\gui.py";             DestDir: "{app}"; Flags: ignoreversion
Source: "..\repeat_guard.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\orientation.py";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\gpu_select.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\model_patches.py";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\model_config.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\quantization.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\download_model.py";  DestDir: "{app}"; Flags: ignoreversion
Source: "..\compare_runs.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup.ps1";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion
Source: "app.ico";               DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\vc_redist.x64_1.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\vc_redist.x64_2.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[InstallDelete]
; Left over from older versions: everything now goes through the installer and the app.
Type: files; Name: "{app}\run.ps1"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\gui.py"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\app.ico"; AppUserModelID: "ChandraNotebookOCR.App"; Comment: "Transcribe handwritten notebook PDFs offline"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\gui.py"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\app.ico"; AppUserModelID: "ChandraNotebookOCR.App"
Name: "{autoprograms}\{#AppName} setup log"; Filename: "{app}\logs\setup.log"

[Registry]
; The app stores its window settings here; remove them on uninstall.
Root: HKCU; Subkey: "Software\ChandraNotebookOCR"; Flags: uninsdeletekeyifempty dontcreatekey
Root: HKCU; Subkey: "Software\ChandraNotebookOCR\gui"; Flags: uninsdeletekey dontcreatekey

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\gui.py"""; WorkingDir: "{app}"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent runasoriginaluser; Check: SetupSucceeded

[UninstallDelete]
; Everything setup.ps1 created: private Python, packages, models, logs.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: dirifempty;     Name: "{app}"

[Code]
var
  HardwarePage: TInputOptionWizardPage;
  SetupOK: Boolean;

const
  // Order of the choices on the graphics card page.
  ChoiceNvidia = 0;
  ChoiceAmd = 1;
  ChoiceNeither = 2;

var
  GpuNames: String;

function DetectedGpus(): String;
var
  Locator, Service, Items, Item: Variant;
  I: Integer;
begin
  Result := '';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT Name FROM Win32_VideoController');
    for I := 0 to Items.Count - 1 do
    begin
      Item := Items.ItemIndex(I);
      if Result <> '' then Result := Result + ', ';
      Result := Result + Item.Name;
    end;
  except
    Result := '';
  end;
end;

function HasNvidia(): Boolean;
begin
  Result := Pos('NVIDIA', Uppercase(GpuNames)) > 0;
end;

function HasSupportedAmd(): Boolean;
var
  U: String;
begin
  // The AMD PyTorch (ROCm) builds for Windows support Radeon RX 7000 and RX 9000 cards.
  U := Uppercase(GpuNames);
  Result := (Pos('RADEON', U) > 0) and ((Pos('RX 7', U) > 0) or (Pos('RX 9', U) > 0));
end;

function DetectedChoice(): Integer;
begin
  if HasNvidia() then
    Result := ChoiceNvidia
  else if HasSupportedAmd() then
    Result := ChoiceAmd
  else
    Result := ChoiceNeither;
end;

procedure InitializeWizard();
var
  Detected: String;
begin
  GpuNames := DetectedGpus();
  if GpuNames = '' then Detected := '(couldn''t detect)' else Detected := GpuNames;
  HardwarePage := CreateInputOptionPage(wpSelectTasks,
    'Graphics card', 'Do you have an NVIDIA or AMD graphics card?',
    'The OCR model runs much faster on a graphics card, using a version of PyTorch made for that card.' + #13#10#13#10 +
    'Graphics hardware found on this PC: ' + Detected + #13#10#13#10 +
    'The matching choice is selected. Pick "Neither" if you don''t have one or aren''t sure.',
    True, False);
  HardwarePage.Add('NVIDIA graphics card (GeForce / RTX; needs NVIDIA driver 580 or newer)');
  HardwarePage.Add('AMD graphics card (Radeon RX 7000 or RX 9000 series)');
  HardwarePage.Add('Neither / not sure (standard PyTorch; runs on the processor, much slower)');
  HardwarePage.SelectedValueIndex := DetectedChoice();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> HardwarePage.ID then
    Exit;
  if (HardwarePage.SelectedValueIndex = ChoiceNvidia) and not HasNvidia() then
    Result := SuppressibleMsgBox('No NVIDIA graphics card was found on this PC. The NVIDIA version of PyTorch ' +
      'won''t be able to use the graphics card, so OCR would fall back to the much slower processor.' + #13#10#13#10 +
      'Continue with NVIDIA anyway?', mbConfirmation, MB_YESNO, IDNO) = IDYES
  else if (HardwarePage.SelectedValueIndex = ChoiceAmd) and not HasSupportedAmd() then
    Result := SuppressibleMsgBox('No AMD Radeon RX 7000 or RX 9000 card was found on this PC. The AMD version ' +
      'of PyTorch only supports those cards, so setup will probably fail.' + #13#10#13#10 +
      'Continue with AMD anyway?', mbConfirmation, MB_YESNO, IDNO) = IDYES;
end;

function BackendArg(): String;
var
  Choice: Integer;
  Param: String;
begin
  Choice := HardwarePage.SelectedValueIndex;
  // Silent installs: /GPU=nvidia|amd|none chooses; otherwise the detected card is used.
  Param := Lowercase(ExpandConstant('{param:GPU|}'));
  if Param = 'nvidia' then Choice := ChoiceNvidia
  else if Param = 'amd' then Choice := ChoiceAmd
  else if Param = 'none' then Choice := ChoiceNeither;
  case Choice of
    ChoiceNvidia: Result := 'cuda';
    ChoiceAmd: Result := 'rocm';
  else
    Result := 'cpu';  // standard PyTorch from PyPI
  end;
end;

function SetupSucceeded(): Boolean;
begin
  Result := SetupOK;
end;

var
  RestartNeeded: Boolean;

function NeedRestart(): Boolean;
begin
  Result := RestartNeeded;
end;

procedure InstallVCRedist(const FileName, Title: String);
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Installing the ' + Title + '...';
  // The installer is already elevated, so the runtime installs silently.
  // Exit codes: 0 = installed, 1638 = this or a newer version is already installed,
  // 3010 / 1641 = installed, restart needed.
  if Exec(ExpandConstant('{tmp}\' + FileName), '/install /quiet /norestart', '',
          SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if (ResultCode = 0) or (ResultCode = 1638) then
      Exit;
    if (ResultCode = 3010) or (ResultCode = 1641) then
    begin
      RestartNeeded := True;
      Exit;
    end;
  end;
  SuppressibleMsgBox('The ' + Title + ' could not be installed (error ' + IntToStr(ResultCode) + '). ' +
         'If the app fails to start, run ' + FileName + ' from the project page manually.',
         mbInformation, MB_OK, IDOK);
end;

procedure InstallVCRedists();
begin
  // Older first: on a PC without either, the 2015 runtime goes in and the current one then upgrades it.
  InstallVCRedist('vc_redist.x64_1.exe', 'Microsoft Visual C++ 2015 runtime');
  InstallVCRedist('vc_redist.x64_2.exe', 'Microsoft Visual C++ v14 runtime');
end;

procedure RunSetupScript();
var
  Params: String;
  ResultCode: Integer;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\setup.ps1') +
            '" -Backend ' + BackendArg();
  repeat
    WizardForm.StatusLabel.Caption := 'Downloading Python, PyTorch and the OCR models (about 15 GB). ' +
      'A window shows the details. This can take 30-60 minutes...';
    WizardForm.ProgressGauge.Style := npbstMarquee;
    SetupOK := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
                    ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
    WizardForm.ProgressGauge.Style := npbstNormal;
    if SetupOK or WizardSilent() then
      Break;  // silent installs can't answer the retry question (it would retry forever)
  until SuppressibleMsgBox('Setup could not finish downloading and installing the OCR components ' +
               '(error ' + IntToStr(ResultCode) + ').' + #13#10#13#10 +
               'Check your internet connection. Details are in:' + #13#10 +
               ExpandConstant('{app}\logs\setup.log') + #13#10#13#10 +
               'Try again now?', mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY;
  if not SetupOK then
    SuppressibleMsgBox('The program files are installed, but the OCR components are not ready yet. ' +
           'Run this installer again later to finish; it picks up where it left off.', mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InstallVCRedists();
    RunSetupScript();
  end;
end;
