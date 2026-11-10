# -*- coding: utf-8 -*-
"""
edit_changeID.py – zmena [RECORDID] polygónu vrstvy tblHabHlavna
v MapovaniePrePS.gpkg aj vo všetkých podriadených tabuľkách.

Použitie:
    python edit_changeID.py <cesta_ku_gpkg> <FID> <nové_RECORDID>

    <cesta_ku_gpkg>   plná cesta k MapovaniePrePS.gpkg (napr. v priečinku
                      C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\)
    <FID>             [fid] polygónu v tblHabHlavna, ktorému sa mení RECORDID
                      (polygón sa hľadá podľa fid, lebo RECORDID môže byť
                      duplicitné)
    <nové_RECORDID>   nová hodnota [RECORDID] (celé číslo); nesmie ju mať
                      žiadny iný polygón

Zmení sa:
  - [RECORDID] polygónu v tblHabHlavna
  - [fkRECORDID] v tblHabBiotopy, tblHabDruhy a tblAktivity
  - [polygon_id_form] ostatných polygónov, ktoré odkazovali na staré RECORDID
tblHabBiotopyOpatrenia sa viaže na [id] biotopu, preto sa nemení.

Ak staré RECORDID má aj iný polygón (duplicita), nedá sa určiť, ktorému
polygónu patria podriadené záznamy – skript sa opýta, či ich presunúť na
nové RECORDID, alebo ponechať pri druhom polygóne.

Pred zápisom vypíše plán a po potvrdení zapíše zmeny (pred zápisom sa
vytvorí záloha MapovaniePrePS_backup_YYYYMMDD_HHMMSS.gpkg). Fotky v
podpriečinku "f" sa nepremenúvajú – skript len vypíše tie, ktorých názov
začína starým RECORDID.

Vyžaduje: osgeo (GDAL) – interpreter s GDAL, napr.
  C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
"""

import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

# slovenské znaky aj v konzole s cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HLAVNA = "tblHabHlavna"
BIOTOPY = "tblHabBiotopy"
DRUHY = "tblHabDruhy"
AKTIVITY = "tblAktivity"
CHILDREN = (BIOTOPY, DRUHY, AKTIVITY)   # tabuľky s [fkRECORDID]

PHOTO_DIR = "f"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def ask(prompt, default=None):
    """Vstup z konzoly; Enter vráti predvolenú hodnotu."""
    while True:
        try:
            s = input(prompt).strip()
        except EOFError:
            sys.exit("\nPrerušené – žiadne zmeny neboli zapísané.")
        if s or default is None:
            return s
        return default


def run_sql(ds, sql):
    res = ds.ExecuteSQL(sql)
    if res is not None:
        ds.ReleaseResultSet(res)


def refresh_feature_counts(gpkg):
    """GDAL po priamom SQL nastaví počet prvkov v gpkg_ogr_contents na NULL."""
    con = sqlite3.connect(gpkg)
    try:
        for (t,) in con.execute("SELECT table_name FROM gpkg_ogr_contents").fetchall():
            con.execute('UPDATE gpkg_ogr_contents SET feature_count = '
                        '(SELECT COUNT(*) FROM "%s") WHERE table_name = ?' % t, (t,))
        con.commit()
    finally:
        con.close()


def photos_with_recordid(photo_dir, recordid):
    """Obrázky v priečinku "f", ktorých prvé číslo v názve je RECORDID
    (rovnaké pravidlo ako v 04_validacia_foto.py)."""
    if recordid is None or not os.path.isdir(photo_dir):
        return []
    out = []
    for name in sorted(os.listdir(photo_dir)):
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
            continue
        m = re.search(r"\d+", name)
        if m and int(m.group()) == recordid:
            out.append(name)
    return out


# ----------------------------------------------------------------------
# Plán zmeny (len čítanie, čistý sqlite3)
# ----------------------------------------------------------------------

