# -*- coding: utf-8 -*-
"""
Vytvorí kópiu Access databázy (formulár mapovanie_biotopov) vedľa exportovaného
shapefilu a naplní ju údajmi z MapovaniePrePS.gpkg.

Zdroj údajov:  <cloud>\\SKUEV####\\MapovaniePrePS.gpkg (+ SKUEV####_FotoTable_*.txt)
Výstup:        <priečinok skriptu>\\export_for_SOP\\SKUEV####\\SKUEV####.accdb
               teda ten istý priečinok, do ktorého ukladá shapefile
               05_export_shapefile.py; ak neexistuje, vytvorí sa.

Postup:
  1. Zadá sa kód SKUEV#### (len veľkopísmenový priečinok v cloud adresári).
  2. Šablóna mapovanie_biotopov_7.accdb sa skopíruje do výstupného priečinka
     pod názvom SKUEV####.accdb (ak už existuje, pôvodná sa zazálohuje).
  3. Z MapovaniePrePS.gpkg sa naplnia tabuľky:
        tblHabHlavna, tblHabDruhy, tblHabBiotopy,
        tblHabBiotopyOpatrenia, tblAktivity
     Väzby rodič–dieťa (RECORDID/fkRECORDID a tblHabBiotopy.id ->
     tblHabBiotopyOpatrenia.fkHabBiotopyID) sa zachovávajú.

Mapovanie stĺpcov tblHabHlavna (GPKG -> Access):
    RECORDID     -> RECORDID + gis_id_origin = "SKUEV####_N_XX_YYY"
                   (XX = id mapovateľa z tblMappers dopl. na 2 miesta podľa
                   [hlavny_mapovatel]; YYY = GPKG [RECORDID] dopl. na 3 miesta).
                   Tá istá zložená hodnota ide do všetkých cudzích kľúčov
                   fkRECORDID v podriadených tabuľkách (Druhy, Biotopy, Aktivity,
                   Fotky), aby väzby rodič–dieťa ostali zachované.
                   Pozn.: YYY sa berie z [RECORDID], nie z [polygon_id] – ten sa
                   môže v rámci územia opakovať a pokazil by väzby.
    KOD_UEV      -> fldskuevcode
    p            -> plocha
    geom         -> wktgeom            (geometria ako WKT)
    geom centroid-> x_karto, y_karto   (natívne súradnice EPSG:5514)
    datum        -> datum, rok         (rok = rok z dátumu)
    typ_gis_prvku= "P"                 (konštanta)
    ostatné rovnomenné stĺpce (lokalita, hlavny/druhy_mapovatel, poznamka,
    e0–e3, E1_invaz–E3_invaz) sa kopírujú priamo.

Prepojené formuláre ([polygon_id_form]):
  Údaje prepojeného polygónu sa NEKOPÍRUJÚ. Každý polygón sa vloží len so
  svojimi vlastnými údajmi a s odkazom na formulár, do ktorého boli jeho údaje
  zapísané:
    polygon_id_form -> zložené RECORDID zdrojového polygónu, teda
                       "SKUEV####_N_XX_YYY", aby sa dalo v Accesse spojiť
                       s tblHabHlavna.RECORDID (nie surové číslo z GPKG)
    typ_polygon     -> typ_polygon, 'A' / 'B' daného polygónu
  Zdroj sa hľadá podľa [RECORDID] = [polygon_id_form]; ak sa nenájde, skúsi sa
  [polygon_id] a zapíše sa upozornenie (reťazené odkazy sa sledujú až po koncový
  zdroj, s ochranou proti cyklu).

  Šablóna mapovanie_biotopov_7.accdb oba stĺpce má (od 2026-07-30). Ak by
  chýbali (staršia šablóna), doplnia sa cez ALTER TABLE do kópie
  SKUEV####.accdb – šablóna sa nikdy nemení.

tblHabBiotopy.[id]:
  V Accesse je [id] jedinečné v celej tabuľke (a odkazuje naň
  tblHabBiotopyOpatrenia.fkHabBiotopyID), v GPKG jedinečné nie je. Preto sa
  všetkým vkladaným biotopom prideľuje nové poradové [id] a opatrenia sa
  prepájajú na tieto nové hodnoty.

Vyžaduje: pyodbc + ovládač "Microsoft Access Driver (*.mdb, *.accdb)",
shapely. Spúšťať interpreterom, ktorý má tieto balíky (napr.
C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe).
"""

