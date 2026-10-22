# -*- coding: utf-8 -*-
"""
edit_merge.py – zlúčenie polygónov vrstvy tblHabHlavna v MapovaniePrePS.gpkg
aj so záznamami vo všetkých podriadených tabuľkách.

Použitie:
    python edit_merge.py <cesta_ku_gpkg> <ID[,ID,...]> [--target ID]

    <cesta_ku_gpkg>  plná cesta k MapovaniePrePS.gpkg (napr. v priečinku
                     C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\)
    <ID[,ID,...]>    [RECORDID] polygónov (číslo polygónu na mape):
                       - viac ID oddelených čiarkou -> zlúčia sa uvedené polygóny
                       - jedno ID -> zlúči sa tento polygón so všetkými, ktoré
                         ho majú v [polygon_id_form] (aj reťazovo: polygón
                         odkazujúci na polygón, ktorý odkazuje na zadaný)
    --target ID      ktorý polygón ostane (predvolene ten, na ktorý ostatné
                     odkazujú cez [polygon_id_form], inak prvý v zozname)

Postup:
  1. načíta polygóny skupiny a ich podriadené záznamy (tblHabBiotopy,
     tblHabBiotopyOpatrenia, tblHabDruhy, tblAktivity)
  2. zistí konflikty – rozdielne vyplnené hodnoty v tblHabHlavna, ten istý
     biotop / druh / aktivita vo viacerých polygónoch s rozdielnymi hodnotami
  3. pri každom konflikte sa opýta, ktorá hodnota má byť vo výslednom polygóne
     (ponuka zistených hodnôt, pri pokryvnostiach aj plošne vážený priemer,
     možnosť zadať vlastnú hodnotu)
  4. vypíše plán zlúčenia a po potvrdení zapíše zmeny (pred zápisom sa
     vytvorí záloha MapovaniePrePS_backup_YYYYMMDD_HHMMSS.gpkg)

Zlúčenie:
  - geometria = zjednotenie geometrií, [p] = plocha nového polygónu (m²)
  - [polygon_id_form] výsledného polygónu sa vymaže; polygóny mimo skupiny,
    ktoré odkazovali na zlúčené polygóny, odkazujú na výsledný
  - podriadené záznamy, ktoré sú len v jednom polygóne, sa presunú
    ([fkRECORDID]); ten istý biotop ([biotop_cislo_new]), druh ([KOD]+[etaz]
    +[kod_kbx]) alebo aktivita ([Aktivita]) vo viacerých polygónoch sa zlúči
    do jedného záznamu bez straty vyplnených hodnôt
  - opatrenia zlúčených biotopov sa prevesia na ponechaný biotop
    ([fkHabBiotopyID]); rovnaký [kod_opatrenia] sa zlúči
  - zlúčené polygóny sa z tblHabHlavna zmažú

Vyžaduje: osgeo (GDAL) – interpreter s GDAL, napr.
  C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
"""

import os
import shutil
import sys
from datetime import datetime

from osgeo import ogr

ogr.UseExceptions()

# slovenské znaky aj v konzole s cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HLAVNA = "tblHabHlavna"
BIOTOPY = "tblHabBiotopy"
OPATRENIA = "tblHabBiotopyOpatrenia"
DRUHY = "tblHabDruhy"
AKTIVITY = "tblAktivity"

# stĺpce tblHabHlavna, ktoré sa neporovnávajú (kľúče, geometria, odvodené)
HLAVNA_SKIP = {"fid", "geom", "RECORDID", "polygon_id_form", "p", "polygon_id"}
# stĺpce podriadených tabuliek, ktoré nie sú údajmi (kľúče, cudzie kľúče)
CHILD_SKIP = {"fid", "fid_1", "id", "fkRECORDID", "fkHabBiotopyID"}
# Braun-Blanquetova stupnica [POKRYVNOST] od najnižšej
POKRYVNOST_ORDER = ["r", "+", "1", "2m", "2a", "2b", "3", "4", "5"]
# percentá kvality / manažmentu / vyhliadok biotopu (0–100 % plochy biotopu,
# 0 je platná hodnota) – pri konflikte sa ponúkne priemer vážený plochou
# biotopu v jednotlivých polygónoch (plocha polygónu × [biotop_pokryv])
BIOTOP_PERCENT_COLS = {
    "kvalita_biotopu_good", "kvalita_biotopu_bad", "kvalita_biotopu_unsiut",
    "manazment_biotopu_vhod", "manazment_biotopu_nevhod",
    "vyhliadky_biotopu_good", "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut",
}
# stĺpce s percentom plochy – pri konflikte sa ponúkne plošne vážený priemer
AREA_WEIGHTED = {
    (BIOTOPY, "biotop_pokryv"),
    (AKTIVITY, "Perc_Plochy"),
    (OPATRENIA, "percento_z_plochy_biotopu"),
}


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def is_empty(v):
    """None, prázdny text alebo 0 v [pokryvnost_perc] (predvolená hodnota)."""
    return v is None or (isinstance(v, str) and not v.strip())


