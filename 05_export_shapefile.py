# -*- coding: utf-8 -*-
"""
Export GIS shapefilu (Output A z EXPORT_SPEC.md) pre zadané územie SKUEV####.

Jeden riadok = jeden polygón (tblHabHlavna), s dominantným biotopom
(riadok tblHabBiotopy s najvyšším biotop_pokryv). Stĺpce sú premenované na
zmluvné názvy (≤10 znakov kvôli DBF), kvalita je zložená do jednej hodnoty,
interné polia sa vynechávajú.

Zdroj údajov:  C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\MapovaniePrePS.gpkg
Výstup:        <export>\\SKUEV####\\SKUEV####_N.shp  (+ .dbf/.shx/.prj/.cpg)
CRS:           EPSG:5514 (S-JTSK / Krovak East North)

Rozhodnutia k medzerám G1–G6 z EXPORT_SPEC.md:
  G1  ID_polygon = KOD_UEV + '_N_' + XX + '_' + YYY
        XX  = 2-miestny kód mapovateľa z tblMappers (Access šablóna). Berie sa
              autoritatívny mapovateľ územia zo skuev_mapovatel.txt; ak sa
              nenájde, skúsi sa [hlavny_mapovatel] daného riadku; inak '00'.
        YYY = RECORDID doplnené na 3 miesta (RECORDID je jedinečný, na rozdiel
              od polygon_id, ktorý sa môže opakovať).
  G2  mapovatel = [hlavny_mapovatel]; [druhy_mapovatel] ostáva len v Access DB.
  G3  biotopy   = zoznam všetkých biotopov 'kódNový:%' oddelený '; ' (aj
                  dominantný), zoradený podľa pokryvu zostupne. Kód = 2023
                  [biotop_cislo_new] (ak chýba, náhradou 2002 [biotop_cislo]).
  G4  kvalita   = pre každý biotop kombinácia nenulových kategórií kvality
                  'D:%,Z:%,N:%' (good→D, bad→Z, unsiut→N), biotopy oddelené '; '
                  v rovnakom poradí ako [biotopy]. Príklad: 'D:50,N:50; D:100'.
  G5  premenovanie stĺpcov podľa zmluvy.
  G6  datum ostáva ako text dd.MM.rrrr.

Prepojené formuláre ([polygon_id_form]):
  Ak má riadok tblHabHlavna vyplnené [polygon_id_form], jeho údaje boli zapísané
  do formulára iného polygónu. Taký riadok sa exportuje s VLASTNOU geometriou a
  VLASTNÝM [ID_polygon], ale všetky ostatné údaje sa prevezmú zo zdrojového
  polygónu, ktorého [RECORDID] = [polygon_id_form] – teda aj biotopy
  (Biotop, Biotop_new, Biotop_p, kvalita, biotopy) z jeho tblHabBiotopy.
  Zdrojový polygón sa exportuje aj sám za seba, nič sa nevynecháva.
  Ak je hodnota zdroja prázdna, ponechá sa vlastná hodnota riadku.
  Odkaz sa hľadá podľa [RECORDID] (jedinečný); ak sa nenájde, skúsi sa ešte
  [polygon_id] (nie je jedinečné) a zapíše sa upozornenie. Reťazené odkazy sa
  sledujú až po koncový zdroj, s ochranou proti cyklu.

Nové stĺpce (skrátené na 10 znakov kvôli DBF):
  pid_form  = [polygon_id_form] daného riadku (vlastná hodnota, nie zo zdroja)
  typ_polyg = [typ_polygon] (pri prepojenom formulári zo zdrojového polygónu)

Vyžaduje: osgeo (GDAL) – interpreter s GDAL, napr.
  C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
"""

import os
import re
import sqlite3
import sys
from datetime import datetime

import pyodbc
from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
EXPORT_DIR = os.path.dirname(os.path.abspath(__file__))
GPKG_NAME = "MapovaniePrePS.gpkg"
TEMPLATE_ACCDB = r"C:\_projects\MapovaniePrePS\formular\mapovanie_biotopov_7.accdb"
MAPOVATEL_FILE = os.path.join(EXPORT_DIR, "skuev_mapovatel.txt")