import glob
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

import pyodbc
import shapely.wkb

# slovenské znaky aj v konzole s cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
# výstup ide k shapefilu z 05_export_shapefile.py – musí to byť ten istý
# priečinok ako out_dir v 05 (priečinok skriptu\export_for_SOP\SKUEV####)
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "export_for_SOP")
GPKG_NAME = "MapovaniePrePS.gpkg"
TEMPLATE_ACCDB = r"C:\_projects\MapovaniePrePS\formular\mapovanie_biotopov_7.accdb"


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def ask_skuev():
    raw = (sys.argv[1] if len(sys.argv) > 1
           else input("Zadaj kód územia SKUEV (napr. SKUEV0862): ")).strip().upper()
    if re.fullmatch(r"\d{4}", raw):
        raw = "SKUEV" + raw
    if not re.fullmatch(r"SKUEV\d{4}", raw):
        sys.exit("Neplatný kód '%s' – očakávam tvar SKUEV#### (napr. SKUEV0862)." % raw)
    return raw


def find_folder(skuev):
    """Len veľkopísmenový priečinok SKUEV#### (nie 'skuev...')."""
    try:
        dir_names = os.listdir(CLOUD_DIR)
    except OSError as e:
        sys.exit("Priečinok %s sa nedá čítať: %s" % (CLOUD_DIR, e))
    if skuev not in dir_names:
        sys.exit("Priečinok %s neexistuje v %s (malé 'skuev...' sa ignoruje)."
                 % (skuev, CLOUD_DIR))
    return os.path.join(CLOUD_DIR, skuev)


def gpkg_wkb(blob):
    """WKB časť z GPKG geometrie, alebo None."""
    if blob is None or len(blob) < 8 or blob[0:2] != b"GP":
        return None
    flags = blob[3]
    env_counts = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
    env_code = (flags >> 1) & 0x07
    if env_code not in env_counts:
        return None
    return blob[8 + 8 * env_counts[env_code]:]


def geom_from_blob(blob):
    """shapely geometria z GPKG blobu, alebo None."""
    wkb = gpkg_wkb(blob)
    if not wkb:
        return None
    try:
        return shapely.wkb.loads(wkb)
    except Exception:
        return None


def is_empty(v):
    """None alebo prázdny/whitespace text."""
    return v is None or (isinstance(v, str) and not v.strip())


def parse_date(value):
    """(date, rok) z textu 'dd.mm.rrrr', inak (None, None)."""
    if value is None or str(value).strip() == "":
        return None, None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.strptime(str(value).strip(), fmt).date()
            return d, str(d.year)
        except ValueError:
            continue
    return None, None