def norm(v):
    """Hodnota na porovnávanie: text bez medzier, číslo ako číslo."""
    if is_empty(v):
        return None
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s.replace(",", "."))
            return int(f) if f.is_integer() else f
        except ValueError:
            return s
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def show(v):
    if v is None:
        return "<prázdne>"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def pokryvnost_rank(v):
    s = "" if is_empty(v) else str(v).strip().lower()
    return POKRYVNOST_ORDER.index(s) if s in POKRYVNOST_ORDER else -1


def read_rows(ds, table, where=None):
    """Riadky tabuľky ako slovníky {stĺpec: hodnota} (fid je kľúč OGR)."""
    layer = ds.GetLayerByName(table)
    if layer is None:
        sys.exit("V GPKG chýba tabuľka %s." % table)
    defn = layer.GetLayerDefn()
    cols = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    layer.SetAttributeFilter(where)
    rows = []
    layer.ResetReading()
    for f in layer:
        row = {"fid": f.GetFID()}
        for i, c in enumerate(cols):
            row[c] = None if f.IsFieldNull(i) or not f.IsFieldSet(i) else f.GetField(i)
        if layer.GetGeomType() != ogr.wkbNone:
            g = f.GetGeometryRef()
            row["geom"] = g.Clone() if g is not None else None
        rows.append(row)
    layer.SetAttributeFilter(None)
    return cols, rows


def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def run_sql(ds, sql):
    res = ds.ExecuteSQL(sql)
    if res is not None:
        ds.ReleaseResultSet(res)


def refresh_feature_counts(gpkg):
    """GDAL po priamom SQL nastaví počet prvkov v gpkg_ogr_contents na NULL."""
    import sqlite3
    con = sqlite3.connect(gpkg)
    try:
        for (t,) in con.execute("SELECT table_name FROM gpkg_ogr_contents").fetchall():
            con.execute('UPDATE gpkg_ogr_contents SET feature_count = '
                        '(SELECT COUNT(*) FROM "%s") WHERE table_name = ?' % t, (t,))
        con.commit()
    finally:
        con.close()


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


def ask_choice(title, values, area_weighted=None, numeric=False):
    """
    Opýta sa na hodnotu konfliktu.
      values        [(hodnota, popis zdroja)] – rôzne zistené hodnoty
      area_weighted plošne vážený priemer (ponúkne sa ako prvá možnosť)
    Vráti zvolenú hodnotu.
    """
    print("\n  KONFLIKT: %s" % title)
    options = []
    if area_weighted is not None:
        options.append((area_weighted, "plošne vážený priemer"))
    options.extend(values)
    for i, (val, src) in enumerate(options, 1):
        print("    %d) %-20s %s" % (i, show(val), src))
    print("    %d) %-20s" % (len(options) + 1, "vlastná hodnota"))
    while True:
        s = ask("  Voľba [1]: ", "1")
        if s.isdigit() and 1 <= int(s) <= len(options):
            return options[int(s) - 1][0]
        if s.isdigit() and int(s) == len(options) + 1:
            s = ask("  Zadaj hodnotu (prázdny Enter = NULL): ", "")
            if s == "":
                return None
            if numeric:
                try:
                    f = float(s.replace(",", "."))
                    return int(f) if f.is_integer() else f
                except ValueError:
                    print("  Očakávam číslo.")
                    continue
            return s
        print("  Neplatná voľba.")


def weighted(pairs):
    """Plošne vážený priemer z [(hodnota alebo None, plocha)] – None = 0."""
    total = sum(a for _, a in pairs)
    if total <= 0:
        return None
    s = sum((0 if is_empty(v) else float(v)) * a for v, a in pairs)
    return int(round(s / total))


