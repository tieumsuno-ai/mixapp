#!/bin/bash
# Installe MixApp sur ce Mac : double-clic sur ce fichier.
# (La 1re fois, macOS peut demander une confirmation : clic droit > Ouvrir.)
set -e
cd "$(dirname "$0")"
clear
echo "=============================================="
echo "        Installation de MixApp (Mac)"
echo "=============================================="
echo

# 1) Trouver Python 3.10 ou plus récent
PY=""
for c in python3.12 python3.13 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3 n'est pas installé sur ce Mac (il en faut la version 3.12)."
  echo "La page de téléchargement va s'ouvrir : télécharge « macOS 64-bit universal2 installer »,"
  echo "installe-le, puis double-clique à nouveau sur ce fichier."
  open "https://www.python.org/downloads/macos/"
  read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
  exit 1
fi
echo "✓ Python trouvé : $("$PY" --version)"

# 2) Environnement de construction (dans ce dossier, rien n'est installé ailleurs)
echo "→ Préparation (quelques minutes la 1re fois)…"
"$PY" -m venv .build-venv
source .build-venv/bin/activate
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q
echo "✓ Bibliothèques installées"

# 3) Construction de MixApp.app
echo "→ Construction de l'application…"
rm -rf build dist
python -m PyInstaller MixApp.spec --noconfirm --log-level ERROR
echo "✓ MixApp.app construite"

# 4) Copie dans Applications
DEST="/Applications"
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
rm -rf "$DEST/MixApp.app"
cp -R dist/MixApp.app "$DEST/"
echo "✓ Installée dans $DEST"
echo
echo "C'est prêt ! MixApp va s'ouvrir. Tu la retrouveras ensuite dans $DEST"
echo "(ou avec Spotlight : ⌘ Espace, puis « MixApp »)."
open "$DEST/MixApp.app"
sleep 2
