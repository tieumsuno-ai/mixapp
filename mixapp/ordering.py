"""« Flow DJ » : réordonner une playlist pour que chaque enchaînement soit
facile à mixer au tempo (écart de BPM ≤ 5 %), avec des clés compatibles, et
une énergie qui monte progressivement plutôt que par à-coups.

Le premier morceau (celui choisi pour démarrer) reste en tête.
"""

from __future__ import annotations

import math

MAX_SYNC = 0.05


def _tempo_ratio(a: float, b: float) -> float:
    return abs(math.log(b / a))


def _camelot(c):
    try:
        return int(c[:-1]), c[-1]
    except (TypeError, ValueError, IndexError):
        return None


def key_cost(ca, cb) -> float:
    a, b = _camelot(ca), _camelot(cb)
    if not a or not b:
        return 0.5
    (na, la), (nb, lb) = a, b
    d = min((na - nb) % 12, (nb - na) % 12)
    if (la == lb and d <= 1) or (la != lb and d == 0):
        return 0.0                      # même clé, voisine ou relative
    if (la == lb and d == 2) or (la != lb and d == 1):
        return 0.5                      # proche
    # transposable de ±1-2 demi-tons (≙ ±7 sur la roue) → acceptable
    for st in (1, -1, 2, -2):
        n2 = (nb - 1 + 7 * st) % 12 + 1
        d2 = min((na - n2) % 12, (n2 - na) % 12)
        if (la == lb and d2 <= 1) or (la != lb and d2 == 0):
            return 0.9
    return 1.6


def cost(a: dict, b: dict) -> float:
    if not a.get("bpm") or not b.get("bpm"):
        return 3.0
    d = _tempo_ratio(a["bpm"], b["bpm"])
    if d <= 0.01:
        c = 0.0
    elif d <= MAX_SYNC:
        c = 6.0 * d                     # 0,06 … 0,3
    else:
        c = 3.0 + 10.0 * d              # transition à effet obligatoire
    c += key_cost(a.get("camelot"), b.get("camelot"))
    ea, eb = a.get("energy") or 0.65, b.get("energy") or 0.65
    c += 2.0 * max(0.0, ea - eb - 0.03)  # éviter les chutes d'énergie
    c += 0.8 * max(0.0, eb - ea - 0.15)  # … et les sauts trop brusques
    return c


def path_cost(rows) -> float:
    return sum(cost(a, b) for a, b in zip(rows, rows[1:]))


def dj_order(rows: list[dict], first_id=None) -> list[dict]:
    """Ordre qui minimise la difficulté des enchaînements (glouton + 2-opt)."""
    if len(rows) < 3:
        return list(rows)
    rest = list(rows)
    start = next((r for r in rest if r["id"] == first_id), rest[0])
    rest.remove(start)
    path = [start]
    while rest:                                       # plus proche voisin
        nxt = min(rest, key=lambda r: cost(path[-1], r))
        rest.remove(nxt)
        path.append(nxt)
    improved, n = True, len(path)
    while improved:                                   # 2-opt (le 1er reste fixe)
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new = path[:i] + path[i:j + 1][::-1] + path[j + 1:]
                if path_cost(new) + 1e-9 < path_cost(path):
                    path, improved = new, True
    return path