# ----------------------------------------------------------------------
# Zlúčenie skupiny záznamov jednej tabuľky
# ----------------------------------------------------------------------

def merge_records(table, label, cols, rows, areas, polygon_area):
    """
    Zlúči záznamy `rows` (ten istý objekt v rôznych polygónoch) do jedného.
      areas         {fid záznamu: plocha polygónu, z ktorého pochádza}
      polygon_area  celková plocha výsledného polygónu (váha chýbajúcich = 0)
    Vráti (ponechaný riadok, {stĺpec: nová hodnota}, [zmazané riadky],
           [popisy konfliktov]).
    """
    data_cols = [c for c in cols if c not in CHILD_SKIP]

    def filled(r):
        return sum(1 for c in data_cols if not is_empty(r[c])
                   and not (c == "pokryvnost_perc" and norm(r[c]) == 0))

    if table == DRUHY:
        keep = max(rows, key=lambda r: (pokryvnost_rank(r["POKRYVNOST"]), filled(r), -r["fid"]))
    else:
        keep = max(rows, key=lambda r: (filled(r), -r["fid"]))
    drop = [r for r in rows if r["fid"] != keep["fid"]]

    updates = {}
    conflicts = []

    def sources(rs):
        return "polygón(y) %s" % ", ".join(
            show(r["fkRECORDID"]) if "fkRECORDID" in r else "fid=%s" % r["fid"] for r in rs)

    for c in data_cols:
        values = {}
        for r in rows:
            v = r[c]
            if is_empty(v) or (c == "pokryvnost_perc" and norm(v) == 0):
                continue
            key = str(norm(v)).upper() if c == "kod_kbx" else norm(v)
            values.setdefault(key, (v, []))[1].append(r)
        if not values:
            continue
        if len(values) == 1:
            v = next(iter(values.values()))[0]
            if norm(v) != norm(keep[c]):
                updates[c] = v
            continue
        # rozdielne vyplnené hodnoty
        if c == "POKRYVNOST":
            best = max((v for v, _ in values.values()), key=pokryvnost_rank)
            chosen = best
            default_note = "najvyššia na stupnici"
        elif c == "pokryvnost_perc":
            chosen = max((v for v, _ in values.values()), key=lambda x: float(norm(x)))
            default_note = "najvyššia hodnota"
        else:
            chosen = None
            default_note = None

        choices = [(v, sources(rs)) for v, rs in values.values()]
        aw = None
        if (table, c) in AREA_WEIGHTED and polygon_area:
            pairs = [(r[c], areas.get(r["fid"], 0)) for r in rows]
            covered = sum(a for _, a in pairs)
            # polygóny skupiny, kde objekt chýba, prispievajú nulou
            aw = weighted(pairs + [(None, max(polygon_area - covered, 0))])
        elif table == BIOTOPY and c in BIOTOP_PERCENT_COLS:
            # percento z plochy biotopu: váha = plocha biotopu v polygóne,
            # polygóny bez tohto biotopu sa nepočítajú
            def biotop_area(r):
                pokryv = norm(r["biotop_pokryv"])
                return areas.get(r["fid"], 0) * (100 if pokryv is None else float(pokryv)) / 100.0
            aw = weighted([(r[c], biotop_area(r)) for r in rows if not is_empty(r[c])])
        title = "%s – %s: [%s]" % (table, label, c)
        if chosen is not None:
            title += "  (predvolene %s: %s)" % (default_note, show(chosen))
            choices.sort(key=lambda x: norm(x[0]) != norm(chosen))
        numeric = all(isinstance(norm(v), (int, float)) for v, _ in choices)
        value = ask_choice(title, choices, aw, numeric=numeric)
        conflicts.append("%s [%s]: %s -> %s" % (
            label, c, " / ".join(show(v) for v, _ in choices), show(value)))
        if norm(value) != norm(keep[c]):
            updates[c] = value
    return keep, updates, drop, conflicts


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def parse_args(argv):
    args = [a for a in argv[1:]]
    target = None
    if "--target" in args:
        i = args.index("--target")
        if i + 1 >= len(args) or not args[i + 1].isdigit():
            sys.exit("--target vyžaduje číslo RECORDID.")
        target = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    gpkg, ids_raw = args
    if not os.path.isfile(gpkg):
        sys.exit("Súbor neexistuje: %s" % gpkg)
    ids = []
    for part in ids_raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            sys.exit("Neplatné ID polygónu '%s' – očakávam celé číslo (RECORDID)." % part)
        if int(part) not in ids:
            ids.append(int(part))
    if not ids:
        sys.exit("Nezadané žiadne ID polygónu.")
    return gpkg, ids, target


