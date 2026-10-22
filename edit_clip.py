# -*- coding: utf-8 -*-
"""
edit_clip.py – vyrezanie polygónu vrstvy tblHabHlavna zo všetkých polygónov,
ktoré sa s ním prekrývajú (MapovaniePrePS.gpkg).

Použitie:
    python edit_clip.py <cesta_ku_gpkg> <ID>

    <cesta_ku_gpkg>  plná cesta k MapovaniePrePS.gpkg (napr. v priečinku
                     C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\)
    <ID>             [RECORDID] polygónu (číslo polygónu na mape), ktorý sa má
                     vyrezať z ostatných polygónov; sám ostáva nezmenený

Postup:
  1. nájde polygóny, ktoré sa so zadaným polygónom plošne prekrývajú
     (dotyk hranicou sa nepočíta)
  2. z každého odpočíta geometriu zadaného polygónu a prepočíta [p]
     (plocha v m² z novej geometrie)
  3. vypíše plán (pôvodná a nová plocha, počet častí) a po potvrdení zapíše
     zmeny (pred zápisom sa vytvorí záloha
     MapovaniePrePS_backup_YYYYMMDD_HHMMSS.gpkg)

Polygón, ktorý leží celý vnútri zadaného polygónu, by po vyrezaní nemal
žiadnu plochu – skript sa pri ňom opýta, či ho ponechať bez zmeny alebo
zmazať aj s podriadenými záznamami (tblHabBiotopy + opatrenia, tblHabDruhy,
tblAktivity). Podriadené záznamy ostatných polygónov sa nemenia.

Vyžaduje: osgeo (GDAL) – interpreter s GDAL, napr.
  C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
"""

import os
import shutil
import sqlite3
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

MIN_OVERLAP = 0.01   # m² – menší prekryv sa považuje za dotyk hranicou
SLIVER = 1.0         # m² – menšie časti výsledku sa hlásia ako úlomky


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


def valid(g):
    g = g.Clone()
    return g if g.IsValid() else g.MakeValid()


def polygons_only(g):
    """Z výsledku geometrickej operácie ponechá len plošné časti -> MultiPolygon."""
    if g is None or g.IsEmpty():
        return None
    parts = []
    gtype = ogr.GT_Flatten(g.GetGeometryType())
    if gtype == ogr.wkbPolygon:
        parts.append(g.Clone())
    elif gtype in (ogr.wkbMultiPolygon, ogr.wkbGeometryCollection):
        for i in range(g.GetGeometryCount()):
            sub = polygons_only(g.GetGeometryRef(i))
            if sub is not None:
                parts.extend(sub.GetGeometryRef(j).Clone()
                             for j in range(sub.GetGeometryCount()))
    if not parts:
        return None
    out = ogr.Geometry(ogr.wkbMultiPolygon)
    for p in parts:
        if p.GetArea() > 0:
            out.AddGeometry(p)
    return None if out.GetGeometryCount() == 0 else out


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


