#define AppName "JetVideo | AutoPost"
#define AppVersion "1.1.2"
[Setup]
AppId={{8AD67C8F-E442-44D7-9B97-B78D94FE0101}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\JetVideo
DefaultGroupName=JetVideo
UninstallDisplayIcon={app}\JetVideo.exe
OutputDir=dist
OutputBaseFilename=JetVideo_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UsePreviousAppDir=yes
DisableDirPage=no
AllowNoIcons=yes
SetupIconFile=icon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[CustomMessages]
russian.DesktopShortcut=Создать ярлык на рабочем столе
english.DesktopShortcut=Create a desktop shortcut
ukrainian.DesktopShortcut=Створити ярлик на робочому столі

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; Flags: unchecked

[Files]
Source: "dist\JetVideo.exe"; DestDir: "{app}"; Flags: ignoreversion

Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.ru.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.uk.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "COPYRIGHT.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "third_party\*"; DestDir: "{app}\third_party"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "docs\*.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "docs\r2-images\*.png"; DestDir: "{app}\docs\r2-images"; Flags: ignoreversion
Source: "docs\buffer-images\*.png"; DestDir: "{app}\docs\buffer-images"; Flags: ignoreversion

[Icons]
Name: "{group}\JetVideo"; Filename: "{app}\JetVideo.exe"
Name: "{autodesktop}\JetVideo"; Filename: "{app}\JetVideo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JetVideo.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