def main():
    gpkg, ids, target_id = parse_args(sys.argv)

    ds = ogr.Open(gpkg, 0)
    if ds is None:
        sys.exit("GPKG sa nedá otvoriť: %s" % gpkg)
    hl_cols, hlavna = read_rows(ds, HLAVNA)
    by_rec = {}
    for r in hlavna:
        if r["RECORDID"] is not None:
            by_rec.setdefault(int(r["RECORDID"]), []).append(r)
    dups = [k for k, v in by_rec.items() if len(v) > 1]

    missing = [i for i in ids if i not in by_rec]
    if missing:
        sys.exit("Polygón(y) RECORDID=%s v tblHabHlavna neexistujú."
                 % ", ".join(str(i) for i in missing))

    # ---- výber skupiny ----
    if len(ids) == 1:
        root = ids[0]
        group_ids = [root]
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for r in hlavna:
                pf = r["polygon_id_form"]
                if pf is not None and int(pf) == cur and r["RECORDID"] is not None \
                        and int(r["RECORDID"]) not in group_ids:
                    group_ids.append(int(r["RECORDID"]))
                    queue.append(int(r["RECORDID"]))
        if len(group_ids) == 1:
            own = by_rec.get(root, [{}])[0].get("polygon_id_form")
            hint = (" Polygón %s sám odkazuje na polygón %s – zadaj ten." % (root, show(own))
                    if own is not None else "")
            sys.exit("Na polygón RECORDID=%s neodkazuje cez [polygon_id_form] žiadny "
                     "iný polygón – nie je čo zlúčiť.%s" % (root, hint))
    else:
        group_ids = ids

    bad = [i for i in group_ids if i in dups]
    if bad:
        sys.exit("RECORDID=%s nie je jedinečné (viac polygónov) – najprv opraviť."
                 % ", ".join(str(i) for i in bad))
    group = [by_rec[i][0] for i in group_ids]

    # ---- výber ponechaného polygónu ----
    if target_id is None:
        referenced = [i for i in group_ids
                      if any(r["polygon_id_form"] is not None
                             and int(r["polygon_id_form"]) == i for r in group)]
        target_id = referenced[0] if referenced else group_ids[0]
    if target_id not in group_ids:
        sys.exit("--target %s nie je v zlučovanej skupine." % target_id)
    target = by_rec[target_id][0]
    others = [r for r in group if int(r["RECORDID"]) != target_id]
    other_ids = [int(r["RECORDID"]) for r in others]

    def area_of(r):
        return r["geom"].GetArea() if r["geom"] is not None else 0.0

    areas_by_rec = {int(r["RECORDID"]): area_of(r) for r in group}

    print("GPKG: %s" % gpkg)
    print("Zlučované polygóny (RECORDID): %s" % ", ".join(str(i) for i in group_ids))
    print("Ostáva polygón RECORDID=%s (fid=%s), zmažú sa: %s"
          % (target_id, target["fid"], ", ".join(str(i) for i in other_ids)))
    for r in group:
        print("  RECORDID=%-5s fid=%-5s plocha=%9.0f m²  typ=%s  polygon_id_form=%s  "
              "podlaorta=%s  datum=%s" % (
                  r["RECORDID"], r["fid"], area_of(r), show(r["typ_polygon"]),
                  show(r["polygon_id_form"]), show(r["podlaorta"]), show(r["datum"])))

    # ---- geometria ----
    geoms = [r["geom"] for r in group if r["geom"] is not None]
    if not geoms:
        sys.exit("Žiadny z polygónov nemá geometriu.")
    union = None
    for g in geoms:
        g = g.Clone()
        if not g.IsValid():
            g = g.MakeValid()
        union = g if union is None else union.Union(g)
    union = ogr.ForceToMultiPolygon(union)
    new_area = union.GetArea()
    multipart = union.GetGeometryCount() > 1
    if multipart:
        print("UPOZORNENIE: zlúčené polygóny sa nedotýkajú – výsledok má %d častí."
              % union.GetGeometryCount())

    # ---- atribúty tblHabHlavna ----
    hl_updates = {}
    conflicts = []
    for c in hl_cols:
        if c in HLAVNA_SKIP:
            continue
        values = {}
        for r in group:
            if not is_empty(r[c]):
                values.setdefault(norm(r[c]), (r[c], []))[1].append(int(r["RECORDID"]))
        if not values:
            continue
        if len(values) == 1:
            v = next(iter(values.values()))[0]
            if norm(v) != norm(target[c]):
                hl_updates[c] = v
            continue
        choices = [(v, "polygón(y) %s" % ", ".join(str(i) for i in recs))
                   for v, recs in values.values()]
        # hodnota ponechaného polygónu ako prvá (predvolená)
        choices.sort(key=lambda x: norm(x[0]) != norm(target[c]))
        if c == "poznamka":
            joined = " | ".join(v for v, _ in choices)
            choices.append((joined, "spojené poznámky"))
        numeric = all(isinstance(norm(v), (int, float)) for v, _ in choices)
        value = ask_choice("%s RECORDID=%s: [%s]" % (HLAVNA, target_id, c),
                           choices, numeric=numeric)
        conflicts.append("%s [%s]: %s -> %s" % (
            HLAVNA, c, " / ".join(show(v) for v, _ in choices), show(value)))
        if norm(value) != norm(target[c]):
            hl_updates[c] = value
    hl_updates["p"] = int(round(new_area))
    if target["polygon_id_form"] is not None:
        hl_updates["polygon_id_form"] = None

    # polygóny mimo skupiny odkazujúce na zlúčené polygóny
    repoint = [int(r["RECORDID"]) for r in hlavna
               if r["polygon_id_form"] is not None and int(r["polygon_id_form"]) in other_ids
               and r["RECORDID"] is not None and int(r["RECORDID"]) not in group_ids]

    # ---- podriadené tabuľky ----
    in_list = "(%s)" % ", ".join(str(i) for i in group_ids)
    bio_cols, biotopy = read_rows(ds, BIOTOPY, "fkRECORDID IN " + in_list)
    dr_cols, druhy = read_rows(ds, DRUHY, "fkRECORDID IN " + in_list)
    ak_cols, aktivity = read_rows(ds, AKTIVITY, "fkRECORDID IN " + in_list)
    bio_ids = [r["id"] for r in biotopy if r["id"] is not None]
    op_cols, opatrenia = read_rows(
        ds, OPATRENIA, "fkHabBiotopyID IN (%s)" % ", ".join(str(i) for i in bio_ids)
    ) if bio_ids else (None, [])

    plan = {t: {"move": [], "update": {}, "delete": []}
            for t in (BIOTOPY, DRUHY, AKTIVITY, OPATRENIA)}

    def child_area(r):
        return areas_by_rec.get(int(r["fkRECORDID"]), 0.0)

    def merge_table(table, label_fn, key_fn, cols, rows):
        groups = {}
        for r in rows:
            groups.setdefault(key_fn(r), []).append(r)
        kept_by_dropped = {}
        for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if len(rs) == 1:
                r = rs[0]
                if int(r["fkRECORDID"]) != target_id:
                    plan[table]["move"].append(r)
                continue
            keep, upd, drop, conf = merge_records(
                table, label_fn(rs[0]), cols, rs,
                {r["fid"]: child_area(r) for r in rs}, new_area)
            if int(keep["fkRECORDID"]) != target_id:
                plan[table]["move"].append(keep)
            if upd:
                plan[table]["update"][keep["fid"]] = (keep, upd)
            plan[table]["delete"].extend(drop)
            conflicts.extend("%s %s" % (table, c) for c in conf)
            for d in drop:
                kept_by_dropped[d["fid"]] = keep
        return kept_by_dropped

    # kľúč biotopu = [biotop_cislo_new]; záznam bez neho patrí k biotopu
    # s rovnakým [biotop_cislo], ak je taký práve jeden (Katalóg 2002 -> 2023
    # je mnoho-k-mnohým, pri viacerých ostáva samostatne)
    old_to_new = {}
    for r in biotopy:
        if not is_empty(r["biotop_cislo_new"]) and not is_empty(r["biotop_cislo"]):
            old_to_new.setdefault(str(r["biotop_cislo"]).strip().upper(), set()).add(
                str(r["biotop_cislo_new"]).strip().upper())

    def bio_key(r):
        if not is_empty(r["biotop_cislo_new"]):
            return str(r["biotop_cislo_new"]).strip().upper()
        if is_empty(r["biotop_cislo"]):
            return "fid=%s" % r["fid"]
        old = str(r["biotop_cislo"]).strip().upper()
        if len(old_to_new.get(old, ())) == 1:
            return next(iter(old_to_new[old]))
        return "OLD:" + old

    def bio_label(r):
        return "biotop %s" % (show(r["biotop_cislo_new"]) if not is_empty(r["biotop_cislo_new"])
                              else show(r["biotop_cislo"]))

    def druh_key(r):
        if is_empty(r["KOD"]):
            return ("fid", r["fid"])
        etaz = "" if is_empty(r["etaz"]) else str(r["etaz"]).strip().upper()
        kbx = "" if is_empty(r["kod_kbx"]) else str(r["kod_kbx"]).strip().upper()
        return (str(norm(r["KOD"])), etaz, kbx)

    def druh_label(r):
        return "druh %s (KOD=%s) etáž %s%s" % (
            show(r["NAZOV_LAT"]), show(r["KOD"]), show(r["etaz"]),
            "" if is_empty(r["kod_kbx"]) else " kod_kbx=%s" % r["kod_kbx"])

    def akt_key(r):
        return str(r["Aktivita"]).strip().upper() if not is_empty(r["Aktivita"]) else "fid=%s" % r["fid"]

    def akt_label(r):
        return "aktivita %s" % show(r["Aktivita"])

    # rovnaký druh s rozdielnym [kod_kbx] ostáva samostatne (pravidlo z 03_validacia)
    druhy_warn = {}
    for r in druhy:
        if not is_empty(r["KOD"]):
            k = druh_key(r)
            druhy_warn.setdefault((k[0], k[1]), set()).add(k[2])
    warnings = ["tblHabDruhy: druh KOD=%s etáž '%s' má rozdielne [kod_kbx] (%s) – "
                "ponechané ako samostatné záznamy." % (k[0], k[1], ", ".join(sorted(v - {""}) or ["-"]))
                for k, v in druhy_warn.items() if len(v) > 1]

    bio_kept = merge_table(BIOTOPY, bio_label, bio_key, bio_cols, biotopy)
    merge_table(DRUHY, druh_label, druh_key, dr_cols, druhy)
    merge_table(AKTIVITY, akt_label, akt_key, ak_cols, aktivity)

    # opatrenia: prevesiť zo zmazaných biotopov na ponechané, zlúčiť rovnaké kódy
    if opatrenia:
        bio_by_id = {r["id"]: r for r in biotopy}
        new_parent = {}
        for o in opatrenia:
            b = bio_by_id.get(o["fkHabBiotopyID"])
            if b is not None and b["fid"] in bio_kept:
                new_parent[o["fid"]] = bio_kept[b["fid"]]["id"]
        groups = {}
        for o in opatrenia:
            parent = new_parent.get(o["fid"], o["fkHabBiotopyID"])
            code = str(o["kod_opatrenia"]).strip().upper() if not is_empty(o["kod_opatrenia"]) else "fid=%s" % o["fid"]
            groups.setdefault((parent, code), []).append(o)
        for (parent, code), rs in groups.items():
            if len(rs) == 1:
                o = rs[0]
                if o["fid"] in new_parent:
                    plan[OPATRENIA]["update"][o["fid"]] = (o, {"fkHabBiotopyID": parent})
                continue
            # plocha biotopu = plocha polygónu, z ktorého opatrenie pochádza
            areas = {o["fid"]: child_area(bio_by_id[o["fkHabBiotopyID"]])
                     if o["fkHabBiotopyID"] in bio_by_id else 0.0 for o in rs}
            keep, upd, drop, conf = merge_records(
                OPATRENIA, "opatrenie %s biotopu id=%s" % (code, parent), op_cols, rs,
                areas, new_area)
            if keep["fid"] in new_parent:
                upd["fkHabBiotopyID"] = parent
            if upd:
                plan[OPATRENIA]["update"][keep["fid"]] = (keep, upd)
            plan[OPATRENIA]["delete"].extend(drop)
            conflicts.extend("%s %s" % (OPATRENIA, c) for c in conf)
    ds = None

    # ---- plán ----
    print("\n================ PLÁN ZLÚČENIA ================")
    print("%s RECORDID=%s: nová geometria (%s, %.0f m²), zmeny: %s" % (
        HLAVNA, target_id, "viac častí" if multipart else "1 časť", new_area,
        ", ".join("[%s]=%s" % (c, show(v)) for c, v in hl_updates.items())))
    print("%s: zmazať polygóny RECORDID=%s" % (HLAVNA, ", ".join(str(i) for i in other_ids)))
    if repoint:
        print("%s: polygóny RECORDID=%s mimo skupiny budú odkazovať [polygon_id_form]=%s"
              % (HLAVNA, ", ".join(str(i) for i in repoint), target_id))
    for t in (BIOTOPY, DRUHY, AKTIVITY, OPATRENIA):
        p = plan[t]
        if not (p["move"] or p["update"] or p["delete"]):
            continue
        print("%s: presunúť %d, upraviť %d, zmazať %d záznamov"
              % (t, len(p["move"]), len(p["update"]), len(p["delete"])))
        for fid, (row, upd) in p["update"].items():
            print("   fid=%s: %s" % (fid, ", ".join("[%s]=%s" % (c, show(v)) for c, v in upd.items())))
        if p["delete"]:
            print("   zmazať fid=%s" % ", ".join(str(r["fid"]) for r in p["delete"]))
    if conflicts:
        print("Vyriešené konflikty (%d):" % len(conflicts))
        for c in conflicts:
            print("   " + c)
    else:
        print("Konflikty: žiadne.")
    for w in warnings:
        print("UPOZORNENIE: " + w)

    if ask("\nZapísať zmeny do GPKG? [ano/nie]: ", "nie").lower() not in ("ano", "a", "y", "yes"):
        print("Zrušené – žiadne zmeny neboli zapísané.")
        return

    # ---- zápis ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.splitext(gpkg)[0] + "_backup_" + stamp + ".gpkg"
    shutil.copy2(gpkg, backup)
    print("Záloha vytvorená: %s" % backup)

    ds = ogr.Open(gpkg, 1)
    layer = ds.GetLayerByName(HLAVNA)
    ds.StartTransaction()
    try:
        feat = layer.GetFeature(target["fid"])
        feat.SetGeometry(union)
        for c, v in hl_updates.items():
            if v is None:
                feat.SetFieldNull(c)
            else:
                feat.SetField(c, v)
        layer.SetFeature(feat)

        for t in (BIOTOPY, DRUHY, AKTIVITY, OPATRENIA):
            p = plan[t]
            for r in p["move"]:
                run_sql(ds, "UPDATE %s SET fkRECORDID = %d WHERE fid = %d"
                        % (t, target_id, r["fid"]))
            for fid, (row, upd) in p["update"].items():
                run_sql(ds, "UPDATE %s SET %s WHERE fid = %d" % (
                    t, ", ".join('"%s" = %s' % (c, sql_literal(v)) for c, v in upd.items()), fid))
            for r in p["delete"]:
                run_sql(ds, "DELETE FROM %s WHERE fid = %d" % (t, r["fid"]))

        for rec in repoint:
            run_sql(ds, "UPDATE %s SET polygon_id_form = %d WHERE RECORDID = %d"
                    % (HLAVNA, target_id, rec))
        for r in others:
            layer.DeleteFeature(r["fid"])
        ds.CommitTransaction()
    except Exception as e:
        ds.RollbackTransaction()
        ds = None
        sys.exit("CHYBA pri zápise, zmeny vrátené späť: %s" % e)
    ds = None
    refresh_feature_counts(gpkg)

    # kontrola: po zlúčení nesmú ostať osirelé záznamy
    ds = ogr.Open(gpkg, 0)
    other_list = "(%s)" % ", ".join(str(i) for i in other_ids)
    _, left = read_rows(ds, HLAVNA, "RECORDID IN " + other_list)
    orphans = []
    for t in (BIOTOPY, DRUHY, AKTIVITY):
        _, rs = read_rows(ds, t, "fkRECORDID IN " + other_list)
        if rs:
            orphans.append("%s: %d" % (t, len(rs)))
    ds = None
    if left or orphans:
        print("UPOZORNENIE: po zlúčení ostali záznamy – polygóny: %d, osirelé: %s"
              % (len(left), ", ".join(orphans) or "-"))
    print("HOTOVO: polygóny %s zlúčené do RECORDID=%s."
          % (", ".join(str(i) for i in group_ids), target_id))


if __name__ == "__main__":
    main()
