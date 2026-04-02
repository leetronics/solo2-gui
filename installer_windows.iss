#define MyAppName "SoloKeys GUI"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "SoloKeys GUI Contributors"
#endif
#ifndef MyAppSourceDir
  #define MyAppSourceDir "dist\SoloKeys GUI"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "dist\installer"
#endif

[Setup]
AppId={{E5C9FE6E-B50A-4719-9B0D-5A8DE21E6D32}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={commonpf64}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#MyOutputDir}
OutputBaseFilename=SoloKeys-GUI-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=src\solo_gui\resources\icon-light.ico
UninstallDisplayIcon={app}\SoloKeys GUI.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SoloKeys GUI"; Filename: "{app}\SoloKeys GUI.exe"
Name: "{autodesktop}\SoloKeys GUI"; Filename: "{app}\SoloKeys GUI.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SoloKeys GUI.exe"; Description: "Launch SoloKeys GUI"; Flags: nowait postinstall skipifsilent

[UninstallRun]
