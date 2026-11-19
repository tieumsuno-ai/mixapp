; Installateur Windows de MixApp (Inno Setup). Construit par GitHub Actions.
; S'installe pour l'utilisateur courant : pas besoin d'être administrateur.
[Setup]
AppId={{6F2C3B1E-4A7D-4E8B-9C21-5D0A7F3E2B10}
AppName=MixApp
AppVersion=0.4.0
AppPublisher=MixApp
DefaultDirName={localappdata}\Programs\MixApp
DefaultGroupName=MixApp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=MixApp-Setup-Windows
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MixApp.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\MixApp\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\MixApp"; Filename: "{app}\MixApp.exe"
Name: "{userdesktop}\MixApp"; Filename: "{app}\MixApp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MixApp.exe"; Description: "Lancer MixApp"; Flags: nowait postinstall skipifsilent
