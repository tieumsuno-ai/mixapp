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


_memo: dict = {}


def dj_order(rows: list[dict], first_id=None) -> list[dict]:
    """Ordre qui minimise la difficulté des enchaînements (glouton + 2-opt).
    Rapide (matrice des coûts, gain de chaque inversion calculé en O(1)) et
    mémorisé : redemander le même ordre ne coûte rien."""
    if len(rows) < 3:
        return list(rows)
    key = (tuple((r["id"], r.get("bpm"), r.get("camelot")) for r in rows), first_id)
    if key in _memo:
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in _memo[key]]
    n = len(rows)
    C = [[cost(a, b) if a is not b else 0.0 for b in rows] for a in rows]
    start = next((k for k, r in enumerate(rows) if r["id"] == first_id), 0)
    rest = set(range(n)) - {start}
    path = [start]
    while rest:                                       # plus proche voisin
        cur = path[-1]
        nxt = min(rest, key=lambda k: C[cur][k])
        rest.remove(nxt)
        path.append(nxt)
    for _ in range(50):                               # 2-opt (le 1er reste fixe)
        fwd = [0.0]
        bwd = [0.0]
        for k in range(n - 1):
            fwd.append(fwd[-1] + C[path[k]][path[k + 1]])
            bwd.append(bwd[-1] + C[path[k + 1]][path[k]])
        best, best_ij = -1e-9, None
        for i in range(1, n - 1):
            a_prev = path[i - 1]
            for j in range(i + 1, n):
                old = C[a_prev][path[i]] + (fwd[j] - fwd[i])
                new = C[a_prev][path[j]] + (bwd[j] - bwd[i])
                if j + 1 < n:
                    old += C[path[j]][path[j + 1]]
                    new += C[path[i]][path[j + 1]]
                gain = old - new
                if gain > best + 1e-9:
                    best, best_ij = gain, (i, j)
        if best_ij is None:
            break
        i, j = best_ij
        path[i:j + 1] = path[i:j + 1][::-1]
    _memo[key] = [rows[k]["id"] for k in path]
    if len(_memo) > 50:
        _memo.pop(next(iter(_memo)))
    return [rows[k] for k in path]
