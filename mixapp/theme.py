"""Thème clair / sombre.

L'interface est dessinée avec la palette sombre ; en mode clair, chaque
couleur est remplacée par son équivalent clair (feuille de style, textes des
listes, éléments dessinés à la main)."""

from __future__ import annotations

import re

_light = False

# couleur sombre → couleur claire. Les couleurs absentes (accents bleus,
# blanc « #fff » sur fond coloré…) restent identiques dans les deux thèmes.
LIGHT = {
    # fonds
    "#15171c": "#eef0f4",   # fenêtre
    "#1b1e24": "#ffffff",   # listes, lecteur
    "#1f2229": "#f5f6f9",   # lignes alternées
    "#22252d": "#ffffff",   # champs
    "#2a2e38": "#e4e7ee",   # boutons
    "#343947": "#d8dde7",   # survol
    "#343a48": "#d8dde7",
    "#353a46": "#cfd4de",   # bordures
    "#333844": "#cfd4de",
    "#3a4050": "#c4cad6",
    "#2f3b66": "#5b74e8",   # sélection (texte blanc dessus)
    "#3a2a2a": "#f2dedc",
    # textes
    "#e6e6e6": "#1d2129",
    "#f2f2f2": "#12151b",
    "#ffffff": "#12151b",   # textes « en cours » (les icônes blanches restent #fff)
    "#c8cdd8": "#3a4050",
    "#aab0bf": "#4f5668",
    "#9aa3b5": "#5d6578",
    "#8a90a0": "#687080",
    "#6f7686": "#80879a",
    "#666": "#a3a9b5",
    "#555": "#b5bac4",
    "#7a6060": "#b08a8a",
    "#5c6375": "#9aa0ac",
    "#596074": "#b9bfcb",
    "#8fb3ff": "#2f5bd8",   # à venir
    "#7891c9": "#4a67b0",
    "#646a78": "#9aa0ac",   # déjà joué
    "#585e6b": "#a3a8b3",
    "#555b67": "#a8adb7",
    "#ffd479": "#a86f00",   # prioritaire
}

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def set_light(on: bool):
    global _light
    _light = bool(on)


def is_light() -> bool:
    return _light


def c(color: str) -> str:
    """La couleur à utiliser dans le thème actuel."""
    if not _light:
        return color
    return LIGHT.get(color.lower(), color)


def css(sheet: str) -> str:
    """Feuille de style adaptée au thème actuel."""
    if not _light or not sheet:
        return sheet
    return _HEX.sub(lambda m: LIGHT.get(m.group(0).lower(), m.group(0)), sheet)