def child_counts(ds, recordid):
    """Počty podriadených záznamov polygónu {tabuľka: počet}."""
    counts = {}
    for t in (BIOTOPY, DRUHY, AKTIVITY):
        layer = ds.GetLayerByName(t)
        layer.SetAttributeFilter("fkRECORDID = %d" % recordid)
        counts[t] = layer.GetFeatureCount()
        layer.SetAttributeFilter(None)
    bio = ds.GetLayerByName(BIOTOPY)
    bio.SetAttributeFilter("fkRECORDID = %d" % recordid)
    ids = [f.GetField("id") for f in bio if not f.IsFieldNull("id")]
    bio.SetAttributeFilter(None)
    if ids:
        op = ds.GetLayerByName(OPATRENIA)
        op.SetAttributeFilter("fkHabBiotopyID IN (%s)" % ", ".join(str(i) for i in ids))
        counts[OPATRENIA] = op.GetFeatureCount()
        op.SetAttributeFilter(None)
    else:
        counts[OPATRENIA] = 0
    return counts


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def parse_args(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    gpkg, raw = argv[1], argv[2].strip()
    if not os.path.isfile(gpkg):
        sys.exit("Súbor neexistuje: %s" % gpkg)
    if not raw.isdigit():
        sys.exit("Neplatné ID polygónu '%s' – očakávam celé číslo (RECORDID)." % raw)
    return gpkg, int(raw)


def main():
    gpkg, clip_id = parse_args(sys.argv)

    ds = ogr.Open(gpkg, 0)
    if ds is None:
        sys.exit("GPKG sa nedá otvoriť: %s" % gpkg)
    layer = ds.GetLayerByName(HLAVNA)
    if layer is None:
        sys.exit("V GPKG chýba vrstva %s." % HLAVNA)

    layer.SetAttributeFilter("RECORDID = %d" % clip_id)
    clip_feats = [f for f in layer]
    layer.SetAttributeFilter(None)
    if not clip_feats:
        sys.exit("Polygón RECORDID=%s v %s neexistuje." % (clip_id, HLAVNA))
    if len(clip_feats) > 1:
        sys.exit("RECORDID=%s nie je jedinečné (fid %s) – najprv opraviť."
                 % (clip_id, ", ".join(str(f.GetFID()) for f in clip_feats)))
    clip_feat = clip_feats[0]
    if clip_feat.GetGeometryRef() is None:
        sys.exit("Polygón RECORDID=%s nemá geometriu." % clip_id)
    clip_geom = valid(clip_feat.GetGeometryRef())
    clip_area = clip_geom.GetArea()
    print("GPKG: %s" % gpkg)
    print("Vyrezávaný polygón RECORDID=%s (fid=%s), plocha %.0f m²"
          % (clip_id, clip_feat.GetFID(), clip_area))

    # ---- prekrývajúce sa polygóny ----
    layer.SetSpatialFilter(clip_geom)
    affected = []   # (fid, RECORDID, p, old_area, new_geom alebo None, overlap)
    for f in layer:
        if f.GetFID() == clip_feat.GetFID():
            continue
        g = f.GetGeometryRef()
        if g is None:
            continue
        g = valid(g)
        inter = polygons_only(g.Intersection(clip_geom))
        overlap = inter.GetArea() if inter is not None else 0.0
        if overlap <= MIN_OVERLAP:
            continue
        new_geom = polygons_only(g.Difference(clip_geom))
        rec = None if f.IsFieldNull("RECORDID") else f.GetField("RECORDID")
        p = None if f.IsFieldNull("p") else f.GetField("p")
        affected.append({
            "fid": f.GetFID(), "rec": rec, "p": p, "old_area": g.GetArea(),
            "overlap": overlap, "new_geom": new_geom,
            "new_area": new_geom.GetArea() if new_geom is not None else 0.0,
        })
    layer.SetSpatialFilter(None)

    if not affected:
        ds = None
        print("S polygónom RECORDID=%s sa neprekrýva žiadny iný polygón – nie je čo vyrezať."
              % clip_id)
        return

    # ---- polygóny, ktoré by po vyrezaní zanikli ----
    to_delete = []
    for a in affected:
        if a["new_geom"] is not None and a["new_area"] > MIN_OVERLAP:
            continue
        counts = child_counts(ds, a["rec"]) if a["rec"] is not None else {}
        print("\nPolygón RECORDID=%s (fid=%s, %.0f m²) leží celý vnútri polygónu %s – "
              "po vyrezaní by nemal žiadnu plochu." % (a["rec"], a["fid"], a["old_area"], clip_id))
        print("  Podriadené záznamy: %s" % ", ".join(
            "%s %d" % (t, n) for t, n in counts.items()) if counts else "  Bez RECORDID.")
        print("    1) ponechať bez zmeny")
        print("    2) zmazať polygón aj s podriadenými záznamami")
        while True:
            s = ask("  Voľba [1]: ", "1")
            if s in ("1", "2"):
                break
            print("  Neplatná voľba.")
        a["skip"] = (s == "1")
        a["children"] = counts
        if s == "2":
            to_delete.append(a)
    to_cut = [a for a in affected if a["new_geom"] is not None and a["new_area"] > MIN_OVERLAP]
    ds = None

    # ---- plán ----
    print("\n================ PLÁN VYREZANIA ================")
    print("%-9s %-6s %12s %12s %12s %6s %12s" % (
        "RECORDID", "fid", "plocha", "prekryv", "nová plocha", "častí", "staré [p]"))
    warnings = []
    for a in to_cut:
        parts = a["new_geom"].GetGeometryCount()
        print("%-9s %-6s %12.0f %12.0f %12.0f %6d %12s" % (
            a["rec"], a["fid"], a["old_area"], a["overlap"], a["new_area"], parts,
            "" if a["p"] is None else a["p"]))
        slivers = [a["new_geom"].GetGeometryRef(i).GetArea() for i in range(parts)
                   if a["new_geom"].GetGeometryRef(i).GetArea() < SLIVER]
        if parts > 1:
            warnings.append("RECORDID=%s: výsledok má %d častí%s." % (
                a["rec"], parts,
                " (z toho %d úlomkov < %.0f m²)" % (len(slivers), SLIVER) if slivers else ""))
    for a in to_delete:
        print("%-9s %-6s %12.0f %12.0f %12s  zmazať aj s podriadenými záznamami" % (
            a["rec"], a["fid"], a["old_area"], a["overlap"], "-"))
    skipped = [a for a in affected if a.get("skip")]
    if skipped:
        print("Bez zmeny ostanú: RECORDID=%s" % ", ".join(str(a["rec"]) for a in skipped))
    for w in warnings:
        print("UPOZORNENIE: " + w)
    if not to_cut and not to_delete:
        print("Nie je čo zapísať.")
        return

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
        for a in to_cut:
            f = layer.GetFeature(a["fid"])
            f.SetGeometry(a["new_geom"])
            f.SetField("p", int(round(a["new_area"])))
            layer.SetFeature(f)
        for a in to_delete:
            rec = a["rec"]
            if rec is not None:
                run_sql(ds, "DELETE FROM %s WHERE fkHabBiotopyID IN "
                            "(SELECT id FROM %s WHERE fkRECORDID = %d AND id IS NOT NULL)"
                        % (OPATRENIA, BIOTOPY, rec))
                for t in (BIOTOPY, DRUHY, AKTIVITY):
                    run_sql(ds, "DELETE FROM %s WHERE fkRECORDID = %d" % (t, rec))
                run_sql(ds, "UPDATE %s SET polygon_id_form = NULL WHERE polygon_id_form = %d"
                        % (HLAVNA, rec))
            layer.DeleteFeature(a["fid"])
        ds.CommitTransaction()
    except Exception as e:
        ds.RollbackTransaction()
        ds = None
        sys.exit("CHYBA pri zápise, zmeny vrátené späť: %s" % e)
    ds = None
    refresh_feature_counts(gpkg)

    print("HOTOVO: polygón RECORDID=%s vyrezaný z %d polygónov%s."
          % (clip_id, len(to_cut),
             ", zmazaných %d" % len(to_delete) if to_delete else ""))


if __name__ == "__main__":
    main()
