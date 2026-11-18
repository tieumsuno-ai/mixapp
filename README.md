# MixApp

Lecteur MP3 pour Mac et Windows qui enchaîne les morceaux comme un DJ, dans
l'esprit de Serato Pyro.

- **Un dossier = une playlist**, mise à jour automatiquement.
- **Enchaînements calés sur le tempo** (BPM, tonalité, intros / outros, « 1 »
  de la mesure), échange des basses, filtres ; sortie en écho quand les tempos
  sont trop éloignés.
- **Recherche instantanée** avec pastille de compatibilité avec le morceau en cours.
- **File prioritaire** : « Jouer ensuite » / « Ajouter à la file ».
- **Suivant** : sortie en écho (2 appuis rapides = coupe nette).
- Analyse automatique des morceaux, y compris à la volée pendant la lecture.

## Installation

1. Dézipper le dossier `mixapp` où tu veux (par exemple dans Documents).
2. **Mac** : double-clic sur **Installer MixApp (Mac).command**.
   La 1re fois, macOS peut refuser d'ouvrir un fichier téléchargé : fais
   clic droit (ou ctrl-clic) sur le fichier > **Ouvrir** > **Ouvrir**.
   **Windows** : double-clic sur **Installer MixApp (Windows).bat**.
3. S'il manque Python, l'installateur ouvre la page de téléchargement :
   installe **Python 3.12**, puis relance l'installateur.
4. Compter 5 à 10 minutes la 1re fois. L'appli s'ouvre ensuite toute seule ;
   tu la retrouves dans **Applications** (Mac) ou sur le **Bureau** (Windows).

Au 1er lancement, MixApp demande ton dossier de musique (par exemple
« Ma playlist soirée ») puis analyse les morceaux en arrière-plan : on peut
lancer la lecture sans attendre.

## Dans l'application

- Choisir une playlist à gauche, un morceau, puis **▶ Lancer la playlist ici**.
- Taper dans la barre de recherche, choisir un résultat puis **Jouer ensuite**
  ou **Ajouter à la file**. Pastille verte : s'enchaîne parfaitement ;
  orange : tempo un peu différent ; rouge : enchaînement en écho.
- Colonne **À suivre** : 🔒 déjà préparé (environ 25 s avant la transition),
  ★ file prioritaire (↑ ↓ pour réordonner, Retirer), puis la playlist.
- **⏭ Suivant** : sortie en écho ; deux appuis rapides : coupe nette.
- **■ Stop** (rouge) : arrêt en douceur, le morceau part dans un écho qui
  s'éteint en ~3 s ; deux appuis rapides : arrêt immédiat.
- Dans la liste : morceaux **pas encore joués en bleu**, **déjà joués grisés**,
  morceau **en cours en gras** avec ▶.
- **Compteur** : durée de la playlist en mix (en-tête), temps restant, nombre
  de morceaux et heure de fin estimée (lecteur). Les superpositions des
  transitions sont prises en compte.
