# -*- coding: utf-8 -*-
"""
Validácia údajov v GeoPackage MapovaniePrePS pre zadané územie SKUEV.

Kontroluje tabuľky tblHabHlavna, tblHabDruhy, tblHabBiotopy,
tblHabBiotopyOpatrenia a tblAktivity podľa pravidiel vo validacia.txt.
Polygóny s vyplneným [polygon_id_form] sa nekontrolujú – ich údaje sú
vedené na polygóne, ktorého [RECORDID] je v tomto poli uvedené, a ten sa
kontroluje samostatne. Počet preskočených polygónov je uvedený v reporte.

Opravy zapisované do GPKG (pred zápisom sa vytvorí záloha):
  - prázdny [datum]           -> doplní sa z priestorovo najbližšieho polygónu s dátumom
  - [hlavny_mapovatel]        -> nastaví sa podľa skuev_mapovatel.txt,
                                 ale len ak sa od neho líši
  - duplicitné záznamy v tblHabDruhy (zhodné vo všetkých stĺpcoch okrem fid)
                              -> ponechá sa len jeden záznam, ostatné sa zmažú
  - [biotop_cislo_new]       -> kontroluje sa, že každý polygón má aspoň
                                 jeden záznam v tblHabBiotopy s vyplnenou hodnotou
  - prázdny [biotop_cislo]     -> doplní sa [kod_2002] z prevodníka
                                 tblHabMapping_kod2002_kod2023.txt podľa
                                 [biotop_cislo_new]=[kod_2023]; ak má kód viac
                                 možností, zapíše sa iba varovanie

Ostatné pravidlá sa iba reportujú ako chyby (konzola + textový súbor
SKUEV####_ValidationReport_YYYYMMDD_HHMM.txt v priečinku SKUEV####).
"""

import math
import os
import re
import shutil
import sqlite3
import struct
import sys
from datetime import datetime

# slovenské znaky aj v konzole s cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAPOVATEL_FILE = os.path.join(SCRIPT_DIR, "skuev_mapovatel.txt")
MAPPING_FILE = os.path.join(SCRIPT_DIR, "tblHabMapping_kod2002_kod2023.txt")
GPKG_NAME = "MapovaniePrePS.gpkg"
# biotopy bez diagnostických druhov – nepočítajú sa do komplexu pri kontrole [kod_kbx]
KBX_EXEMPT_CODES = {"KRO12", "LES11", "LES"}


# ----------------------------------------------------------------------
# Geometria: GPKG blob -> stred (centroid bounding boxu)
# ----------------------------------------------------------------------

def _wkb_points(buf, offset, points):
    """Rekurzívne pozbiera súradnice z WKB geometrie, vráti nový offset."""
    byte_order = buf[offset]
    endian = "<" if byte_order == 1 else ">"
    (gtype,) = struct.unpack_from(endian + "I", buf, offset + 1)
    offset += 5

    has_z = bool(gtype & 0x80000000) or (gtype % 10000) // 1000 in (1, 3)
    has_m = bool(gtype & 0x40000000) or (gtype % 10000) // 1000 in (2, 3)
    dims = 2 + has_z + has_m
    base = gtype & 0x0FFFFFFF
    base %= 1000

    if base == 1:  # Point
        vals = struct.unpack_from(endian + "d" * dims, buf, offset)
        points.append((vals[0], vals[1]))
        return offset + 8 * dims
    if base == 2:  # LineString
        (n,) = struct.unpack_from(endian + "I", buf, offset)
        offset += 4
        for _ in range(n):
            vals = struct.unpack_from(endian + "d" * dims, buf, offset)
            points.append((vals[0], vals[1]))
            offset += 8 * dims
        return offset
    if base == 3:  # Polygon
        (nrings,) = struct.unpack_from(endian + "I", buf, offset)
        offset += 4
        for _ in range(nrings):
            (n,) = struct.unpack_from(endian + "I", buf, offset)
            offset += 4
            for _ in range(n):
                vals = struct.unpack_from(endian + "d" * dims, buf, offset)
                points.append((vals[0], vals[1]))
                offset += 8 * dims
        return offset
    if base in (4, 5, 6, 7):  # Multi* / GeometryCollection
        (n,) = struct.unpack_from(endian + "I", buf, offset)
        offset += 4
        for _ in range(n):
            offset = _wkb_points(buf, offset, points)
        return offset
    raise ValueError("Nepodporovaný typ WKB geometrie: %d" % gtype)