# kategórie kvality (G4): stĺpec -> skratka, v pevnom poradí D, Z, N
KVALITA_CAT = [
    ("kvalita_biotopu_good", "D"),
    ("kvalita_biotopu_bad", "Z"),
    ("kvalita_biotopu_unsiut", "N"),
]

# definícia výstupných polí: (názov ≤10, ogr typ, šírka, presnosť)
FIELDS = [
    ("ID_polygon", ogr.OFTString, 20, 0),
    ("Kod_UEV", ogr.OFTString, 12, 0),
    ("mapovatel", ogr.OFTString, 80, 0),
    ("datum", ogr.OFTString, 10, 0),
    ("poznamky", ogr.OFTString, 254, 0),
    ("Biotop", ogr.OFTString, 20, 0),
    ("Biotop_new", ogr.OFTString, 20, 0),
    ("Biotop_p", ogr.OFTReal, 10, 2),
    ("kvalita", ogr.OFTString, 254, 0),  # G4: kombinácia po biotopoch
    ("biotopy", ogr.OFTString, 254, 0),  # G3: 'biotopy_all' skrátené na ≤10
    ("pid_form", ogr.OFTInteger, 10, 0),  # [polygon_id_form]
    ("typ_polyg", ogr.OFTString, 1, 0),   # [typ_polygon]
]


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def ask_skuev(argv):
    raw = (argv[1] if len(argv) > 1 else
           input("Zadaj kód územia SKUEV (napr. SKUEV0862): ")).strip().upper()
    if re.fullmatch(r"\d{4}", raw):
        raw = "SKUEV" + raw
    if not re.fullmatch(r"SKUEV\d{4}", raw):
        sys.exit("Neplatný kód '%s' – očakávam SKUEV#### (napr. SKUEV0862)." % raw)
    return raw


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


