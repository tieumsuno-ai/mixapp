"""Point d'entrée. Sans argument : l'application. Avec : la ligne de commande.

    python -m mixapp                         ouvre l'application

    python -m mixapp scan ~/Musique          scanne + analyse
    python -m mixapp playlists               liste les playlists (1 dossier = 1 playlist)
    python -m mixapp show "Nom du dossier"   morceaux d'une playlist
    python -m mixapp search "bpm:120-128 house"
    python -m mixapp compatible 42           morceaux qui s'enchaînent bien après le n°42
    python -m mixapp watch ~/Musique         surveille les dossiers en continu
    python -m mixapp play "House"            lecture avec enchaînements (n = suivant, h = aide)
    python -m mixapp mix "House" -o mix.mp3  exporte un mix dans un fichier
"""

from __future__ import annotations

import argparse
import sys
import time

from . import analysis, db, library, search

COLORS = {"vert": "\033[32m●\033[0m", "orange": "\033[33m●\033[0m",
          "rouge": "\033[31m●\033[0m", "inconnu": "○"}


def fmt_time(s):
    if s is None:
        return "  -  "
    return f"{int(s // 60)}:{int(s % 60):02d}"


def print_tracks(rows, extra=None):
    for i, r in enumerate(rows):
        bpm = f"{r['bpm']:6.1f}" if r["bpm"] else "     -"
        key = f"{r['key_name'] or '-':>4} {r['camelot'] or '-':>3}"
        struct = f"intro→{fmt_time(r['intro_end'])} outro→{fmt_time(r['outro_start'])}"
        dot = (COLORS[extra[i]] + " ") if extra else ""
        print(f"{dot}[{r['id']:>4}] {bpm} BPM  {key}  {fmt_time(r['duration'])}  {struct}  "
              f"{r['artist'] or '?'} — {r['title']}")


