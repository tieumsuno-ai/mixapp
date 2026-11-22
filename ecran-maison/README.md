# Écran maison

Page d'affichage pour une vieille tablette fixée au mur (testée sur iPad Air / iOS 12) :
heure, date, fête du jour, éphéméride, météo animée avec pluie dans les 2 heures,
collecte des déchets, infos qui défilent (football, vacances scolaires, jours fériés,
qualité de l'air, durée du jour), température intérieure Netatmo et télécommande Sonos
(tuile « En écoute » pendant la lecture).

- `index.html` : la page (JavaScript ES5, sans dépendance). Elle lit ses réglages dans
  `config.js`, un fichier **local** qui n'est pas publié ici.
- `relais.py` : programme qui tourne sur le NAS. Il met la page à jour toutes les 15 minutes
  et, si des codes Netatmo sont fournis, écrit `interieur.json`. Il expose aussi une petite API
  (port 8099) qui pilote les enceintes Sonos du réseau local (variable `SONOS_RESEAU`, ex. `192.168.1.0/24`).

Données : [Open-Meteo](https://open-meteo.com) (météo, qualité de l'air),
[TheSportsDB](https://www.thesportsdb.com) (football), API Netatmo.