def connect_access(path):
    return pyodbc.connect(
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=%s;" % path)


def ensure_columns(acc, table, columns):
    """Doplní chýbajúce stĺpce do tabuľky (ALTER TABLE). Vráti pridané názvy.

    Šablóna mapovanie_biotopov_7.accdb tieto stĺpce nemá; pridávajú sa len do
    kópie SKUEV####.accdb, šablóna sa nemení. Idempotentné."""
    cur = acc.cursor()
    cur.execute("SELECT * FROM [%s] WHERE 1=0" % table)
    existing = {c[0].lower() for c in cur.description}
    added = []
    for name, sql_type in columns:
        if name.lower() in existing:
            continue
        cur.execute("ALTER TABLE [%s] ADD COLUMN [%s] %s" % (table, name, sql_type))
        added.append(name)
    if added:
        acc.commit()
    return added


def load_mappers(acc):
    """{meno mapovateľa: id} z tblMappers."""
    result = {}
    for mid, name in acc.cursor().execute("SELECT ID, mapovatel FROM tblMappers"):
        if name is not None:
            result[str(name).strip()] = mid
    return result


def load_foto_rows(folder, skuev, rec_map, pid_map):
    """Riadky pre tblHabFotky z najnovšieho SKUEV####_FotoTable_*.txt.

    Súbor vytvára 04_validacia_foto.py. Stĺpce sa čítajú podľa hlavičky, aby
    sedeli aj staršie tabuľky (tie majú navyše 'new_name' a v prvom stĺpci
    'polygon_id' namiesto 'RECORDID'):
        RECORDID | polygon_id, original_name, [new_name], note
    Vráti (zoznam n-tíc pre tblHabFotky, cesta_k_súboru | None,
    množina nenamapovaných kľúčov).
    Mapovanie: fkRECORDID = zložené RECORDID (SKUEV####_N_XX_YYY) podľa
    [RECORDID] cez `rec_map` (staršie tabuľky podľa [polygon_id] cez `pid_map`);
    fotoFileName = new_name, ak v tabuľke je, inak original_name;
    fotoFileNameOriginal = original_name, fotoNote = note.
    """
    pattern = os.path.join(folder, skuev + "_FotoTable_*.txt")
    files = glob.glob(pattern)
    if not files:
        return [], None, set()
    latest = max(files, key=os.path.getmtime)
    rows = []
    unmatched = set()
    with open(latest, encoding="utf-8") as f:
        header = [h.strip().lower() for h in f.readline().rstrip("\n").split("\t")]
        col = {name: i for i, name in enumerate(header)}
        # prvý stĺpec: RECORDID (nové tabuľky) alebo polygon_id (staršie)
        by_recordid = "recordid" in col
        key_idx = col.get("recordid", col.get("polygon_id", 0))
        key_map = rec_map if by_recordid else pid_map

        def value(parts, name):
            i = col.get(name)
            return "" if i is None or i >= len(parts) else parts[i].strip()

        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            key = parts[key_idx].strip() if key_idx < len(parts) else ""
            fk_recordid = key_map.get(key)
            if key and fk_recordid is None:
                unmatched.add(key)
            original_name = value(parts, "original_name")
            new_name = value(parts, "new_name") or original_name
            rows.append((
                fk_recordid,
                new_name or None,
                original_name or None,
                value(parts, "note") or None))
    return rows, latest, unmatched


def insert_rows(acc, table, columns, rows, errors):
    """Vloží riadky (zoznam n-tíc) do tabuľky, chyby zbiera. Vráti počet."""
    sql = "INSERT INTO [%s] (%s) VALUES (%s)" % (
        table, ", ".join(columns), ", ".join(["?"] * len(columns)))
    cur = acc.cursor()
    n = 0
    for row in rows:
        try:
            cur.execute(sql, row)
            n += 1
        except Exception as e:
            errors.append("%s: %s | dáta=%r" % (table, e, tuple(row)))
    return n


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def main():
    skuev = ask_skuev()
    folder = find_folder(skuev)
    gpkg = os.path.join(folder, GPKG_NAME)
    if not os.path.isfile(gpkg):
        sys.exit("Súbor %s neexistuje." % gpkg)
    if not os.path.isfile(TEMPLATE_ACCDB):
        sys.exit("Šablóna %s neexistuje." % TEMPLATE_ACCDB)

    # kópia šablóny -> <export>\SKUEV####\SKUEV####.accdb (existujúcu zazálohuj)
    out_dir = os.path.join(EXPORT_DIR, skuev)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, skuev + ".accdb")
    if os.path.exists(dest):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.splitext(dest)[0] + "_backup_" + stamp + ".accdb"
        os.rename(dest, backup)
        print("Existujúca DB zazálohovaná: %s" % backup)
    shutil.copy2(TEMPLATE_ACCDB, dest)
    print("Vytvorená kópia: %s" % dest)

    con = sqlite3.connect(gpkg)
    con.row_factory = sqlite3.Row
    acc = connect_access(dest)
    # V šablóne mapovanie_biotopov_7.accdb už oba stĺpce sú (od 2026-07-30),
    # rovnako pomenované ako v GPKG; doplnenie je len záloha pre staršiu šablónu.
    added = ensure_columns(acc, "tblHabHlavna", [
        ("polygon_id_form", "TEXT(255)"),  # zložené RECORDID zdrojového polygónu
        ("typ_polygon", "TEXT(255)"),      # 'A' / 'B'
    ])
    if added:
        print("Do tblHabHlavna pridané stĺpce: %s" % ", ".join(added))
    mappers = load_mappers(acc)

    errors = []
    warnings = []
    counts = {}
    unmatched_mappers = set()

    hlavna = con.execute("SELECT * FROM tblHabHlavna").fetchall()

    # [polygon_id_form] odkazuje na [RECORDID] (jedinečný). [polygon_id] nie je
    # jedinečné – polygóny pridané v QField preberajú polygon_id polygónu, z
    # ktorého boli rozdelené – preto slúži len ako záložné dohľadanie.
    hlavna_by_rec = {}
    hlavna_by_pid = {}
    for r in sorted(hlavna, key=lambda x: x["fid"]):
        if not is_empty(r["RECORDID"]):
            hlavna_by_rec.setdefault(str(r["RECORDID"]).strip(), r)
        if not is_empty(r["polygon_id"]):
            hlavna_by_pid.setdefault(str(r["polygon_id"]).strip(), r)

    def polyname(row):
        return "polygon_id=%s (fid=%s)" % (row["polygon_id"], row["fid"])

    def source_row(row):
        """Riadok, z ktorého sa preberajú údaje (odkaz [polygon_id_form]).

        Odkazy sa sledujú až po koncový zdroj; pri neexistujúcom odkaze alebo
        cykle sa vráti posledný platný riadok a zapíše sa upozornenie."""
        current = row
        seen = {row["fid"]}
        while not is_empty(current["polygon_id_form"]):
            ref = str(current["polygon_id_form"]).strip()
            parent = hlavna_by_rec.get(ref)
            if parent is None:
                parent = hlavna_by_pid.get(ref)
                if parent is not None:
                    warnings.append(
                        "%s: [polygon_id_form]='%s' nie je [RECORDID] – "
                        "dohľadané podľa [polygon_id] (fid=%s)."
                        % (polyname(row), ref, parent["fid"]))
            if parent is None:
                warnings.append(
                    "%s: [polygon_id_form]='%s', ale polygón s takým [RECORDID] "
                    "neexistuje – použité vlastné údaje."
                    % (polyname(row), ref))
                return current
            if parent["fid"] in seen:
                warnings.append("%s: cyklus v [polygon_id_form] – prevzaté údaje z %s."
                                % (polyname(row), polyname(current)))
                return current
            seen.add(parent["fid"])
            current = parent
        return current

    # ---------- tblHabHlavna ----------
    hlavna_cols = [
        "RECORDID", "fldskuevcode", "plocha", "datum", "rok", "lokalita",
        "x_karto", "y_karto", "gis_id_origin", "typ_gis_prvku",
        "hlavny_mapovatel", "druhy_mapovatel", "poznamka",
        "e0", "e1", "e2", "e3", "E1_invaz", "E2_invaz", "E3_invaz", "wktgeom",
        "polygon_id_form", "typ_polygon"]
    # RECORDID = SKUEV####_N_XX_YYY (rovnaká hodnota ide aj do gis_id_origin
    # a do všetkých cudzích kľúčov fkRECORDID v podriadených tabuľkách).
    #   XX  = 2-miestny kód mapovateľa z tblMappers podľa [hlavny_mapovatel]
    #   YYY = GPKG [RECORDID] doplnené na 3 miesta – POZOR: nie [polygon_id].
    #         Deti (tblHabDruhy/Biotopy/Aktivity) sa v GPKG viažu cez [RECORDID],
    #         ktoré je jedinečné aj tam, kde sa [polygon_id] opakuje; použitie
    #         polygon_id by pokazilo väzby a spôsobilo duplicitné RECORDID.
    gid_by_fid = {}   # fid -> zložené RECORDID
    rec_map = {}      # str(RECORDID)   -> zložené RECORDID (pre fotky)
    pid_map = {}      # str(polygon_id) -> zložené RECORDID (staršie FotoTable)
    pid_dupes = set()

    # 1. prechod: zložené RECORDID pre každý polygón (potrebné aj pre odkaz
    #    [polygon_id_form], ktorý môže smerovať na neskorší riadok)
    for r in hlavna:
        recid = r["RECORDID"]
        mid = mappers.get((r["hlavny_mapovatel"] or "").strip())
        if r["hlavny_mapovatel"] and mid is None:
            unmatched_mappers.add(r["hlavny_mapovatel"])
        xx = "%02d" % mid if mid is not None else "00"
        gid = "%s_N_%s_%03d" % (skuev, xx, recid) if recid is not None else None
        gid_by_fid[r["fid"]] = gid
        if gid is None:
            warnings.append("%s: chýba [RECORDID] – polygón sa nedá vložiť."
                            % polyname(r))
            continue
        if r["polygon_id"] is not None:
            key = str(r["polygon_id"])
            if key in pid_map:
                pid_dupes.add(key)  # nejednoznačné pre staršie FotoTable
            else:
                pid_map[key] = gid
        rec_map[str(recid)] = gid

    # 2. prechod: riadky tblHabHlavna – každý polygón s VLASTNÝMI údajmi,
    #    prepojenie na iný formulár len ako odkaz v [polygon_id_form]
    n_linked = 0
    hlavna_rows = []
    for r in hlavna:
        gid = gid_by_fid.get(r["fid"])
        if gid is None:
            continue

        geom = geom_from_blob(r["geom"])
        cx = cy = wkt = None
        if geom is not None:
            c = geom.centroid
            cx, cy = c.x, c.y
            wkt = geom.wkt
        plocha = None if r["p"] is None else str(r["p"])
        d, rok = parse_date(r["datum"])

        # [polygon_id_form] = zložené RECORDID zdrojového polygónu, aby sa dalo
        # v Accesse spojiť s tblHabHlavna.RECORDID
        form_gid = None
        if not is_empty(r["polygon_id_form"]):
            src = source_row(r)
            if src["fid"] != r["fid"]:
                n_linked += 1
                form_gid = gid_by_fid.get(src["fid"])
                if form_gid is None:
                    warnings.append(
                        "%s: zdrojový polygón %s nemá [RECORDID] – "
                        "[polygon_id_form] ostane prázdne."
                        % (polyname(r), polyname(src)))

        hlavna_rows.append((
            gid, r["KOD_UEV"], plocha, d, rok,
            r["lokalita"], cx, cy, gid, "P",
            r["hlavny_mapovatel"], r["druhy_mapovatel"], r["poznamka"],
            r["e0"], r["e1"], r["e2"], r["e3"],
            r["E1_invaz"], r["E2_invaz"], r["E3_invaz"], wkt,
            form_gid,
            None if is_empty(r["typ_polygon"]) else str(r["typ_polygon"]).strip()[:1]))
    counts["tblHabHlavna"] = insert_rows(acc, "tblHabHlavna", hlavna_cols,
                                         hlavna_rows, errors)

    # ---------- podriadené tabuľky ----------
    # Každý polygón dostane len svoje VLASTNÉ riadky – údaje prepojeného
    # formulára sa nekopírujú, prepojenie je zaznamenané v [polygon_id_form].
    def index_by_rec(table):
        idx = {}
        for row in con.execute("SELECT * FROM %s" % table):
            if not is_empty(row["fkRECORDID"]):
                idx.setdefault(str(row["fkRECORDID"]).strip(), []).append(row)
        return idx

    druhy_by_rec = index_by_rec("tblHabDruhy")
    biotopy_by_rec = index_by_rec("tblHabBiotopy")
    aktivity_by_rec = index_by_rec("tblAktivity")

    # riadky, ktorých [fkRECORDID] nemá polygón – nemajú kam patriť
    hlavna_recs = {str(r["RECORDID"]).strip() for r in hlavna
                   if not is_empty(r["RECORDID"])}
    for table, index in (("tblHabDruhy", druhy_by_rec),
                         ("tblHabBiotopy", biotopy_by_rec),
                         ("tblAktivity", aktivity_by_rec)):
        orphans = {k: len(v) for k, v in index.items() if k not in hlavna_recs}
        if orphans:
            warnings.append(
                "%s: %d riadkov má [fkRECORDID] bez polygónu v tblHabHlavna "
                "(%s) – nevložia sa."
                % (table, sum(orphans.values()),
                   ", ".join(sorted(orphans, key=str))))

    # Opatrenia sa v GPKG viažu na tblHabBiotopy.[id], ktoré tam nie je zaručene
    # jedinečné – opatrenie sa priradí biotopu s najnižším fid a zapíše sa
    # upozornenie. V Accesse sa väzba prepojí na novo pridelené [id].
    biotopy_fid_by_id = {}
    ambiguous_biotop_ids = set()
    for b in sorted((r for rows in biotopy_by_rec.values() for r in rows),
                    key=lambda x: x["fid"]):
        if b["id"] in biotopy_fid_by_id:
            ambiguous_biotop_ids.add(b["id"])
        else:
            biotopy_fid_by_id[b["id"]] = b["fid"]

    opat_by_biotop_fid = {}
    for o in con.execute("SELECT * FROM tblHabBiotopyOpatrenia"):
        bfid = biotopy_fid_by_id.get(o["fkHabBiotopyID"])
        if bfid is None:
            warnings.append("tblHabBiotopyOpatrenia fid=%s: [fkHabBiotopyID]=%s "
                            "nemá biotop – riadok sa vynechá."
                            % (o["fid"], o["fkHabBiotopyID"]))
            continue
        if o["fkHabBiotopyID"] in ambiguous_biotop_ids:
            warnings.append("tblHabBiotopyOpatrenia fid=%s: [fkHabBiotopyID]=%s "
                            "zodpovedá viacerým biotopom – priradené biotopu "
                            "fid=%s." % (o["fid"], o["fkHabBiotopyID"], bfid))
        opat_by_biotop_fid.setdefault(bfid, []).append(o)

    druhy_cols = ["fkRECORDID", "KOD", "NAZOV_LAT", "POKRYVNOST", "etaz",
                  "is_characetristic", "pokryvnost_perc", "KOD_KB"]
    biotopy_cols = ["id", "fkRECORDID", "biotop_cislo", "biotop_pokryv",
                    "biotop_cislo_new", "kvalita_biotopu_good",
                    "kvalita_biotopu_bad", "kvalita_biotopu_unsiut",
                    "manazment_biotopu_vhod", "manazment_biotopu_nevhod",
                    "vyhliadky_biotopu_good", "vyhliadky_biotopu_bad",
                    "vyhliadky_biotopu_unsiut"]
    opat_cols = ["fkHabBiotopyID", "kod_opatrenia", "detailny_opis_opatrenia",
                 "percento_z_plochy_biotopu"]
    akt_cols = ["fkRECORDID", "Aktivita", "Intenzita", "Perc_Plochy", "Vplyv"]

    druhy_rows, biotopy_rows, opat_rows, akt_rows = [], [], [], []
    next_biotop_id = 1

    for r in hlavna:
        gid = gid_by_fid.get(r["fid"])
        if gid is None:
            continue  # bez RECORDID nie je rodič, na ktorý by sa deti viazali
        own_rec = None if is_empty(r["RECORDID"]) else str(r["RECORDID"]).strip()

        for d in druhy_by_rec.get(own_rec) or []:
            druhy_rows.append((
                gid, d["KOD"], d["NAZOV_LAT"], d["POKRYVNOST"], d["etaz"],
                d["is_characetristic"], d["pokryvnost_perc"], d["kod_kbx"]))

        for b in biotopy_by_rec.get(own_rec) or []:
            bid = next_biotop_id
            next_biotop_id += 1
            biotopy_rows.append((
                bid, gid,
                b["biotop_cislo"], b["biotop_pokryv"], b["biotop_cislo_new"],
                b["kvalita_biotopu_good"], b["kvalita_biotopu_bad"],
                b["kvalita_biotopu_unsiut"], b["manazment_biotopu_vhod"],
                b["manazment_biotopu_nevhod"], b["vyhliadky_biotopu_good"],
                b["vyhliadky_biotopu_bad"], b["vyhliadky_biotopu_unsiut"]))
            for o in opat_by_biotop_fid.get(b["fid"], []):
                opat_rows.append((
                    bid, o["kod_opatrenia"], o["detailny_opis_opatrenia"],
                    o["percento_z_plochy_biotopu"]))

        for a in aktivity_by_rec.get(own_rec) or []:
            akt_rows.append((
                gid, a["Aktivita"], a["Intenzita"], a["Perc_Plochy"], a["Vplyv"]))

    counts["tblHabDruhy"] = insert_rows(acc, "tblHabDruhy", druhy_cols,
                                        druhy_rows, errors)
    # [id] sa vkladá explicitne kvôli väzbe na tblHabBiotopyOpatrenia
    counts["tblHabBiotopy"] = insert_rows(acc, "tblHabBiotopy", biotopy_cols,
                                          biotopy_rows, errors)
    counts["tblHabBiotopyOpatrenia"] = insert_rows(
        acc, "tblHabBiotopyOpatrenia", opat_cols, opat_rows, errors)
    counts["tblAktivity"] = insert_rows(acc, "tblAktivity", akt_cols,
                                        akt_rows, errors)

    # ---------- tblHabFotky (z FotoTable od 04_validacia_foto.py) ----------
    foto_cols = ["fkRECORDID", "fotoFileName", "fotoFileNameOriginal", "fotoNote"]
    foto_rows, foto_file, foto_unmatched = load_foto_rows(folder, skuev,
                                                         rec_map, pid_map)
    counts["tblHabFotky"] = insert_rows(acc, "tblHabFotky", foto_cols,
                                        foto_rows, errors)
    if foto_file is None:
        print("UPOZORNENIE: nenašiel som %s_FotoTable_*.txt – tblHabFotky ostáva "
              "prázdna (spusti najprv 04_validacia_foto.py)." % skuev)
    else:
        print("Fotky z: %s" % os.path.basename(foto_file))
        if foto_unmatched:
            print("UPOZORNENIE: kľúče fotiek bez zodpovedajúceho polygónu "
                  "(fkRECORDID = NULL): %s" % ", ".join(sorted(foto_unmatched)))
        if pid_dupes:
            print("UPOZORNENIE: duplicitné polygon_id v tblHabHlavna %s – pri "
                  "staršej FotoTable (stĺpec polygon_id) môže byť priradenie "
                  "fotiek nejednoznačné." % ", ".join(sorted(pid_dupes)))

    acc.commit()
    acc.close()
    con.close()

    # ---------- zhrnutie ----------
    print("\nNaplnené tabuľky v %s:" % dest)
    for t in ("tblHabHlavna", "tblHabDruhy", "tblHabBiotopy",
              "tblHabBiotopyOpatrenia", "tblAktivity", "tblHabFotky"):
        print("   %-24s %d riadkov" % (t, counts.get(t, 0)))
    print("   %-24s %d polygónov s vyplneným [polygon_id_form]" % ("", n_linked))
    if unmatched_mappers:
        print("\nUPOZORNENIE – mapovatelia nenájdení v tblMappers "
              "(v gis_id_origin je XX='00'):")
        for m in sorted(unmatched_mappers):
            print("   -", m)
    if warnings:
        print("\nUPOZORNENIA (%d):" % len(warnings))
        for w in warnings[:30]:
            print("   -", w)
        if len(warnings) > 30:
            print("   ... a ďalších %d" % (len(warnings) - 30))
    if errors:
        print("\nCHYBY (%d):" % len(errors))
        for e in errors[:50]:
            print("   ", e)
        if len(errors) > 50:
            print("   ... a ďalších %d" % (len(errors) - 50))
    else:
        print("\nBez chýb.")


if __name__ == "__main__":
    main()