def make_plan(con, fid, new_id):
    """Zistí, čo sa zmení. Pri chybe vyhodí ValueError so správou."""
    row = con.execute("SELECT fid, RECORDID, polygon_id_form FROM %s WHERE fid = ?"
                      % HLAVNA, (fid,)).fetchone()
    if row is None:
        raise ValueError("Polygón fid=%s v %s neexistuje." % (fid, HLAVNA))
    old_id = row[1]
    if old_id is not None and int(old_id) == new_id:
        raise ValueError("Polygón fid=%s už má RECORDID=%s." % (fid, new_id))

    taken = [r[0] for r in con.execute(
        "SELECT fid FROM %s WHERE RECORDID = ? AND fid <> ?" % HLAVNA, (new_id, fid))]
    if taken:
        raise ValueError("RECORDID=%s už má polygón fid %s – zvoľ inú hodnotu."
                         % (new_id, ", ".join(str(t) for t in taken)))

    plan = {
        "fid": fid, "old": None if old_id is None else int(old_id), "new": new_id,
        "own_form": row[2], "shared": [], "children": {}, "form_refs": [],
        "orphans": {},
    }
    for t in CHILDREN:
        plan["orphans"][t] = con.execute(
            "SELECT COUNT(*) FROM %s WHERE fkRECORDID = ?" % t, (new_id,)).fetchone()[0]
    if plan["old"] is None:
        return plan

    plan["shared"] = [r[0] for r in con.execute(
        "SELECT fid FROM %s WHERE RECORDID = ? AND fid <> ? ORDER BY fid" % HLAVNA,
        (plan["old"], fid))]
    for t in CHILDREN:
        plan["children"][t] = con.execute(
            "SELECT COUNT(*) FROM %s WHERE fkRECORDID = ?" % t, (plan["old"],)).fetchone()[0]
    plan["form_refs"] = [(r[0], r[1]) for r in con.execute(
        "SELECT fid, RECORDID FROM %s WHERE polygon_id_form = ? AND fid <> ? ORDER BY fid"
        % HLAVNA, (plan["old"], fid))]
    return plan


def write_sql(plan, move_children=True):
    """SQL príkazy zmeny v poradí, v akom sa vykonajú."""
    sql = []
    old, new = plan["old"], plan["new"]
    if old is not None and move_children:
        for t in CHILDREN:
            sql.append("UPDATE %s SET fkRECORDID = %d WHERE fkRECORDID = %d" % (t, new, old))
        sql.append("UPDATE %s SET polygon_id_form = %d WHERE polygon_id_form = %d AND fid <> %d"
                   % (HLAVNA, new, old, plan["fid"]))
    sql.append("UPDATE %s SET RECORDID = %d WHERE fid = %d" % (HLAVNA, new, plan["fid"]))
    return sql


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def parse_args(argv):
    if len(argv) != 4:
        sys.exit(__doc__)
    gpkg, fid_raw, new_raw = argv[1], argv[2].strip(), argv[3].strip()
    if not os.path.isfile(gpkg):
        sys.exit("Súbor neexistuje: %s" % gpkg)
    if not fid_raw.isdigit():
        sys.exit("Neplatné FID '%s' – očakávam celé číslo." % fid_raw)
    if not new_raw.isdigit():
        sys.exit("Neplatné nové RECORDID '%s' – očakávam kladné celé číslo." % new_raw)
    return gpkg, int(fid_raw), int(new_raw)


