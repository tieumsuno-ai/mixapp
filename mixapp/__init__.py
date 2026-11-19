"""MixApp — lecteur MP3 qui enchaîne les morceaux au tempo."""

import os
import sys
from pathlib import Path

__version__ = "0.3.0"


def _user_cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "MixApp"


# librosa compile certaines fonctions (numba) et garde le résultat en cache.
# Sans réglage, ce cache s'écrirait à côté du code, c'est-à-dire *dans*
# l'application installée (MixApp.app, Program Files…) : écriture refusée ou
# signature de l'appli abîmée. On le place dans le dossier de cache de l'utilisateur.
try:
    _numba_cache = _user_cache_dir() / "numba"
    _numba_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(_numba_cache))
except OSError:
    pass