- **Jaquettes** : celle du MP3 si c'est une vraie pochette ; si c'est le logo
  d'un « remix pool » (même image dans plusieurs morceaux) ou s'il n'y en a
  pas, MixApp cherche la pochette sur internet (catalogues Apple iTunes puis
  Deezer ; seuls l'artiste et le titre sont envoyés) et la garde en mémoire.
  Pour désactiver la recherche en ligne : `"covers_online": false` dans
  `settings.json` (dossier de données de l'appli).

Raccourcis : ⌘F (Ctrl+F) recherche · Entrée = Jouer ensuite · ⌘Entrée
(Ctrl+Entrée) = Ajouter à la file · ⌘→ (Ctrl+→) ou touche « piste suivante »
du clavier = Suivant · Suppr = retirer de la file.

## Ligne de commande (pour les tests)

```bash
python -m mixapp scan ~/Music                # scanne + analyse (Windows : C:\Users\toi\Music)
python -m mixapp playlists                   # liste des playlists
python -m mixapp show "House"                # morceaux d'une playlist
python -m mixapp search beyonce              # recherche libre
python -m mixapp compatible 42               # ce qui s'enchaîne bien après le morceau n°42
python -m mixapp watch ~/Music               # surveille les dossiers en continu
python -m mixapp play "House"                # LECTURE avec enchaînements automatiques
python -m mixapp mix "House" -o mix.mp3      # exporte un mix dans un fichier
```

### Pendant la lecture (`play`)

Taper la commande puis Entrée :

| Commande  | Effet                                                              |
|-----------|--------------------------------------------------------------------|
| (Entrée)  | morceau en cours, position, morceau suivant                        |
| `n`       | morceau suivant avec sortie en écho ; `n` une 2e fois pendant l'écho = coupe nette |
| `s texte` | recherche (mêmes filtres que `search`), avec pastille de compatibilité avec le morceau en cours |
| `j 3`     | « Jouer ensuite » : le résultat n°3 passe juste après le morceau en cours |
| `a 3`     | « Ajouter à la file » : le résultat n°3 va en fin de file prioritaire |
| `f`       | file d'attente (🔒 = déjà préparé, ★ = file prioritaire)           |
| `r 2`     | retirer le n°2 de la file prioritaire                              |
| `q`       | quitter                                                            |

### Exporter un mix (`mix`)

Pratique pour écouter les transitions à tête reposée ou tester des réglages :

```bash
python -m mixapp mix "House" -o mix.mp3 --minutes 15
python -m mixapp mix "House" -o test.mp3 --skip-at 40 95 --queue 12@20 7@25:a
```
`--skip-at` simule la touche suivant aux secondes indiquées ; `--queue 12@20`
fait « Jouer ensuite » le morceau n°12 à 20 s, `7@25:a` l'ajoute à la file à 25 s.

## Comment s'enchaînent les morceaux

Le type de transition est choisi automatiquement :

- **Mix calé** (écart de tempo ≤ 8 %), à la manière d'un DJ : la superposition
  commence sur une frontière de phrase (début de l'outro du morceau sortant) et
  dure autant que l'intro du morceau entrant (8 à 32 mesures), pour que son
  « drop » tombe pile à la fin de la transition. Pendant la superposition, le
  tempo glisse en douceur d'un morceau à l'autre, les kicks restant calés (à
  ~10 ms près, mesurés sur les vrais kicks à cet endroit). Le morceau entrant
  arrive sans ses basses, les basses s'échangent à mi-parcours, et le morceau
  sortant s'efface sur la dernière phrase avec un filtre qui s'ouvre. Hors des
  transitions, chaque morceau joue à son tempo d'origine : les voix ne sont
  jamais accélérées.
- **Sortie en écho** (tempos trop éloignés, ex. house → hip-hop) : le morceau en
  cours part dans un écho calé sur son tempo à la fin de son corps, le suivant
  démarre sur le temps d'après.
- **Fondu enchaîné** : seulement pour un morceau pas encore analysé.

Le morceau suivant est figé environ 25 s avant le début de la transition, le temps
de la préparer. Un « Jouer ensuite » fait après ce moment passe au morceau d'après.

Les volumes sont égalisés automatiquement d'un morceau à l'autre, sans jamais
écrêter un morceau peu compressé.

L'analyse repère aussi le « 1 » de chaque mesure (changements d'accords, débuts
de sections), pour que les transitions tombent sur les phrases musicales.

### Syntaxe de recherche (combinable)

| Exemple             | Effet                                              |
|---------------------|----------------------------------------------------|
| `daft pun`          | texte libre ; le dernier mot est un préfixe        |
| `bpm:128`           | tempo à ±1 BPM                                     |
| `bpm:120-128`       | plage de tempo                                     |
| `cle:8A` / `key:Am` | tonalité (Camelot ou nom)                          |
| `genre:house`       | filtre sur un champ (aussi `artiste:`, `titre:`, `album:`) |

La recherche porte sur le titre, l'artiste, l'album, le genre, le nom du fichier
et le nom du dossier.

La base de données est rangée dans :
- Mac : `~/Library/Application Support/MixApp/library.db`
- Windows : `%APPDATA%\MixApp\library.db`

## Fiabilité mesurée

`tools/make_test_tracks.py` génère 8 morceaux de test au tempo et à la tonalité
connus (88 à 128 BPM, majeur et mineur), et `tools/check_analysis.py` compare
l'analyse à la vérité :

- BPM : 8/8 exacts
- tonalité : 8/8 exactes
- grille rythmique : à ±3 ms du vrai kick
- intro / outro : à la mesure près, 8/8

Sur 22 vrais morceaux de soirée (extended mixes, latino, années 80) : tous les
tempos sont cohérents (Billie Jean 116,8, Uptown Funk 115, I Gotta Feeling 128…),
les tonalités vérifiables sont justes (Billie Jean Fa# mineur, Sweet Dreams
Do mineur, I Gotta Feeling Sol, Uptown Funk Ré mineur), et les intros « DJ » de
8 ou 16 mesures sont repérées grâce à l'absence de voix et de mélodie.

Les morceaux de test synthétiques sont, eux, faciles. Sur de la vraie musique, le BPM
sera très fiable sur l'électro, la house et le hip-hop, moins sur la musique
jouée live ; la tonalité se trompera plus souvent (de l'ordre de 70 à 80 % de
réussite, comme les logiciels de DJ du commerce). L'appli permettra de corriger
à la main.

Vitesse : environ 6 s par morceau et par cœur de processeur. Sur une machine
récente à 8 cœurs, 1 000 morceaux s'analysent en quelques minutes, une seule fois.

Transitions (mesuré sur les mêmes morceaux) : les kicks des deux morceaux
tombent ensemble à quelques millisecondes près pendant la superposition, le tempo
glisse sans à-coup, aucun clic aux raccords.

## Prochaines étapes

1. ~~Bibliothèque, playlists par dossier, analyse, recherche~~ ✅
2. ~~Moteur de lecture et transitions, file prioritaire, touche suivant en écho~~ ✅
3. ~~Interface graphique et installateurs Mac / Windows~~ ✅

## Note de licence

pedalboard est sous licence GPLv3. Aucun problème pour un usage personnel ; si
l'appli devait un jour être distribuée, elle devrait l'être sous GPLv3 aussi.