def main():
    gpkg, fid, new_id = parse_args(sys.argv)

    con = sqlite3.connect("file:%s?mode=ro" % gpkg.replace("\\", "/"), uri=True)
    try:
        plan = make_plan(con, fid, new_id)
    except ValueError as e:
        sys.exit(str(e))
    except sqlite3.Error as e:
        sys.exit("GPKG sa nedá prečítať (%s): %s" % (gpkg, e))
    finally:
        con.close()

    old = plan["old"]
    print("GPKG: %s" % gpkg)
    print("Polygón fid=%s: RECORDID %s -> %s"
          % (fid, "(prázdne)" if old is None else old, new_id))

    # ---- duplicitné staré RECORDID: komu patria podriadené záznamy? ----
    move_children = True
    has_links = any(plan["children"].values()) or plan["form_refs"]
    if plan["shared"] and has_links:
        print("\nUPOZORNENIE: RECORDID=%s má aj polygón fid %s – podriadené záznamy "
              "a odkazy [polygon_id_form] sa nedajú jednoznačne priradiť."
              % (old, ", ".join(str(s) for s in plan["shared"])))
        print("    1) ponechať ich pri RECORDID=%s (ostanú pri druhom polygóne)" % old)
        print("    2) presunúť všetky na nové RECORDID=%s" % new_id)
        while True:
            s = ask("  Voľba [1]: ", "1")
            if s in ("1", "2"):
                break
            print("  Neplatná voľba.")
        move_children = (s == "2")

    # ---- plán ----
    print("\n================ PLÁN ZMENY RECORDID ================")
    print("%s: fid=%s  RECORDID %s -> %s"
          % (HLAVNA, fid, "(prázdne)" if old is None else old, new_id))
    if old is not None:
        verb = "presunie sa" if move_children else "ostane pri RECORDID=%s" % old
        for t in CHILDREN:
            print("%-22s %4d záznamov s fkRECORDID=%s – %s" % (t, plan["children"][t], old, verb))
        if plan["form_refs"]:
            print("%s [polygon_id_form]=%s má %d polygónov (RECORDID %s) – %s"
                  % (HLAVNA, old, len(plan["form_refs"]),
                     ", ".join(str(r) for _, r in plan["form_refs"]),
                     "zmení sa na %s" % new_id if move_children else "bez zmeny"))
    print("tblHabBiotopyOpatrenia – bez zmeny (viaže sa na [id] biotopu)")

    warnings = []
    orphans = {t: n for t, n in plan["orphans"].items() if n}
    if orphans:
        warnings.append("záznamy bez polygónu s fkRECORDID=%s sa priradia k tomuto polygónu: %s"
                        % (new_id, ", ".join("%s %d" % (t, n) for t, n in orphans.items())))
    if plan["own_form"] is not None and int(plan["own_form"]) == new_id:
        warnings.append("[polygon_id_form] tohto polygónu je %s – bude odkazovať sám na seba."
                        % new_id)
    photo_dir = os.path.join(os.path.dirname(os.path.abspath(gpkg)), PHOTO_DIR)
    photos = photos_with_recordid(photo_dir, old)
    if photos:
        warnings.append("fotky so starým RECORDID v %s sa NEpremenujú: %s"
                        % (photo_dir, ", ".join(photos)))
    for w in warnings:
        print("UPOZORNENIE: " + w)

    if ask("\nZapísať zmeny do GPKG? [ano/nie]: ", "nie").lower() not in ("ano", "a", "y", "yes"):
        print("Zrušené – žiadne zmeny neboli zapísané.")
        return

    # ---- zápis ----
    from osgeo import ogr
    ogr.UseExceptions()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.splitext(gpkg)[0] + "_backup_" + stamp + ".gpkg"
    shutil.copy2(gpkg, backup)
    print("Záloha vytvorená: %s" % backup)

    try:
        ds = ogr.Open(gpkg, 1)
    except RuntimeError as e:
        sys.exit("GPKG sa nedá otvoriť na zápis (nie je otvorený v QGIS?): %s" % e)
    ds.StartTransaction()
    try:
        for sql in write_sql(plan, move_children):
            run_sql(ds, sql)
        ds.CommitTransaction()
    except Exception as e:
        ds.RollbackTransaction()
        ds = None
        sys.exit("CHYBA pri zápise, zmeny vrátené späť: %s" % e)
    ds = None
    refresh_feature_counts(gpkg)

    print("HOTOVO: polygón fid=%s má RECORDID=%s%s."
          % (fid, new_id,
             "" if old is None or not move_children
             else " (podriadené záznamy a odkazy presunuté zo %s)" % old))


if __name__ == "__main__":
    main()