def progress_bar(done, total):
    w = 30
    fill = int(w * done / total)
    sys.stdout.write(f"\r  Analyse : [{'#' * fill}{'.' * (w - fill)}] {done}/{total}")
    sys.stdout.flush()
    if done == total:
        print()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args == ["gui"]:                 # sans argument : l'application
        from .gui import main as gui_main
        return gui_main()
    p = argparse.ArgumentParser(prog="mixapp")
    p.add_argument("--db", help="chemin de la base (défaut : dossier de données utilisateur)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("folders", nargs="+")
    s.add_argument("--force", action="store_true", help="réanalyser tous les morceaux")
    sub.add_parser("playlists")
    sub.add_parser("covertest", help="vérifie la recherche de pochettes sur internet")
    sub.add_parser("vocaltest", help="vérifie la détection du chant (modèle embarqué)")
    sub.add_parser("infotest", help="vérifie les infos « le saviez-vous ? » (Wikipédia, MusicBrainz)")
    s = sub.add_parser("show"); s.add_argument("name")
    s = sub.add_parser("search"); s.add_argument("query", nargs="+")
    s = sub.add_parser("compatible"); s.add_argument("track_id", type=int)
    s = sub.add_parser("watch"); s.add_argument("folders", nargs="+")
    s = sub.add_parser("play"); s.add_argument("name")
    s.add_argument("--shuffle", action="store_true", help="ordre aléatoire")
    s = sub.add_parser("mix"); s.add_argument("name")
    s.add_argument("-o", "--out", default="mix.mp3", help="fichier de sortie (.mp3 ou .wav)")
    s.add_argument("--minutes", type=float, default=10)
    s.add_argument("--shuffle", action="store_true")
    s.add_argument("--skip-at", type=float, nargs="*", default=[],
                   help="secondes où simuler la touche suivant (ex. 40 95)")
    s.add_argument("--queue", nargs="*", default=[],
                   help="ID@secondes = jouer ensuite, ID@secondes:a = ajouter à la file")
    a = p.parse_args(args)

    if a.cmd == "infotest":
        from . import trackinfo
        cases = [("Michael Jackson", "Billie Jean (Extended Mix) [www.fuvi-clan.com]"),
                 ("Lucenzo & Don Omar", "Danza Kuduro (Edit Mix)"),
                 ("Céline Dion", "J'irai Où Tu Iras (Extended Mix By Fuvi Clan)"),
                 ("Ottawan", "D.I.S.C.O (Extended Mix By Fuvi Clan)"),
                 ("Les Paillettes", "Boule à facettes")]
        ok = 0
        for art, tit in cases:
            info = trackinfo.lookup(art, tit)
            ok += bool(info.get("fact") or info.get("year"))
            print(f"{tit[:30]:30} → {info}")
        return 0 if ok >= 3 else 1

    if a.cmd == "vocaltest":
        import time
        import numpy as np
        from . import vocals
        print("modèle :", vocals.MODEL, vocals.MODEL.exists(), "| disponible :", vocals.available())
        t0 = time.time()
        x = (np.random.default_rng(0).standard_normal((44100 * 20, 2)) * 0.05).astype(np.float32)
        act = vocals.activity(x)
        print(f"carte du chant : {len(act)} tranches en {time.time() - t0:.1f} s")
        return 0 if len(act) > 50 else 1

    if a.cmd == "covertest":
        from . import covers
        cases = [("Euryhmics", "Sweet Dreams [www.fuvi-clan.com]"),
                 ("Stef Konstan", "Bruno Mars - 24K Magic (Stef Konstan Edit)"),
                 ("Jungeli, Abou Debeing, Alonzo, Imen Es & Lossa", "Petit Génie (Extended Mix)"),
                 ("Tropic Music", "Talina - Ca va bouger (feat. Meiway, Décibel & Big Tom)"),
                 ("Decibel, Dr, Dr", "Ambiance tropicale, Kiss Kiss")]
        found = 0
        for art, tit in cases:
            try:
                url = covers.search_online(art, tit)
            except Exception as e:
                url = f"ERREUR {e}"
            found += bool(url and url.startswith("http"))
            print(f"pochette : {tit[:40]:40} → {url}")
        print(f"{found}/{len(cases)} trouvées")
        return 0 if found >= 3 else 1

    conn = db.connect(a.db)

    if a.cmd == "scan":
        t = time.time()
        r = library.scan(conn, a.folders)
        print(f"Scan : {r.added} ajoutés, {r.updated} modifiés, {r.removed} supprimés, "
              f"{r.unchanged} inchangés")
        errs = analysis.analyze_library(conn, force=a.force, progress=progress_bar)
        for e in r.errors + errs:
            print("  ⚠", e)
        print(f"Terminé en {time.time() - t:.1f} s")

    elif a.cmd == "playlists":
        for pl in library.playlists(conn):
            print(f"{pl['name']:<40} {pl['count']:>4} morceaux  {fmt_time(pl['duration']):>7}"
                  f"  ({pl['analyzed']}/{pl['count']} analysés)")

    elif a.cmd == "show":
        match = [pl for pl in library.playlists(conn) if pl["name"].lower() == a.name.lower()]
        if not match:
            sys.exit(f"Playlist introuvable : {a.name}")
        print_tracks(library.playlist_tracks(conn, match[0]["folder"]))

    elif a.cmd == "search":
        print_tracks(search.search(conn, " ".join(a.query)))

    elif a.cmd == "compatible":
        res = search.compatible_tracks(conn, a.track_id)
        print_tracks([r for r, _ in res], [c for _, c in res])

    elif a.cmd == "play":
        from .player_cli import run_play
        run_play(conn, a.name, a.shuffle)

    elif a.cmd == "mix":
        from .player_cli import run_mix
        run_mix(conn, a.name, a.out, a.minutes, a.shuffle, a.skip_at, a.queue)

    elif a.cmd == "watch":
        library.scan(conn, a.folders)
        analysis.analyze_library(conn, progress=progress_bar)

        def changed(res):
            c = db.connect(a.db)
            print(f"Changement détecté : +{res.added} ~{res.updated} -{res.removed}")
            analysis.analyze_library(c, progress=progress_bar)
            c.close()

        obs = library.watch(lambda: db.connect(a.db), a.folders, changed)
        print("Surveillance active (Ctrl+C pour arrêter)…")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            obs.stop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