def geom_center(blob):
    """Stred geometrie z GPKG blobu (stred obálky), alebo None."""
    if blob is None or len(blob) < 8 or blob[0:2] != b"GP":
        return None
    flags = blob[3]
    endian = "<" if flags & 0x01 else ">"
    env_code = (flags >> 1) & 0x07
    env_counts = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
    if env_code not in env_counts:
        return None
    n_env = env_counts[env_code]
    header_len = 8 + 8 * n_env
    if n_env >= 4:
        minx, maxx, miny, maxy = struct.unpack_from(endian + "dddd", blob, 8)
        return ((minx + maxx) / 2.0, (miny + maxy) / 2.0)
    # bez obálky – prejdeme vrcholy WKB
    points = []
    try:
        _wkb_points(blob, header_len, points)
    except Exception:
        return None
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def geom_bounds(blob):
    """(minx, maxx, miny, maxy) z GPKG blobu, alebo None."""
    if blob is None or len(blob) < 8 or blob[0:2] != b"GP":
        return None
    flags = blob[3]
    endian = "<" if flags & 0x01 else ">"
    env_code = (flags >> 1) & 0x07
    env_counts = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
    if env_code not in env_counts:
        return None
    n_env = env_counts[env_code]
    if n_env >= 4:
        return struct.unpack_from(endian + "dddd", blob, 8)
    points = []
    try:
        _wkb_points(blob, 8, points)
    except Exception:
        return None
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), max(xs), min(ys), max(ys))


def register_gpkg_functions(con):
    """GPKG rtree triggre volajú ST_* funkcie, ktoré čisté sqlite3 nemá."""
    def st_is_empty(blob):
        if blob is None:
            return None
        return 1 if (len(blob) > 3 and blob[3] & 0x10) else 0

    def bound(idx):
        def f(blob):
            b = geom_bounds(blob)
            return None if b is None else b[idx]
        return f

    con.create_function("ST_IsEmpty", 1, st_is_empty)
    con.create_function("ST_MinX", 1, bound(0))
    con.create_function("ST_MaxX", 1, bound(1))
    con.create_function("ST_MinY", 1, bound(2))
    con.create_function("ST_MaxY", 1, bound(3))


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def is_empty(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def num(v):
    return 0 if v is None else v


def load_mapovatelia(path):
    """Načíta skuev_mapovatel.txt -> dict {'SKUEV0903': 'Meno Priezvisko'}."""
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"\t+|\s{2,}| ", line, maxsplit=1)
            if len(parts) != 2:
                continue
            key, name = parts[0].strip().upper(), parts[1].strip()
            if re.fullmatch(r"SKUEV\d{4}", key) and name:
                result[key] = name
    return result


def load_kod_mapping(path):
    """Načíta prevodník kod_2023 -> kod_2002 (TSV s hlavičkou).

    Vráti dict {'LKP1': ['Lk1', ...]} – kľúč veľkými písmenami,
    hodnoty v poradí zo súboru bez duplikátov.
    """
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        f.readline()  # hlavička: ID  kod_2002  nazov_2002  kod_2023  nazov_2023
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            k2002, k2023 = parts[1].strip(), parts[3].strip()
            if not k2002 or not k2023:
                continue
            vals = result.setdefault(k2023.upper(), [])
            if k2002 not in vals:
                vals.append(k2002)
    return result


def resolve_multi_kod(candidates):
    """Vyberie [kod_2023], keď má kód 2002 viac možností.

    Pravidlo: z kandidátov končiacich písmenom (napr. LKP03a, LKP03b) sa
    odstráni koncové písmeno a ak všetky vedú na jeden spoločný základ
    (LKP03), vráti sa tento základ. Kandidáti končiaci číslom sa ignorujú.
    Ak sa nedá jednoznačne určiť, vráti None.
    """
    bases = set()
    for c in candidates:
        if re.search(r"[A-Za-z]$", c):
            bases.add(re.sub(r"[A-Za-z]+$", "", c))
    return next(iter(bases)) if len(bases) == 1 else None


