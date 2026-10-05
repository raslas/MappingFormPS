# -*- coding: utf-8 -*-
"""
Kontrola fotiek v podpriečinku "f" územia SKUEV.

Pre zadané SKUEV#### prejde všetky obrázky v
C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\f:

  - obrázok v inom formáte ako JPEG (HEIC, PNG…)  -> skonvertuje sa na JPEG
    (vrátane EXIF údajov) a originál sa zmaže
  - polygón sa určí primárne z názvu súboru: prvé číslo v názve je RECORDID
        (napr. "2_3.JPG" -> polygón s RECORDID 2, "IMG_0001.jpg" -> RECORDID 1)
        RECORDID je jedinečné a je to zároveň hodnota, ktorú mapovateľ vidí ako
        štítok polygónu v QFielde (nie [polygon_id], ktoré sa môže opakovať).
  - ak názov súboru nezodpovedá žiadnemu RECORDID v tblHabHlavna, polygón
        sa skúsi určiť priestorovým prekryvom podľa GPS súradníc (geotagu)
  - každý polygón z tblHabHlavna s prázdnym [polygon_id_form] musí mať aspoň
        jednu fotku v priečinku "f"; inak je to chyba
  - fotky sa nepremenúvajú, okrem zmeny formátu na JPEG

Výsledok sa uloží ako tabuľka oddelená tabulátormi
SKUEV####_FotoTable_YYYYMMDD_HHMM.txt v priečinku SKUEV#### so stĺpcami:
    RECORDID       – RECORDID určeného polygónu
    original_name  – pôvodný názov obrázka
    note           – poznámka (napr. "missing geotag")
Tabuľku číta 06_naplnenie_access.py (stĺpce podľa hlavičky).
Na konzolu sa vypíše len krátke zhrnutie.

Vyžaduje: Pillow, pillow-heif, shapely, pyproj.
"""

import os
import re
import struct
import sqlite3
import sys
from datetime import datetime

from pillow_heif import register_heif_opener
from PIL import Image, ExifTags
import shapely.wkb
from shapely.geometry import Point
from pyproj import Transformer

register_heif_opener()

# slovenské znaky aj v konzole s cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
GPKG_NAME = "MapovaniePrePS.gpkg"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
MAX_DIST_M = 100.0
NEAR_DIST_M = 10.0  # v tomto okruhu má prednosť najmenší polygón pred najbližším


