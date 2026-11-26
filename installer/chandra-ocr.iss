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
#define AppVersion "1.0.2"
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
Source: "..\run_log.py";         DestDir: "{app}"; Flags: ignoreversion
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

[Dirs]
; Log files (logs\run-*.log, logs\gui-*.log, setup.log) for troubleshooting. The app runs as a normal
; user and Program Files is read-only for them, so give users write access to this one folder.
Name: "{app}\logs"; Permissions: users-modify

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

// --- setup.ps1 runs hidden; its output is shown on the Installing page --------------
// The green-box line (FilenameLabel) shows the latest output line (or download %),
// and a scrolling box under the progress bar shows everything.
const
  EM_SCROLLCARET = $00B7;
  MaxOutputLines = 1000;

var
  OutputMemo: TNewMemo;
  SetupPid: Integer;
  SetupCancelled: Boolean;
  LastPercent: Integer;
  OldCancelClick: TNotifyEvent;

procedure CreateOutputMemo();
var
  Page: TNewNotebookPage;
begin
  Page := WizardForm.InstallingPage;
  OutputMemo := TNewMemo.Create(WizardForm);
  OutputMemo.Parent := Page;
  OutputMemo.Left := WizardForm.ProgressGauge.Left;
  OutputMemo.Top := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + ScaleY(12);
  OutputMemo.Width := WizardForm.ProgressGauge.Width;
  OutputMemo.Height := Page.ClientHeight - OutputMemo.Top - ScaleY(4);
  OutputMemo.Anchors := [akLeft, akTop, akRight, akBottom];
  OutputMemo.ReadOnly := True;
  OutputMemo.ScrollBars := ssVertical;
  OutputMemo.WordWrap := True;
  OutputMemo.Font.Name := 'Consolas';
  OutputMemo.Font.Size := 8;
  OutputMemo.Visible := False;
end;

procedure AddOutputLine(const S: String);
begin
  if OutputMemo.Lines.Count >= MaxOutputLines then
    OutputMemo.Lines.Delete(0);
  OutputMemo.Lines.Add(S);
  OutputMemo.SelStart := Length(OutputMemo.Text);
  SendMessage(OutputMemo.Handle, EM_SCROLLCARET, 0, 0);
end;

function ParseProgress(const S: String; var Percent: Integer): Boolean;
var
  Rest: String;
  P: Integer;
  Done, Total: Int64;
begin
  // pip --progress-bar raw prints 'Progress <bytes> of <total>'.
  Result := False;
  if Copy(S, 1, 9) <> 'Progress ' then Exit;
  Rest := Copy(S, 10, Length(S));
  P := Pos(' of ', Rest);
  if P = 0 then Exit;
  Done := StrToInt64Def(Copy(Rest, 1, P - 1), -1);
  Total := StrToInt64Def(Copy(Rest, P + 4, Length(Rest)), -1);
  if (Done < 0) or (Total <= 0) then Exit;
  Percent := Integer(Done * 100 div Total);
  Result := True;
end;

function EnableWindow(hWnd: HWND; bEnable: BOOL): BOOL;
  external 'EnableWindow@user32.dll stdcall';

procedure OnSetupOutput(const S: String; const Error, FirstLine: Boolean);
var
  Line: String;
  Percent: Integer;
begin
  // Setup disables its window while a program runs; turn it back on so the
  // Cancel button (and scrolling the output box) work during the long download.
  if not SetupCancelled then
    EnableWindow(WizardForm.Handle, True);
  Line := TrimRight(S);
  if Line = '' then Exit;
  if Copy(Line, 1, 11) = '[setup pid ' then
  begin
    SetupPid := StrToIntDef(Copy(Line, 12, Length(Line) - 12), 0);
    Exit;
  end;
  if ParseProgress(Line, Percent) then
  begin
    // Download progress drives the bar; not added to the box (thousands of lines).
    if Percent <> LastPercent then
    begin
      LastPercent := Percent;
      WizardForm.ProgressGauge.Style := npbstNormal;
      WizardForm.ProgressGauge.Max := 100;
      WizardForm.ProgressGauge.Position := Percent;
      WizardForm.FilenameLabel.Caption := 'Downloading... ' + IntToStr(Percent) + '%';
    end;
    Exit;
  end;
  if LastPercent >= 0 then
  begin
    LastPercent := -1;
    WizardForm.ProgressGauge.Style := npbstMarquee;
  end;
  WizardForm.FilenameLabel.Caption := Trim(Line);
  AddOutputLine(Line);
end;

procedure CancelSetupClick(Sender: TObject);
var
  ResultCode: Integer;
begin
  if SetupCancelled or (SetupPid = 0) then Exit;
  if MsgBox('Stop downloading the OCR components?' + #13#10#13#10 +
            'The program files stay installed. Run this installer again later to finish; ' +
            'it picks up where it left off.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
    Exit;
  SetupCancelled := True;
  WizardForm.CancelButton.Enabled := False;
  AddOutputLine('Cancelling...');
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/PID ' + IntToStr(SetupPid) + ' /T /F', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure RunSetupScript();
var
  Params: String;
  ResultCode: Integer;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\setup.ps1') +
            '" -Backend ' + BackendArg();
  CreateOutputMemo();
  OutputMemo.Visible := True;
  // Let the user stop this long step (the Cancel button is normally off after files are copied).
  OldCancelClick := WizardForm.CancelButton.OnClick;
  WizardForm.CancelButton.OnClick := @CancelSetupClick;
  repeat
    WizardForm.StatusLabel.Caption := 'Downloading Python, PyTorch and the OCR models (about 15 GB)...';
    WizardForm.FilenameLabel.Caption := '';
    AddOutputLine('This needs internet and can take 30-60 minutes. The full log is saved in ' +
                  ExpandConstant('{app}\logs\setup.log'));
    WizardForm.ProgressGauge.Style := npbstMarquee;
    SetupPid := 0;
    LastPercent := -1;
    WizardForm.CancelButton.Enabled := True;
    try
      SetupOK := ExecAndLogOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
                                  ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode,
                                  @OnSetupOutput) and (ResultCode = 0);
    except
      AddOutputLine(GetExceptionMessage());
      SetupOK := False;
    end;
    WizardForm.CancelButton.Enabled := False;
    WizardForm.ProgressGauge.Style := npbstNormal;
    WizardForm.ProgressGauge.Position := WizardForm.ProgressGauge.Max;
    if SetupOK or SetupCancelled or WizardSilent() then
      Break;  // silent installs can't answer the retry question (it would retry forever)
    AddOutputLine('');
  until SuppressibleMsgBox('Setup could not finish downloading and installing the OCR components ' +
               '(error ' + IntToStr(ResultCode) + ').' + #13#10#13#10 +
               'Check your internet connection. Details are in:' + #13#10 +
               ExpandConstant('{app}\logs\setup.log') + #13#10#13#10 +
               'Try again now?', mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY;
  WizardForm.CancelButton.OnClick := OldCancelClick;
  if SetupOK then
    WizardForm.FilenameLabel.Caption := 'Setup complete.'
  else
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