def ask_skuev():
    raw = (sys.argv[1] if len(sys.argv) > 1
           else input("Zadaj kód územia SKUEV (napr. SKUEV0903): ")).strip().upper()
    if re.fullmatch(r"\d{4}", raw):
        raw = "SKUEV" + raw
    if not re.fullmatch(r"SKUEV\d{4}", raw):
        sys.exit("Neplatný kód '%s' – očakávam tvar SKUEV#### (napr. SKUEV0903)." % raw)
    return raw


def find_gpkg(skuev):
    """Nájde GPKG len vo veľkopísmenovom priečinku SKUEV#### (nie 'skuev...')."""
    try:
        dir_names = os.listdir(CLOUD_DIR)
    except OSError as e:
        sys.exit("Priečinok %s sa nedá čítať: %s" % (CLOUD_DIR, e))
    if skuev not in dir_names:  # presné porovnanie – vyžaduje veľké SKUEV
        sys.exit("Priečinok %s neexistuje v %s (malé 'skuev...' sa ignoruje)."
                 % (skuev, CLOUD_DIR))
    folder = os.path.join(CLOUD_DIR, skuev)
    path = os.path.join(folder, GPKG_NAME)
    if os.path.isfile(path):
        return path
    fallback = os.path.join(folder, skuev + ".gpkg")
    if os.path.isfile(fallback):
        print("Upozornenie: %s neexistuje, použijem %s." % (GPKG_NAME, fallback))
        return fallback
    sys.exit("V priečinku %s som nenašiel %s ani %s.gpkg." % (folder, GPKG_NAME, skuev))


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def main():
    skuev = ask_skuev()
    gpkg = find_gpkg(skuev)

    mapovatelia = load_mapovatelia(MAPOVATEL_FILE)
    mapovatel = mapovatelia.get(skuev)

    errors = []    # chybové hlásenia
    warnings = []  # varovania
    fixes = []     # vykonané opravy

    # záloha pred zápisom
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.splitext(gpkg)[0] + "_backup_" + stamp + ".gpkg"
    shutil.copy2(gpkg, backup)
    print("Záloha vytvorená: %s" % backup)

    con = sqlite3.connect(gpkg)
    register_gpkg_functions(con)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    hlavna = cur.execute(
        "SELECT fid, geom, polygon_id, polygon_id_form, RECORDID, datum, hlavny_mapovatel,"
        "       e0, e1, e2, e3, typ_polygon FROM tblHabHlavna"
    ).fetchall()

    # ---------- 0. duplicitné záznamy v tblHabDruhy ----------
    # duplicita = zhoda vo všetkých stĺpcoch okrem fid; ponechá sa najnižší fid
    druhy_cols = [r["name"] for r in
                  cur.execute("PRAGMA table_info(tblHabDruhy)").fetchall()
                  if r["name"].lower() != "fid"]
    group_by = ", ".join('"%s"' % c for c in druhy_cols)
    dup_groups = cur.execute(
        "SELECT fkRECORDID, NAZOV_LAT, etaz, COUNT(*) - 1 AS extra "
        "FROM tblHabDruhy GROUP BY %s HAVING COUNT(*) > 1" % group_by
    ).fetchall()
    if dup_groups:
        cur.execute(
            "DELETE FROM tblHabDruhy WHERE fid NOT IN "
            "(SELECT MIN(fid) FROM tblHabDruhy GROUP BY %s)" % group_by)
        poly_by_rec = {str(r["RECORDID"]): r["polygon_id"] for r in hlavna
                       if not is_empty(r["RECORDID"])}
        for g in dup_groups:
            poly = poly_by_rec.get(
                "" if g["fkRECORDID"] is None else str(g["fkRECORDID"]), "?")
            fixes.append("tblHabDruhy: polygon_id=%s (fkRECORDID=%s), druh '%s' "
                         "[etaz]='%s': zmazaných %d duplicitných záznamov "
                         "(1 ponechaný)."
                         % (poly, g["fkRECORDID"], g["NAZOV_LAT"], g["etaz"],
                            g["extra"]))

    druhy = cur.execute("SELECT fid, fkRECORDID, etaz, kod_kbx FROM tblHabDruhy").fetchall()
    biotopy = cur.execute("SELECT * FROM tblHabBiotopy").fetchall()
    aktivity = cur.execute("SELECT fkRECORDID FROM tblAktivity").fetchall()
    opatrenia = cur.execute("SELECT fkHabBiotopyID FROM tblHabBiotopyOpatrenia").fetchall()

    # indexy podľa cudzích kľúčov
    etaze_by_rec = {}
    for r in druhy:
        if not is_empty(r["fkRECORDID"]) and not is_empty(r["etaz"]):
            etaze_by_rec.setdefault(str(r["fkRECORDID"]), set()).add(
                r["etaz"].strip().upper())
    biotopy_recs = {str(r["fkRECORDID"]) for r in biotopy if not is_empty(r["fkRECORDID"])}
    biotopy_by_rec = {}
    for r in biotopy:
        if not is_empty(r["fkRECORDID"]):
            biotopy_by_rec.setdefault(str(r["fkRECORDID"]), []).append(r)
    druhy_kbx_by_rec = {}
    for r in druhy:
        if not is_empty(r["fkRECORDID"]) and not is_empty(r["kod_kbx"]):
            druhy_kbx_by_rec.setdefault(str(r["fkRECORDID"]), set()).add(
                r["kod_kbx"].strip().upper())
    aktivity_recs = {str(r["fkRECORDID"]) for r in aktivity if not is_empty(r["fkRECORDID"])}
    opatrenia_ids = {r["fkHabBiotopyID"] for r in opatrenia if r["fkHabBiotopyID"] is not None}
    # [RECORDID] je kľúč pre podriadené tabuľky – duplicity sa reportujú nižšie
    hlavna_by_recordid = {}
    recordid_dupes = {}
    for r in sorted(hlavna, key=lambda x: x["fid"]):
        if not is_empty(r["RECORDID"]):
            key = str(r["RECORDID"]).strip()
            if key in hlavna_by_recordid:
                recordid_dupes.setdefault(key, [hlavna_by_recordid[key]]).append(r)
            else:
                hlavna_by_recordid[key] = r

    def polyname(row):
        return "polygon_id=%s (fid=%s)" % (row["polygon_id"], row["fid"])

    # Polygón s vyplneným [polygon_id_form] má údaje vedené na polygóne,
    # ktorého [RECORDID] je v tomto poli – kontroly aj opravy prebehnú tam,
    # takže tento polygón sa preskakuje.
    def is_form_copy(row):
        return not is_empty(row["polygon_id_form"])

    skipped_form = [r for r in hlavna if is_form_copy(r)]

    def recordid_of(row):
        return None if is_empty(row["RECORDID"]) else str(row["RECORDID"])

    # ---------- 1. datum – doplnenie z najbližšieho polygónu ----------
    centers = {row["fid"]: geom_center(row["geom"]) for row in hlavna}
    dated = [row for row in hlavna
             if not is_empty(row["datum"]) and centers[row["fid"]] is not None]

    for row in hlavna:
        if not is_empty(row["datum"]) or is_form_copy(row):
            continue
        c = centers[row["fid"]]
        if c is None:
            errors.append("%s: prázdny [datum] a polygón nemá geometriu – nedá sa doplniť."
                          % polyname(row))
            continue
        if not dated:
            errors.append("%s: prázdny [datum] a žiadny polygón v vrstve nemá vyplnený "
                          "dátum – nedá sa doplniť." % polyname(row))
            continue
        best, best_d = None, None
        for other in dated:
            oc = centers[other["fid"]]
            d = math.hypot(c[0] - oc[0], c[1] - oc[1])
            if best_d is None or d < best_d:
                best, best_d = other, d
        cur.execute("UPDATE tblHabHlavna SET datum = ? WHERE fid = ?",
                    (best["datum"], row["fid"]))
        fixes.append("%s: [datum] doplnený na '%s' podľa najbližšieho polygónu "
                     "polygon_id=%s (vzdialenosť ~%.0f m)."
                     % (polyname(row), best["datum"], best["polygon_id"], best_d))

    # ---------- 2. hlavny_mapovatel ----------
    if mapovatel is None:
        errors.append("SKUEV %s sa nenachádza v %s – [hlavny_mapovatel] nebol vyplnený."
                      % (skuev, os.path.basename(MAPOVATEL_FILE)))
    else:
        cur.execute(
            "UPDATE tblHabHlavna SET hlavny_mapovatel = ? "
            "WHERE hlavny_mapovatel IS NOT ?", (mapovatel, mapovatel))
        if cur.rowcount > 0:
            fixes.append("[hlavny_mapovatel] nastavený na '%s' "
                         "(%d záznamov zmenených)." % (mapovatel, cur.rowcount))

    # ---------- 3. kontroly tblHabHlavna ----------
    # [RECORDID] musí byť jedinečné: je to kľúč pre podriadené tabuľky, cieľ
    # odkazov z [polygon_id_form] aj základ zloženého RECORDID v Access DB
    # (tam je na ňom jedinečný index, takže duplicita = stratený polygón).
    # Nová hodnota vzniká ako max+1, čo pri paralelnom offline mapovaní dvoma
    # zariadeniami môže dať rovnaké číslo.
    for key, rows in sorted(recordid_dupes.items()):
        errors.append(
            "[RECORDID]=%s je duplicitné – %s. Podriadené záznamy sa priradia "
            "nesprávne a do Access DB sa vloží len jeden polygón; treba "
            "prečíslovať a upraviť aj [fkRECORDID] detí."
            % (key, ", ".join(polyname(r) for r in rows)))

    for row in hlavna:
        if is_form_copy(row):
            continue
        name = polyname(row)
        rec = recordid_of(row)

        typ = None if is_empty(row["typ_polygon"]) else row["typ_polygon"].strip().upper()
        if typ is None:
            errors.append("%s: [typ_polygon] je prázdny (povolené hodnoty 'A'/'B')." % name)
        elif typ not in ("A", "B"):
            errors.append("%s: [typ_polygon]='%s' – povolené sú len 'A' alebo 'B'."
                          % (name, row["typ_polygon"]))

        # e0–e3: rozsah 0–100
        evals = {}
        for i in range(4):
            col = "e%d" % i
            v = row[col]
            evals[i] = v
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                warnings.append("%s: [%s]='%s' nie je číslo." % (name, col, v))
                evals[i] = None
                continue
            if not (0 <= fv <= 100):
                warnings.append("%s: [%s]=%s je mimo rozsahu 0–100." % (name, col, v))

        if typ == "A" and all(evals[i] is None for i in range(4)):
            errors.append("%s: typ 'A', ale žiadna z etáží [e0]–[e3] nie je vyplnená."
                            % name)

        # eN > 0 -> aspoň jeden druh s etaz='EN' (E0 sa nekontroluje)
        for i in range(1, 4):
            v = evals[i]
            if v is None or num(v) <= 0:
                continue
            etaz_kod = "E%d" % i
            have = etaze_by_rec.get(rec, set()) if rec else set()
            if etaz_kod not in have:
                warnings.append("%s: [e%d]=%s > 0, ale v tblHabDruhy nie je žiadny druh "
                                "s [etaz]='%s'." % (name, i, v, etaz_kod))

        # typ 'A' -> aspoň 1 záznam v biotopoch a aktivitách
        if typ == "A":
            if rec is None:
                errors.append("%s: typ 'A', ale [RECORDID] je prázdny – nedajú sa overiť "
                              "súvisiace tabuľky." % name)
            else:
                if rec not in biotopy_recs:
                    errors.append("%s: typ 'A', ale v tblHabBiotopy nie je žiadny záznam."
                                  % name)
                if rec not in aktivity_recs:
                    errors.append("%s: typ 'A', ale v tblAktivity nie je žiadny záznam."
                                  % name)
                rec_biotopy = biotopy_by_rec.get(rec, [])
                # KRO12/LES11/LES nemajú diagnostické druhy – v komplexe sa
                # nepočítajú, takže 1 biotop + KRO12/LES11/LES nevyžaduje [kod_kbx]
                normal_biotopy = [b for b in rec_biotopy
                                  if is_empty(b["biotop_cislo_new"])
                                  or b["biotop_cislo_new"].strip().upper()
                                  not in KBX_EXEMPT_CODES]
                if len(rec_biotopy) > 1 and len(normal_biotopy) > 1:
                    allowed_kbx = {b["biotop_cislo_new"].strip().upper()
                                   for b in normal_biotopy
                                   if not is_empty(b["biotop_cislo_new"])}
                    have_kbx = druhy_kbx_by_rec.get(rec, set())
                    if not allowed_kbx:
                        errors.append("%s: typ 'A' a má viac biotopov, ale žiadny "
                                      "záznam v tblHabBiotopy nemá vyplnený "
                                      "[biotop_cislo_new] pre kontrolu [kod_kbx]."
                                      % name)
                    elif not (have_kbx & allowed_kbx):
                        errors.append("%s: typ 'A' a má viac biotopov v tblHabBiotopy, "
                                      "ale žiadny druh v tblHabDruhy nemá [kod_kbx] "
                                      "vyplnený hodnotou z [biotop_cislo_new] (%s)."
                                      % (name, ", ".join(sorted(allowed_kbx))))

    # ---------- 4. biotop_cislo_new – kontrola a doplnenie biotop_cislo ----------
    biotopy_new_recs = {str(r["fkRECORDID"]) for r in biotopy
                        if not is_empty(r["fkRECORDID"])
                        and not is_empty(r["biotop_cislo_new"])}
    for row in hlavna:
        if is_form_copy(row):
            continue
        name = polyname(row)
        rec = recordid_of(row)
        if rec is None:
            errors.append("%s: [RECORDID] je prázdny – nedá sa overiť, či má "
                          "záznam v tblHabBiotopy s vyplneným [biotop_cislo_new]."
                          % name)
        elif rec not in biotopy_new_recs:
            errors.append("%s: v tblHabBiotopy nie je žiadny záznam s vyplneným "
                          "[biotop_cislo_new]." % name)

    try:
        kod_mapping = load_kod_mapping(MAPPING_FILE)
    except OSError as e:
        kod_mapping = None
        errors.append("Prevodník %s sa nedá čítať (%s) – [biotop_cislo] "
                      "nebol doplnený." % (os.path.basename(MAPPING_FILE), e))
    if kod_mapping:
        filled = {}     # kod_2023 -> [kod_2002, počet]
        ambiguous = {}  # kod_2023 -> [kandidáti, počet]
        unknown = {}    # kod_2023 -> počet
        for b in biotopy:
            if not is_empty(b["biotop_cislo"]) or is_empty(b["biotop_cislo_new"]):
                continue
            kod = b["biotop_cislo_new"].strip()
            candidates = kod_mapping.get(kod.upper())
            if candidates is None:
                unknown[kod] = unknown.get(kod, 0) + 1
                continue
            if len(candidates) > 1:
                ambiguous.setdefault(kod, [candidates, 0])[1] += 1
                continue
            stary = candidates[0]
            cur.execute("UPDATE tblHabBiotopy SET biotop_cislo = ? "
                        "WHERE fid = ?", (stary, b["fid"]))
            rec = filled.setdefault(kod, [stary, 0])
            rec[1] += 1
        for kod, (stary, n) in sorted(filled.items()):
            fixes.append("tblHabBiotopy: [biotop_cislo] doplnený na '%s' podľa "
                         "[biotop_cislo_new]='%s' (%d záznamov)."
                         % (stary, kod, n))
        for kod, (cands, n) in sorted(ambiguous.items()):
            warnings.append("tblHabBiotopy: [biotop_cislo_new]='%s' má v prevodníku viac "
                            "kódov 2002 (%s) – %d záznamov s prázdnym "
                            "[biotop_cislo] treba doplniť ručne."
                            % (kod, ", ".join(cands), n))
        for kod, n in sorted(unknown.items()):
            warnings.append("tblHabBiotopy: [biotop_cislo_new]='%s' sa v prevodníku "
                            "nenachádza – %d záznamov s prázdnym [biotop_cislo]."
                            % (kod, n))

    # znovu načítať – kontroly nižšie majú vidieť doplnené [biotop_cislo]
    biotopy = cur.execute("SELECT * FROM tblHabBiotopy").fetchall()

    # ---------- 5. kontroly tblHabBiotopy ----------
    # len biotopy s vyplneným číslom (nezačínajúcim na 'X' ani 'Ls')
    # v polygónoch typu 'A'
    hlavna_by_rec = {str(r["RECORDID"]): r for r in hlavna if not is_empty(r["RECORDID"])}
    missing_by_poly = {}  # fid polygónu -> (polygón, [chýbajúce hodnoty po biotopoch])
    for b in biotopy:
        if is_empty(b["biotop_cislo"]):
            continue
        if b["biotop_cislo"].strip().upper().startswith(("X", "LS")):
            continue
        parent = hlavna_by_rec.get(str(b["fkRECORDID"]) if b["fkRECORDID"] is not None else "")
        parent_typ = (None if parent is None or is_empty(parent["typ_polygon"])
                      else parent["typ_polygon"].strip().upper())
        if parent_typ != "A":
            continue
        # biotopy polygónu s vyplneným [polygon_id_form] sa nekontrolujú
        if is_form_copy(parent):
            continue
        # v hláseniach sa uvádza nový kód (ak chýba, kód 2002)
        kod_new = ("" if is_empty(b["biotop_cislo_new"])
                   else b["biotop_cislo_new"].strip())
        kod_show = kod_new or b["biotop_cislo"]
        where = "tblHabBiotopy fid=%s, biotop='%s' (%s)" % (
            b["fid"], kod_show, polyname(parent))

        if is_empty(b["biotop_pokryv"]):
            errors.append("%s: [biotop_pokryv] je prázdny." % where)
        # kvalita/manažment/vyhliadky sa zbierajú a reportujú raz za polygón
        missing = []
        if not (num(b["kvalita_biotopu_good"]) > 0 or num(b["kvalita_biotopu_bad"]) > 0
                or num(b["kvalita_biotopu_unsiut"]) > 0):
            missing.append("kvalita")
        if not (num(b["manazment_biotopu_vhod"]) > 0 or num(b["manazment_biotopu_nevhod"]) > 0):
            missing.append("manazment")
        if not (num(b["vyhliadky_biotopu_good"]) > 0 or num(b["vyhliadky_biotopu_bad"]) > 0
                or num(b["vyhliadky_biotopu_unsiut"]) > 0):
            missing.append("vyhliadky")
        # kvalita/manažment/vyhliadky a opatrenia sa pri KRO12/LES11/LES nereportujú
        if kod_new.upper() in KBX_EXEMPT_CODES:
            continue
        if missing:
            missing_by_poly.setdefault(parent["fid"], (parent, []))[1].append(
                "biotop '%s' (fid=%s): %s" % (kod_show, b["fid"], ", ".join(missing)))
        if b["id"] is None or b["id"] not in opatrenia_ids:
            warnings.append("%s: v tblHabBiotopyOpatrenia nie je žiadne opatrenie "
                            "(fkHabBiotopyID=%s)." % (where, b["id"]))

    # vypíšu sa v reporte ako samostatná sekcia
    missing_values = ["%s: %s" % (polyname(parent), "; ".join(items))
                      for parent, items in missing_by_poly.values()]

    con.commit()
    con.close()

    # ---------- report ----------
    lines = []
    lines.append("Validácia %s – %s" % (skuev, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("Súbor: %s" % gpkg)
    lines.append("Záloha: %s" % backup)
    lines.append("Nekontrolované polygóny s vyplneným [polygon_id_form]: %d"
                 % len(skipped_form))
    lines.append("")
    lines.append("=== OPRAVY (%d) ===" % len(fixes))
    lines.extend(fixes if fixes else ["(žiadne)"])
    lines.append("")
    lines.append("=== CHYBY (%d) ===" % len(errors))
    lines.extend(errors if errors else ["(žiadne)"])
    lines.append("")
    lines.append("=== CHÝBAJÚ HODNOTY BIOTOPU (žiadna nie je nad nulou) (%d) ==="
                 % len(missing_values))
    lines.extend(missing_values if missing_values else ["(žiadne)"])
    lines.append("")
    lines.append("=== VAROVANIA (%d) ===" % len(warnings))
    lines.extend(warnings if warnings else ["(žiadne)"])

    report = "\n".join(lines)
    print("\n" + report)

    report_path = os.path.join(
        os.path.dirname(gpkg),
        "%s_ValidationReport_%s.txt"
        % (skuev, datetime.now().strftime("%Y%m%d_%H%M")))
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print("\nReport uložený do: %s" % report_path)


if __name__ == "__main__":
    main()
