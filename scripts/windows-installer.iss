; Build after scripts/build_portable.py on Windows. No administrator rights.
#ifndef AppVersion
  #define AppVersion "0.10.0"
#endif
#define BundleDir SourcePath + "\..\dist\HandoffDesk"
#define OfflineWebView2 ReadEnv("HANDOFFDESK_WEBVIEW2_OFFLINE")

[Setup]
AppId={{844F384D-D553-4E7F-9588-C07A6B0C7B1A}
AppName=HandoffDesk
AppVersion={#AppVersion}
AppPublisher=HandoffDesk contributors
AppPublisherURL=https://github.com/EnumaElish123/HandoffDesk
DefaultDirName={localappdata}\Programs\HandoffDesk
DefaultGroupName=HandoffDesk
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=HandoffDesk-{#AppVersion}-windows-x64-setup
SetupIconFile=..\handoffdesk\assets\app.ico
UninstallDisplayIcon={app}\HandoffDesk.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "connectcodex"; Description: "Connect Codex for automatic project records (first hook trust happens in Codex)"; GroupDescription: "Agent integration:"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#if Len(OfflineWebView2) > 0
Source: "{#OfflineWebView2}"; DestDir: "{tmp}"; DestName: "WebView2Runtime.exe"; Flags: dontcopy
#endif

[Icons]
Name: "{group}\HandoffDesk"; Filename: "{app}\HandoffDesk.exe"
Name: "{group}\Uninstall HandoffDesk"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HandoffDesk"; Filename: "{app}\HandoffDesk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HandoffDesk.exe"; Description: "Open HandoffDesk"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\HandoffDeskCLI.exe"; Parameters: "disconnect codex --program-dir ""{app}"""; RunOnceId: "DisconnectOwnedCodex"; Flags: runhidden waituntilterminated

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  BootstrapURL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function HasRuntimeVersion(RootKey: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, WebViewKey, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0');
end;

function HasWebView2: Boolean;
begin
  Result := HasRuntimeVersion(HKCU) or HasRuntimeVersion(HKLM32);
end;

function PowerShellQuote(Value: String): String;
begin
  StringChangeEx(Value, '''', '''''', True);
  Result := '''' + Value + '''';
end;

function IsMicrosoftSigned(FileName: String): Boolean;
var
  Command: String;
  ExitCode: Integer;
begin
  Command := '$s = Get-AuthenticodeSignature -LiteralPath ' + PowerShellQuote(FileName) +
    '; if (($s.Status -eq ''Valid'') -and ($s.SignerCertificate.Subject -match ''O=Microsoft Corporation(?:,|$)'')) { exit 0 } else { exit 1 }';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "' + Command + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RuntimeInstaller: String;
  ExitCode: Integer;
begin
  Result := '';
  if HasWebView2 then Exit;
  WizardForm.StatusLabel.Caption := 'Preparing Microsoft WebView2 Runtime...';
  try
    #if Len(OfflineWebView2) > 0
    ExtractTemporaryFile('WebView2Runtime.exe');
    RuntimeInstaller := ExpandConstant('{tmp}\WebView2Runtime.exe');
    #else
    DownloadTemporaryFile(BootstrapURL, 'WebView2Runtime.exe', '', nil);
    RuntimeInstaller := ExpandConstant('{tmp}\WebView2Runtime.exe');
    #endif
    if not IsMicrosoftSigned(RuntimeInstaller) then begin
      Result := 'The WebView2 installer signature could not be verified. Setup did not run it. Use the official Microsoft Runtime installer and retry.';
      Exit;
    end;
    if not Exec(RuntimeInstaller, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
      Result := 'Could not start the WebView2 Runtime installer.';
      Exit;
    end;
    if (ExitCode <> 0) and (ExitCode <> 3010) then begin
      Result := 'WebView2 installation failed (exit ' + IntToStr(ExitCode) + '). Check network access or use an offline installer.';
      Exit;
    end;
    if ExitCode = 3010 then NeedsRestart := True;
    if not HasWebView2 then
      Result := 'WebView2 Runtime is not available yet. Restart Windows if requested, then run setup again.';
  except
    Result := 'Could not prepare WebView2 Runtime: ' + GetExceptionMessage + '. Use an offline installer when this computer cannot access Microsoft downloads.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('connectcodex') then begin
    if (not Exec(ExpandConstant('{app}\HandoffDeskCLI.exe'), 'connect codex', '',
        SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then
      MsgBox('HandoffDesk was installed, but Codex connection needs attention. Open a terminal and run HandoffDeskCLI.exe connect codex to see the reason. Existing settings and records are preserved.', mbInformation, MB_OK)
    else if not WizardSilent then
      MsgBox('Codex connection files are ready. In Codex, open /hooks and trust the HandoffDesk definitions, then start a new conversation. Installation does not grant host trust automatically.', mbInformation, MB_OK);
  end;
end;

// No UninstallDelete entry for state, registry, backups, projects or attachments.
