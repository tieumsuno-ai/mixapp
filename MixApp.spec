# Recette PyInstaller : construit MixApp.app (Mac) ou MixApp\MixApp.exe (Windows).
#   pyinstaller MixApp.spec --noconfirm
# Utilisée par les scripts « Installer MixApp » ; inutile de la lancer à la main.
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = []
binaries = []
hidden = []
for pkg in ("librosa", "soundfile", "sounddevice", "pedalboard", "soxr"):
    datas += collect_data_files(pkg)
    binaries += collect_dynamic_libs(pkg)
hidden += collect_submodules("librosa") + collect_submodules("scipy.signal")
hidden += ["sklearn.utils._typedefs", "watchdog.observers"]

a = Analysis(
    ["MixApp.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["matplotlib", "tkinter", "IPython", "PyQt5", "PyQt6", "pandas", "cryptography",
              "PIL", "pytest", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
              "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSvg"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MixApp",
    console=False,            # pas de fenêtre de terminal
    argv_emulation=False,
    icon="assets/icon.ico" if sys.platform.startswith("win") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MixApp")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MixApp.app",
        icon="assets/icon.icns",
        bundle_identifier="fr.mixapp.player",
        info_plist={
            "CFBundleDisplayName": "MixApp",
            "CFBundleDevelopmentRegion": "fr",
            "CFBundleLocalizations": ["fr"],
            "CFBundleShortVersionString": "0.4.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.music",
            # Pas d'« App Nap » : macOS ralentirait fortement l'analyse en
            # arrière-plan dès que la fenêtre est cachée.
            "NSAppSleepDisabled": True,
        },
    )