def load_site_mapper_names():
    """{SKUEV####: meno} zo skuev_mapovatel.txt (autoritatívny mapovateľ)."""
    result = {}
    try:
        with open(MAPOVATEL_FILE, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2 and re.fullmatch(r"SKUEV\d{4}", parts[0]):
                    result[parts[0]] = parts[1].strip()
    except OSError:
        pass
    return result


def load_mappers():
    """{meno: id} z tblMappers (Access šablóna)."""
    result = {}
    try:
        acc = pyodbc.connect(
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=%s;"
            % TEMPLATE_ACCDB)
        for mid, name in acc.cursor().execute(
                "SELECT ID, mapovatel FROM tblMappers"):
            if name is not None:
                result[str(name).strip()] = mid
        acc.close()
    except Exception as e:
        print("UPOZORNENIE: tblMappers sa nepodarilo načítať (%s). XX bude '00'."
              % e)
    return result


def mapper_code(name, mappers):
    """2-miestny kód mapovateľa; presná zhoda, potom prefix; inak None."""
    if not name:
        return None
    name = str(name).strip()
    if name in mappers:
        return mappers[name]
    for full, mid in mappers.items():
        if full.startswith(name) or name.startswith(full):
            return mid
    return None


def kvalita_combo(b):
    """Kombinácia nenulových kategórií kvality biotopu (G4), napr. 'D:50,N:50'.
    Prázdny reťazec, ak sú všetky 0/None."""
    parts = []
    for col, short in KVALITA_CAT:
        val = b[col] or 0
        if val > 0:
            parts.append("%s:%s" % (short, _num(val)))
    return ",".join(parts)


def biotopy_summary(biotopy):
    """(dominantný riadok, text [biotopy], text [kvalita]) pre polygón.

    Biotopy zoradené podľa pokryvu zostupne. [biotopy] používa 2023 kód
    (náhradou 2002). [kvalita] je zarovnaná: i-ty segment kvality patrí
    i-temu biotopu."""
    if not biotopy:
        return None, "", ""
    ordered = sorted(
        biotopy,
        key=lambda b: (-(b["biotop_pokryv"] or 0), b["id"] or 0))
    biotopy_parts = []
    kvalita_parts = []
    for b in ordered:
        code = b["biotop_cislo_new"] or b["biotop_cislo"] or "?"
        pok = b["biotop_pokryv"]
        biotopy_parts.append("%s:%s" % (code, "" if pok is None else _num(pok)))
        kvalita_parts.append(kvalita_combo(b))
    kvalita_text = "; ".join(kvalita_parts)
    if not kvalita_text.replace(";", "").strip():
        kvalita_text = ""  # žiadna kategória nikde vyplnená
    return ordered[0], "; ".join(biotopy_parts), kvalita_text


def _num(x):
    """Číslo bez zbytočnej desatinnej nuly."""
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def is_empty(v):
    """None alebo prázdny/whitespace text."""
    return v is None or (isinstance(v, str) and not v.strip())


def pick(src, own, col):
    """Hodnota zo zdrojového riadku; ak je prázdna, vlastná hodnota riadku."""
    v = src[col]
    return own[col] if is_empty(v) else v


def single_polygon(geom):
    """MultiPolygon s 1 časťou → Polygon. Vráti (geom, je_viaccastove)."""
    if geom is None:
        return None, False
    if geom.GetGeometryType() in (ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D):
        if geom.GetGeometryCount() == 1:
            return geom.GetGeometryRef(0).Clone(), False
        return geom, True
    return geom, False


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def export_site(skuev, mappers, site_mappers):
    folder = os.path.join(CLOUD_DIR, skuev)
    gpkg = os.path.join(folder, GPKG_NAME)
    if not os.path.isfile(gpkg):
        print("PRESKOČENÉ %s: %s neexistuje." % (skuev, gpkg))
        return

    con = sqlite3.connect(gpkg)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # biotopy zoskupené podľa fkRECORDID
    biotopy_by_rec = {}
    for b in cur.execute("SELECT * FROM tblHabBiotopy"):
        biotopy_by_rec.setdefault(b["fkRECORDID"], []).append(b)

    hlavna = cur.execute(
        "SELECT * FROM tblHabHlavna ORDER BY polygon_id").fetchall()

    # [polygon_id_form] odkazuje na [RECORDID] (jedinečný). [polygon_id] nie je
    # jedinečné – polygóny pridané v QField preberajú polygon_id polygónu, z
    # ktorého boli rozdelené – preto slúži len ako záložné dohľadanie.
    hlavna_by_rec = {}
    hlavna_by_pid = {}
    for r in sorted(hlavna, key=lambda x: x["fid"]):
        if not is_empty(r["RECORDID"]):
            hlavna_by_rec.setdefault(str(r["RECORDID"]).strip(), []).append(r)
        if not is_empty(r["polygon_id"]):
            hlavna_by_pid.setdefault(str(r["polygon_id"]).strip(), []).append(r)

    # kód mapovateľa pre celé územie (G1)
    site_code = mapper_code(site_mappers.get(skuev), mappers)

    out_dir = os.path.join(EXPORT_DIR, 'export_for_SOP', skuev)
    os.makedirs(out_dir, exist_ok=True)
    shp_path = os.path.join(out_dir, skuev + "_N.shp")

    drv = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(shp_path):
        drv.DeleteDataSource(shp_path)
    ds = drv.CreateDataSource(shp_path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(5514)
    layer = ds.CreateLayer(skuev + "_N", srs, ogr.wkbPolygon,
                           options=["ENCODING=UTF-8"])
    for name, ftype, width, prec in FIELDS:
        fd = ogr.FieldDefn(name, ftype)
        if width:
            fd.SetWidth(width)
        if prec:
            fd.SetPrecision(prec)
        layer.CreateField(fd)
    ldefn = layer.GetLayerDefn()

    warnings = []
    seen_ids = {}
    n = 0
    n_linked = 0

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
            cands = hlavna_by_rec.get(ref)
            if not cands:
                # staršie/ručne zapísané hodnoty môžu odkazovať na [polygon_id]
                cands = hlavna_by_pid.get(ref)
                if cands:
                    warnings.append(
                        "%s: [polygon_id_form]='%s' nie je [RECORDID] – "
                        "dohľadané podľa [polygon_id] (fid=%s)."
                        % (polyname(row), ref, cands[0]["fid"]))
            if not cands:
                warnings.append(
                    "%s: [polygon_id_form]='%s', ale polygón s takým "
                    "[RECORDID] neexistuje – exportujú sa vlastné údaje."
                    % (polyname(row), ref))
                return current
            if len(cands) > 1:
                warnings.append(
                    "%s: [polygon_id_form]='%s' zodpovedá %d polygónom – "
                    "použitý fid=%s."
                    % (polyname(row), ref, len(cands), cands[0]["fid"]))
            nxt = cands[0]
            if nxt["fid"] in seen:
                warnings.append(
                    "%s: cyklus v [polygon_id_form] – prevzaté údaje z %s."
                    % (polyname(row), polyname(current)))
                return current
            seen.add(nxt["fid"])
            current = nxt
        return current

    for r in hlavna:
        pid = r["polygon_id"]
        recid = r["RECORDID"]

        # prepojený formulár: údaje sa preberajú zo zdrojového polygónu,
        # geometria a ID_polygon ostávajú vlastné
        src = source_row(r)
        linked = src["fid"] != r["fid"]
        if linked:
            n_linked += 1

        # kód mapovateľa: územný -> z riadku (resp. zo zdroja) -> 00
        mid = site_code
        if mid is None:
            mid = mapper_code(pick(src, r, "hlavny_mapovatel"), mappers)
        xx = "%02d" % mid if mid is not None else "00"
        if mid is None:
            warnings.append("polygon_id=%s: mapovateľ neurčený, XX='00'" % pid)
        # G1: číselná časť ID = RECORDID (jedinečný), nie polygon_id
        id_polygon = ("%s_N_%s_%03d" % (skuev, xx, recid)
                      if recid is not None else "%s_N_%s_000" % (skuev, xx))

        if id_polygon in seen_ids:
            warnings.append("DUPLICITNÉ ID_polygon %s (RECORDID=%s aj %s)"
                            % (id_polygon, seen_ids[id_polygon], recid))
        seen_ids[id_polygon] = recid

        # biotopy zo zdrojového polygónu; ak zdroj žiadne nemá, vlastné
        bio_rows = biotopy_by_rec.get(src["RECORDID"])
        if not bio_rows and linked:
            bio_rows = biotopy_by_rec.get(recid)
        dominant, biotopy_all, kvalita = biotopy_summary(bio_rows)
        if dominant is None:
            biotop = biotop_new = ""
            biotop_p = None
        else:
            biotop = dominant["biotop_cislo"] or ""
            biotop_new = dominant["biotop_cislo_new"] or ""
            biotop_p = dominant["biotop_pokryv"]

        typ_polygon = pick(src, r, "typ_polygon")
        if (linked and not is_empty(r["typ_polygon"])
                and not is_empty(src["typ_polygon"])
                and str(r["typ_polygon"]).strip() != str(src["typ_polygon"]).strip()):
            warnings.append(
                "%s: [typ_polygon]='%s' sa líši od zdroja %s ('%s') – "
                "exportovaný zdrojový typ."
                % (polyname(r), r["typ_polygon"], polyname(src),
                   src["typ_polygon"]))

        feat = ogr.Feature(ldefn)
        feat.SetField("ID_polygon", id_polygon)
        feat.SetField("Kod_UEV", pick(src, r, "KOD_UEV"))
        if not is_empty(pick(src, r, "hlavny_mapovatel")):
            feat.SetField("mapovatel", str(pick(src, r, "hlavny_mapovatel")))
        if not is_empty(pick(src, r, "datum")):
            feat.SetField("datum", str(pick(src, r, "datum")))
        if not is_empty(pick(src, r, "poznamka")):
            feat.SetField("poznamky", str(pick(src, r, "poznamka"))[:254])
        if not is_empty(r["polygon_id_form"]):
            try:
                feat.SetField("pid_form", int(str(r["polygon_id_form"]).strip()))
            except ValueError:
                warnings.append("%s: [polygon_id_form]='%s' nie je číslo."
                                % (polyname(r), r["polygon_id_form"]))
        if not is_empty(typ_polygon):
            feat.SetField("typ_polyg", str(typ_polygon).strip()[:1])
        if biotop:
            feat.SetField("Biotop", biotop)
        if biotop_new:
            feat.SetField("Biotop_new", biotop_new)
        if biotop_p is not None:
            feat.SetField("Biotop_p", float(biotop_p))
        if kvalita:
            feat.SetField("kvalita", kvalita[:254])
        if biotopy_all:
            feat.SetField("biotopy", biotopy_all[:254])

        geom = None
        wkb = gpkg_wkb(r["geom"])
        if wkb:
            geom = ogr.CreateGeometryFromWkb(wkb)
            geom, is_multi = single_polygon(geom)
            if is_multi:
                warnings.append(
                    "polygon_id=%s (%s): viaccastový MultiPolygon (%d častí) – "
                    "ponechaný ako multipolygon, treba opraviť v zdroji"
                    % (pid, id_polygon, geom.GetGeometryCount()))
        if geom is not None:
            feat.SetGeometry(geom)
        else:
            warnings.append("polygon_id=%s: chýba geometria" % pid)

        layer.CreateFeature(feat)
        feat = None
        n += 1

    ds = None
    con.close()

    print("\n%s -> %s" % (skuev, shp_path))
    print("   %d polygónov, biotopy pre %d polygónov"
          % (n, len(biotopy_by_rec)))
    print("   prepojených formulárov ([polygon_id_form]): %d" % n_linked)
    if warnings:
        print("   UPOZORNENIA (%d):" % len(warnings))
        for w in warnings[:40]:
            print("      -", w)
        if len(warnings) > 40:
            print("      ... a ďalších %d" % (len(warnings) - 40))
    else:
        print("   Bez upozornení.")

    # trvalý report vedľa shapefilu (konvencia z validacia.py)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    report = os.path.join(out_dir, "%s_N_ExportReport_%s.txt" % (skuev, stamp))
    with open(report, "w", encoding="utf-8") as f:
        f.write("Export shapefilu %s_N.shp\n" % skuev)
        f.write("Dátum: %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        f.write("Polygónov: %d | biotopy pre %d polygónov\n"
                % (n, len(biotopy_by_rec)))
        f.write("Prepojených formulárov ([polygon_id_form]): %d "
                "(údaje prevzaté zo zdrojového polygónu)\n" % n_linked)
        f.write("Kód mapovateľa územia (XX): %s\n\n"
                % ("%02d" % site_code if site_code is not None else "neurčený"))
        if warnings:
            f.write("UPOZORNENIA (%d):\n" % len(warnings))
            for w in warnings:
                f.write("   - %s\n" % w)
        else:
            f.write("Bez upozornení.\n")


def main():
    argv = sys.argv
    mappers = load_mappers()
    site_mappers = load_site_mapper_names()

    if len(argv) > 1 and argv[1].strip().upper() == "ALL":
        sites = sorted(
            d for d in os.listdir(CLOUD_DIR)
            if re.fullmatch(r"SKUEV\d{4}", d)
            and os.path.isfile(os.path.join(CLOUD_DIR, d, GPKG_NAME)))
        print("Spracúvam %d území: %s" % (len(sites), ", ".join(sites)))
        for s in sites:
            export_site(s, mappers, site_mappers)
    else:
        skuev = ask_skuev(argv)
        export_site(skuev, mappers, site_mappers)


if __name__ == "__main__":
    main()
