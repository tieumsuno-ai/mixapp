@echo off
rem Installe MixApp sur ce PC : double-clic sur ce fichier.
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo         Installation de MixApp (Windows)
echo ==============================================
echo.

rem 1) Trouver Python 3.10 ou plus recent
set PY=
py -3.12 -c "import sys" >nul 2>&1 && set PY=py -3.12
if "%PY%"=="" py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PY=py -3
if "%PY%"=="" (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PY=python
)
if "%PY%"=="" (
  echo Python 3 n'est pas installe sur ce PC ^(il en faut la version 3.12^).
  echo La page de telechargement va s'ouvrir : installe-le en cochant
  echo « Add python.exe to PATH », puis double-clique a nouveau sur ce fichier.
  start https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo OK  Python trouve

rem 2) Environnement de construction (dans ce dossier)
echo ^> Preparation (quelques minutes la 1re fois)...
%PY% -m venv .build-venv || goto :erreur
call .build-venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q || goto :erreur
echo OK  Bibliotheques installees

rem 3) Construction
echo ^> Construction de l'application...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller MixApp.spec --noconfirm --log-level ERROR || goto :erreur
echo OK  MixApp construite

rem 4) Installation + raccourci sur le Bureau
set DEST=%LOCALAPPDATA%\Programs\MixApp
if exist "%DEST%" rmdir /s /q "%DEST%"
xcopy /e /i /q dist\MixApp "%DEST%" >nul || goto :erreur
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\MixApp.lnk'); $s.TargetPath='%DEST%\MixApp.exe'; $s.WorkingDirectory='%DEST%'; $s.Save()"
echo OK  Installee, raccourci « MixApp » cree sur le Bureau
echo.
start "" "%DEST%\MixApp.exe"
timeout /t 3 >nul
exit /b 0

:erreur
echo.
echo Une erreur s'est produite. Fais une capture de cette fenetre et envoie-la.
pause
exit /b 1