def is_empty(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


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


def load_polygons(gpkg_path):
    """Načíta polygóny, SRS, všetky RECORDID a RECORDID vyžadujúce fotku."""
    con = sqlite3.connect(gpkg_path)
    srs = con.execute(
        "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name='tblHabHlavna'"
    ).fetchone()[0]
    polys = []
    all_recs = set()
    required_recs = set()
    missing_required_rec_fids = []
    for fid, rec, pid_form, blob in con.execute(
            "SELECT fid, RECORDID, polygon_id_form, geom FROM tblHabHlavna"):
        if rec is not None:
            all_recs.add(rec)
        if is_empty(pid_form):
            if rec is None:
                missing_required_rec_fids.append(fid)
            else:
                required_recs.add(rec)
        wkb = gpkg_wkb(blob)
        if not wkb:
            continue
        try:
            polys.append((rec, fid, shapely.wkb.loads(wkb)))
        except Exception:
            pass
    con.close()
    return polys, srs, all_recs, required_recs, missing_required_rec_fids


def read_gps(path):
    """(lat, lon) vo WGS84 z EXIF, alebo None."""
    with Image.open(path) as img:
        gps = img.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    lat, lon = gps.get(2), gps.get(4)
    if not lat or not lon or len(lat) != 3 or len(lon) != 3:
        return None
    lat_deg = float(lat[0]) + float(lat[1]) / 60 + float(lat[2]) / 3600
    lon_deg = float(lon[0]) + float(lon[1]) / 60 + float(lon[2]) / 3600
    if gps.get(1) == "S":
        lat_deg = -lat_deg
    if gps.get(3) == "W":
        lon_deg = -lon_deg
    return (lat_deg, lon_deg)


def convert_to_jpeg(foto_dir, images, errors):
    """Skonvertuje ne-JPEG obrázky na JPEG (s EXIF), originály zmaže.

    Vráti (nový zoznam súborov, zoznam hlásení o konverzii)."""
    result = []
    converted = []
    for fn in images:
        base, ext = os.path.splitext(fn)
        if ext.lower() in (".jpg", ".jpeg"):
            result.append(fn)
            continue
        src = os.path.join(foto_dir, fn)
        new_fn = base + ".jpg"
        dst = os.path.join(foto_dir, new_fn)
        if os.path.exists(dst):
            errors.append("%s: nedá sa skonvertovať, %s už existuje." % (fn, new_fn))
            result.append(fn)
            continue
        try:
            with Image.open(src) as img:
                exif = img.info.get("exif") or img.getexif().tobytes()
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(dst, "JPEG", quality=95, exif=exif)
        except Exception as e:
            errors.append("%s: konverzia na JPEG zlyhala (%s)." % (fn, e))
            if os.path.exists(dst):
                os.remove(dst)
            result.append(fn)
            continue
        os.remove(src)
        converted.append("%s  ->  %s" % (fn, new_fn))
        result.append(new_fn)
    return result, converted


def letter_suffix(i):
    """0->'a', 1->'b', … 25->'z', 26->'aa', …"""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(ord("a") + r) + s
    return s


def main():
    skuev = ask_skuev()
    folder = find_folder(skuev)
    foto_dir = os.path.join(folder, "f")
    if not os.path.isdir(foto_dir):
        sys.exit("Priečinok s fotkami %s neexistuje." % foto_dir)
    gpkg = os.path.join(folder, GPKG_NAME)
    if not os.path.isfile(gpkg):
        sys.exit("Súbor %s neexistuje." % gpkg)

    polys, srs, valid_recs, required_recs, missing_required_rec_fids = load_polygons(gpkg)
    if not polys:
        sys.exit("V tblHabHlavna nie sú žiadne polygóny s geometriou.")
    to_layer = Transformer.from_crs("EPSG:4326", "EPSG:%d" % srs, always_xy=True)

    errors = []
    assigned = {}      # RECORDID -> [názvy súborov v poradí]
    note_by_file = {}  # názov súboru -> poznámka
    table_errors = []  # (original_name, note) pre nepriradené obrázky
    missing_photo_recs = []

    for fid in missing_required_rec_fids:
        errors.append("tblHabHlavna fid=%s: [polygon_id_form] je prázdny, ale "
                      "[RECORDID] nie je vyplnený – nedá sa overiť fotka." % fid)

    images = sorted(
        f for f in os.listdir(foto_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
    if not images:
        errors.append("V priečinku %s nie sú žiadne obrázky." % foto_dir)
        converted = []
    else:
        images, converted = convert_to_jpeg(foto_dir, images, errors)

    for fn in images:
        m = re.search(r"\d+", os.path.splitext(fn)[0])
        if m:
            rec = int(m.group(0))
            if rec in valid_recs:
                note_by_file[fn] = "polygon from filename"
                assigned.setdefault(rec, []).append(fn)
                continue
            filename_note = "filename RECORDID %d not in tblHabHlavna" % rec
        else:
            filename_note = "no polygon number in filename"

        path = os.path.join(foto_dir, fn)
        try:
            gps = read_gps(path)
        except Exception as e:
            errors.append("%s: názov nezodpovedá [RECORDID] a obrázok sa nedá "
                          "prečítať (%s)." % (fn, e))
            table_errors.append((fn, "%s; cannot read image (%s)" % (filename_note, e)))
            continue
        if gps is None:
            errors.append("%s: názov nezodpovedá [RECORDID] a obrázok nemá GPS "
                          "súradnice (geotag) – nedá sa určiť polygón." % fn)
            table_errors.append((fn, "%s; missing geotag" % filename_note))
            continue

        x, y = to_layer.transform(gps[1], gps[0])   # lon, lat
        pt = Point(x, y)
        overlaps = [(rec, fid, geom) for rec, fid, geom in polys if geom.distance(pt) == 0]
        if not overlaps:
            errors.append("%s: názov nezodpovedá [RECORDID] a GPS súradnice sa "
                          "neprekrývajú so žiadnym polygónom." % fn)
            table_errors.append((fn, "%s; geotag does not overlap any polygon" % filename_note))
            continue
        best_rec, best_fid, _ = min(overlaps, key=lambda t: t[2].area)
        if best_rec is None:
            errors.append("%s: prekrytý polygón (fid=%s) nemá vyplnený [RECORDID] – "
                          "nedá sa zaradiť do tabuľky." % (fn, best_fid))
            table_errors.append((fn, "overlapped polygon (fid=%s) has no RECORDID" % best_fid))
            continue
        note_by_file[fn] = "%s; polygon from geotag overlap" % filename_note
        assigned.setdefault(best_rec, []).append(fn)

    for rec in sorted(required_recs):
        if rec not in assigned:
            errors.append("RECORDID=%s: polygón má prázdny [polygon_id_form], ale "
                          "v priečinku f nemá žiadnu fotku." % rec)
            missing_photo_recs.append(rec)

    # ---------- tabuľka (TSV) ----------
    # RECORDID | original_name | note
    table_rows = []
    for rec, files in sorted(assigned.items()):
        for fn in files:
            table_rows.append((str(rec), fn, note_by_file.get(fn, "")))
    for rec in sorted(missing_photo_recs):
        table_rows.append((str(rec), "", "missing image for polygon with empty polygon_id_form"))
    for fn, note in table_errors:
        table_rows.append(("", fn, note))
    table_rows.sort(key=lambda r: (r[1].lower(), r[0]))  # podľa original_name

    table_lines = ["\t".join(("RECORDID", "original_name", "note"))]
    table_lines.extend("\t".join(r) for r in table_rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    table_path = os.path.join(folder, "%s_FotoTable_%s.txt" % (skuev, stamp))
    with open(table_path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(table_lines) + "\n")

    print("Kontrola fotiek %s – obrázkov: %d, skonvertovaných: %d, chýb: %d."
          % (skuev, len(images), len(converted), len(errors)))
    print("Tabuľka uložená do: %s" % table_path)


if __name__ == "__main__":
    main()
