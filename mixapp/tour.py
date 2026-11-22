"""Visite guidée automatique pour fabriquer les tutoriels : l'appli se pilote
toute seule et enregistre des captures de sa fenêtre (MIXAPP_TOUR=dossier).
Utilisé seulement par GitHub Actions, avec la musique de démonstration."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def run(w, out: str, playlist: str = "Soirée Disco", search: str = "playa"):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = [0]
    log = []

    WIDGETS = ["search_box", "btn_folders", "btn_full", "playlist_list", "table", "btn_play_here",
               "btn_play_next", "btn_enqueue", "queue_list", "btn_up", "btn_down", "btn_remove",
               "cover", "now_title", "now_info", "fact_label", "counter", "transition_label",
               "progress", "remain_label", "btn_play", "btn_stop33", "btn_pullup", "btn_next",
               "btn_hl", "btn_dj", "volume", "eq", "mix_badge", "status", "tracks_title",
               "btn_view", "theme_switch", "mix_len", "mix_len_label", "dupes_btn"]

    def geometry():
        g = {}
        for name in WIDGETS:
            wd = getattr(w, name, None)
            if wd is None or not wd.isVisible():
                continue
            p = wd.mapTo(w, wd.rect().topLeft())
            g[name] = [p.x(), p.y(), wd.width(), wd.height()]
        vp = w.table.viewport()
        for r in range(min(w.table.rowCount(), 6)):
            rect = w.table.visualRect(w.table.model().index(r, 1)).united(
                w.table.visualRect(w.table.model().index(r, w.table.columnCount() - 1)))
            p = vp.mapTo(w, rect.topLeft())
            g[f"row{r}"] = [p.x(), p.y(), rect.width(), rect.height()]
        vq = w.queue_list.viewport()
        for r in range(min(w.queue_list.count(), 4)):
            rect = w.queue_list.visualItemRect(w.queue_list.item(r))
            p = vq.mapTo(w, rect.topLeft())
            g[f"queue{r}"] = [p.x(), p.y(), rect.width(), rect.height()]
        d = getattr(w, "deck", None)
        if d is not None and d.isVisible():                 # vue Platines
            def put(key, wd):
                if wd is not None and wd.isVisible():
                    p = wd.mapTo(w, wd.rect().topLeft())
                    g[key] = [p.x(), p.y(), wd.width(), wd.height()]
            put("deck", d)
            put("deck_wave", d.wave)
            put("deck_xf", d.xf)
            put("deck_next", d.next_label)
            put("deck_jog0", d.jogs[0])
            put("deck_jog1", d.jogs[1])
            put("deck_mode", d.mode_box)
            put("deck_drawer", d.drawer_btn)
            for k, b in d.style_pads.items():
                put(f"pad_{k}", b)
            for i in range(2):
                r = d._lay(i)["r"]
                p = d.mapTo(w, r.topLeft())
                g[f"deck_cover{i}"] = [p.x(), p.y(), r.width(), r.height()]
            pads = [g[k] for k in g if k.startswith("pad_")]
            if pads:
                x0 = min(a[0] for a in pads); y0 = min(a[1] for a in pads)
                x1 = max(a[0] + a[2] for a in pads); y1 = max(a[1] + a[3] for a in pads)
                g["pads"] = [x0, y0, x1 - x0, y1 - y0]
        vpl = w.playlist_list.viewport()
        for r in range(w.playlist_list.count()):
            rect = w.playlist_list.visualItemRect(w.playlist_list.item(r))
            p = vpl.mapTo(w, rect.topLeft())
            g[f"pl_{w.playlist_list.item(r).text().split(chr(10))[0]}"] = [p.x(), p.y(), rect.width(), rect.height()]
        return g

    def shot(name):
        import json
        n[0] += 1
        QApplication.processEvents()
        base = out_dir / f"{n[0]:02d}_{name}"
        w.grab().save(str(base) + ".png")
        (Path(str(base) + ".json")).write_text(json.dumps(geometry(), ensure_ascii=False), encoding="utf-8")
        log.append(name)

    def shot_dialog(name, dlg):
        """Capture d'une fenêtre de dialogue posée au centre de la fenêtre assombrie."""
        import json
        from PySide6.QtGui import QColor, QPainter, QPixmap
        n[0] += 1
        QApplication.processEvents()
        base = w.grab()
        k = base.devicePixelRatio()
        img = dlg.grab()
        out = QPixmap(base.size())
        out.setDevicePixelRatio(k)
        p = QPainter(out)
        p.drawPixmap(0, 0, base)
        p.fillRect(0, 0, int(base.width() / k), int(base.height() / k), QColor(0, 0, 0, 120))
        x = int((base.width() / k - img.width() / k) / 2)
        y = int((base.height() / k - img.height() / k) / 2.6)
        p.drawPixmap(x, y, img)
        p.end()
        path = out_dir / f"{n[0]:02d}_{name}"
        out.save(str(path) + ".png")
        g = geometry()
        g["dialog"] = [x, y, int(img.width() / k), int(img.height() / k)]
        from PySide6.QtWidgets import QPushButton
        for b in dlg.findChildren(QPushButton):
            if b.isVisible():
                pt = b.mapTo(dlg, b.rect().topLeft())
                g["dlg_" + b.text().strip()] = [x + pt.x(), y + pt.y(), b.width(), b.height()]
        (Path(str(path) + ".json")).write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        log.append(name)

    def multi_select():
        from PySide6.QtCore import QItemSelectionModel
        sm = w.table.selectionModel()
        sm.clearSelection()
        for r in (1, 2):
            sm.select(w.table.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def open_later(fn):
        QTimer.singleShot(0, fn)

    def analysed():
        try:
            n_ok, tot = w.conn.execute("select count(bpm), count(*) from tracks").fetchone()
            return tot > 0 and n_ok >= tot
        except Exception:
            return False

    def select_playlist(name):
        for i in range(w.playlist_list.count()):
            if name in w.playlist_list.item(i).text():
                w.playlist_list.setCurrentRow(i)
                return

    def mixing(min_frac=0.35):
        eng = w.engine
        st = eng.transition_state() if eng else None
        return bool(st and st[1] >= min_frac)

    # (condition d'attente ou délai, action, capture)
    steps = [
        (analysed, None, None),
        (4.0, None, "fenetre"),
        (0.2, lambda: select_playlist(playlist), None),
        (1.5, None, "playlist"),
        (0.2, lambda: (w.table.selectRow(0), w.play_from_selection()), None),
        (7.0, None, "lecture"),
        (0.2, lambda: w.search_box.setText(search), None),
        (1.5, None, "recherche"),
        (0.2, lambda: w.table.selectRow(0), None),
        (0.8, None, "recherche_selection"),
        (0.2, lambda: w.queue_selected(front=True), None),
        (1.5, None, "jouer_ensuite"),
        (0.2, lambda: w.search_box.setText(""), None),
        (0.2, lambda: select_playlist(playlist), None),
        (1.5, lambda: w.btn_dj.setChecked(True), None),
        (2.0, None, "flow_dj"),
        (0.2, lambda: w.btn_hl.setChecked(True), None),
        (3.0, None, "temps_forts"),
        (0.2, lambda: w._on_seek(0.55), None),
        (1.0, None, "timeline"),
        (lambda: mixing(0.35), None, "mix_en_cours"),
        (4.0, None, "mix_en_cours_2"),
        (lambda: not mixing(0.0), None, None),
        # nouveautés : sélection multiple, réglette, vue Platines, pads, thème, doublons, fermeture
        (0.2, multi_select, None),
        (1.0, None, "selection_multiple"),
        (0.2, lambda: w.mix_len.setValue(8), None),
        (1.2, None, "reglette"),
        (0.2, lambda: w.btn_view.setChecked(True), None),
        (4.0, None, "platines"),
        (0.2, lambda: w.deck.style_pads["echo"].click(), None),
        (2.0, None, "pads"),
        (0.2, lambda: w._on_seek(0.80), None),
        (lambda: mixing(0.30), None, "platines_mix"),
        (lambda: not mixing(0.0), None, None),
        (0.2, lambda: w.btn_view.setChecked(False), None),
        (0.2, lambda: w.theme_switch.setLight(True), None),
        (2.0, None, "theme_clair"),
        (0.2, lambda: w.theme_switch.setLight(False), None),
        (1.0, lambda: w.check_dupes(), None),
        (1.0, None, "doublons_lien"),
        (0.2, lambda: open_later(w.show_dupes), None),
        (lambda: getattr(w, "_dupes_dialog", None) is not None, None, None),
        (1.5, None, None),
        (0.1, lambda: shot_dialog("doublons", w._dupes_dialog), None),
        (0.2, lambda: w._dupes_dialog.reject(), None),
        (0.6, lambda: open_later(w.close), None),
        (lambda: getattr(w, "_quit_dialog", None) is not None, None, None),
        (1.2, None, None),
        (0.1, lambda: shot_dialog("fermeture", w._quit_dialog), None),
        (0.2, lambda: w._quit_dialog.reject(), None),
        (12.0, lambda: w.play_pause(), None),
        (0.8, None, "pause_echo"),
        (6.0, None, "en_pause"),
        (0.2, lambda: w.play_pause(), None),
        (3.0, None, "reprise"),
        (0.2, lambda: w.styled_stop("turntable"), None),
        (0.7, None, "stop_33"),
        (6.0, None, "arrete"),
    ]
    state = {"i": 0, "t0": time.time(), "acted": False}

    def tick():
        i = state["i"]
        if i >= len(steps):
            (out_dir / "journal.txt").write_text("\n".join(log), encoding="utf-8")
            QApplication.quit()
            return
        cond, action, name = steps[i]
        if not state["acted"]:
            if action:
                try:
                    action()
                except Exception as e:                     # on continue la visite
                    log.append(f"⚠ étape {i} : {e}")
            state["acted"], state["t0"] = True, time.time()
        waited = time.time() - state["t0"]
        done = (waited >= cond) if isinstance(cond, float) else (cond() or waited > 240)
        if done:
            if name:
                shot(name)
            state["i"] += 1
            state["acted"] = False
        QTimer.singleShot(250, tick)

    w.resize(1600, 980)
    QTimer.singleShot(1500, tick)
