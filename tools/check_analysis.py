"""Vérifie l'analyse sur les MP3 de test (vérité stockée dans le tag commentaire).

    python tools/check_analysis.py dossier_de_test
"""

import sys
from pathlib import Path

import mutagen
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixapp import analysis as a  # noqa: E402

ok = total = 0
for f in sorted(Path(sys.argv[1]).rglob("*.mp3")):
    tag = [x for x in mutagen.File(f).tags.values() if "bpm=" in str(x)][0].text[0]
    bpm_true = float(tag.split()[0][4:])
    key_true = tag.split("key=")[1]
    y = a.load_mono(str(f))
    first_true = np.argmax(np.abs(y) > 0.02) / a.SR      # premier kick réel
    bar = 240 / bpm_true
    intro_true, outro_true = first_true + 16 * bar, first_true + 48 * bar

    r = a.analyze_file(str(f))
    # tonalité relative (Am ↔ C) = mêmes notes, même chiffre Camelot : compatible
    true_cam = a.camelot(a.NOTES.index(key_true.rstrip("m")), key_true.endswith("m"))
    relative = r["key_name"] != key_true and r["camelot"][:-1] == true_cam[:-1]
    if relative:
        rel_count = globals().setdefault("rel_count", [0]); rel_count[0] += 1
    checks = {
        "bpm": abs(r["bpm"] - bpm_true) < 0.05,
        "clé": r["key_name"] == key_true or relative,
        "grille": abs(r["first_beat"] - first_true) < 0.010,
        "intro": abs(r["intro_end"] - intro_true) < 0.05,
        "outro": abs(r["outro_start"] - outro_true) < 0.05,
    }
    total += 1
    ok += all(checks.values())
    bad = [k for k, v in checks.items() if not v]
    print(f"{'OK ' if not bad else 'ERR'} {f.name[:38]:<38} bpm {r['bpm']:6.1f}  "
          f"clé {r['key_name']:>4} ({r['camelot']:>3})  grille {1000 * (r['first_beat'] - first_true):+4.0f} ms  "
          f"intro {r['intro_end'] - intro_true:+.2f}s  outro {r['outro_start'] - outro_true:+.2f}s"
          + (f"  ← {', '.join(bad)}" if bad else "")
          + (f"  (relatif de {key_true})" if relative else ""))
print(f"\n{ok}/{total} morceaux entièrement corrects"
      + (f" (dont {rel_count[0]} tonalités relatives, compatibles pour mixer)" if "rel_count" in globals() else ""))
