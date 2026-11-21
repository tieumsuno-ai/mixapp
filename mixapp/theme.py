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
    # fonds : gris doux plutôt que blanc pur (moins éblouissant)
    "#15171c": "#e3e6ec",   # fenêtre
    "#1b1e24": "#f4f5f8",   # listes, lecteur
    "#1f2229": "#eaecf1",   # lignes alternées
    "#22252d": "#f4f5f8",   # champs
    "#2a2e38": "#d5dae3",   # boutons
    "#343947": "#c9cfda",   # survol
    "#343a48": "#c9cfda",
    "#353a46": "#b9c0cc",   # bordures
    "#333844": "#b9c0cc",
    "#3a4050": "#aab2c0",
    "#2f3b66": "#3d5bd9",   # sélection (texte blanc dessus)
    "#3a2a2a": "#efd6d3",
    "#6c8cff": "#3d5bd9",   # accent
    "#4f6bed": "#3d5bd9",   # bouton principal (texte blanc)
    # textes : nettement plus foncés
    "#e6e6e6": "#12151b",
    "#f2f2f2": "#0d1016",
    "#ffffff": "#0d1016",   # textes « en cours » (les icônes blanches restent #fff)
    "#c8cdd8": "#2a303d",
    "#aab0bf": "#3a4152",
    "#9aa3b5": "#3f4656",
    "#8a90a0": "#4a5163",
    "#6f7686": "#555c6e",
    "#666": "#6b7180",
    "#555": "#7a808c",
    "#7a6060": "#8a5a5a",
    "#5c6375": "#6b7180",
    "#596074": "#8a91a0",
    "#8fb3ff": "#1d3fae",   # à venir
    "#7891c9": "#33447a",
    "#646a78": "#5f6573",   # déjà joué
    "#585e6b": "#6e7480",
    "#555b67": "#6e7480",
    "#ffd479": "#8a5400",   # prioritaire
}

# en clair, des bordures fines pour bien détacher listes, champs et boutons
LIGHT_EXTRA = """
QListWidget, QTableWidget, QLineEdit { border: 1px solid #b9c0cc; }
QPushButton { border: 1px solid #b9c0cc; }
QPushButton#play, QPushButton#next, QPushButton#stop, QPushButton#primary, QPushButton#flat { border: none; }
"""

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


def app_css(sheet: str) -> str:
    """Feuille de style générale de l'appli dans le thème actuel."""
    return css(sheet) + (LIGHT_EXTRA if _light else "")
