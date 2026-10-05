# -*- coding: utf-8 -*-
"""
Zabalí odovzdávaný balík územia SKUEV#### do jedného ZIP archívu.

Vstupy:
  <priečinok skriptu>\\export_for_SOP\\SKUEV####\\
        SKUEV####.accdb                        (06_naplnenie_access.py)
        SKUEV####_N.shp/.shx/.dbf/.prj/.cpg    (05_export_shapefile.py)
  <cloud>\\SKUEV####\\f\\                      fotky (04_validacia_foto.py)

Výstup:
  <priečinok skriptu>\\export_for_SOP\\SKUEV####.zip
  V archíve je jeden priečinok SKUEV#### a v ňom:
        SKUEV####.accdb
        SKUEV####_N.shp, .shx, .dbf, .prj, .cpg
        f/  – všetky fotky

Pravidlá:
  - chýbajúci povinný súbor (accdb alebo ktorákoľvek časť shapefilu) = územie
    sa nezabalí, vypíše sa čo chýba (balík musí byť kompletný),
  - z priečinka "f" sa berú len obrázky; iné súbory (napr. pomocný shapefile
    _f.shp) sa vynechajú a vypíšu,
  - prázdny/chýbajúci priečinok "f" je len upozornenie – úplnosť fotiek
    kontroluje 04_validacia_foto.py,
  - existujúci ZIP sa prepíše,
  - fotky sa ukladajú bez kompresie (JPEG sa už nezmenší), ostatné súbory
    s kompresiou.

Použitie:
    python 07_zip_for_SOP.py SKUEV0862
    python 07_zip_for_SOP.py ALL
"""

import os
import re
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
# rovnaký priečinok, do ktorého ukladajú 05_export_shapefile.py a
# 06_naplnenie_access.py
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "export_for_SOP")
FOTO_SUBDIR = "f"

SHP_EXTS = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}


def ask_skuev(argv):
    raw = (argv[1] if len(argv) > 1 else
           input("Zadaj kód územia SKUEV (napr. SKUEV0862): ")).strip().upper()
    if re.fullmatch(r"\d{4}", raw):
        raw = "SKUEV" + raw
    if not re.fullmatch(r"SKUEV\d{4}", raw):
        sys.exit("Neplatný kód '%s' – očakávam SKUEV#### (napr. SKUEV0862)." % raw)
    return raw


def required_files(skuev):
    """[(cesta, názov v archíve)] povinných súborov balíka."""
    src_dir = os.path.join(EXPORT_DIR, skuev)
    names = [skuev + ".accdb"] + [skuev + "_N" + ext for ext in SHP_EXTS]
    return [(os.path.join(src_dir, n), n) for n in names]


def photo_files(skuev):
    """(zoznam ciest k fotkám, zoznam vynechaných súborov, cesta k 'f')."""
    foto_dir = os.path.join(CLOUD_DIR, skuev, FOTO_SUBDIR)
    if not os.path.isdir(foto_dir):
        return [], [], foto_dir
    photos, skipped = [], []
    for name in sorted(os.listdir(foto_dir)):
        path = os.path.join(foto_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
            photos.append(path)
        else:
            skipped.append(name)
    return photos, skipped, foto_dir


def zip_site(skuev):
    """Zabalí jedno územie. Vráti True, ak archív vznikol."""
    print("\n=== %s " % skuev + "=" * max(0, 56 - len(skuev)))

    required = required_files(skuev)
    missing = [name for path, name in required if not os.path.isfile(path)]
    if missing:
        print("   PRESKOČENÉ – v %s chýba: %s"
              % (os.path.join(EXPORT_DIR, skuev), ", ".join(missing)))
        return False

    photos, skipped, foto_dir = photo_files(skuev)
    if not photos:
        print("   UPOZORNENIE: v %s nie sú žiadne fotky – archív bude bez "
              "priečinka '%s'." % (foto_dir, FOTO_SUBDIR))
    if skipped:
        print("   Vynechané (nie sú obrázky): %s" % ", ".join(skipped))

    zip_path = os.path.join(EXPORT_DIR, skuev + ".zip")
    if os.path.exists(zip_path):
        print("   Existujúci archív sa prepíše.")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in required:
            zf.write(path, "%s/%s" % (skuev, name))
        for path in photos:
            zf.write(path, "%s/%s/%s" % (skuev, FOTO_SUBDIR,
                                         os.path.basename(path)),
                     compress_type=zipfile.ZIP_STORED)

    size_mb = os.path.getsize(zip_path) / (1024.0 * 1024.0)
    print("   %s" % zip_path)
    print("   %d súborov (%d povinných + %d fotiek), %.1f MB"
          % (len(required) + len(photos), len(required), len(photos), size_mb))
    return True


def main():
    argv = sys.argv
    if not os.path.isdir(EXPORT_DIR):
        sys.exit("Priečinok %s neexistuje – spusť najprv 05 a 06." % EXPORT_DIR)

    if len(argv) > 1 and argv[1].strip().upper() == "ALL":
        sites = sorted(d for d in os.listdir(EXPORT_DIR)
                       if re.fullmatch(r"SKUEV\d{4}", d)
                       and os.path.isdir(os.path.join(EXPORT_DIR, d)))
        if not sites:
            sys.exit("V %s nie sú žiadne priečinky SKUEV####." % EXPORT_DIR)
        print("Spracúvam %d území: %s" % (len(sites), ", ".join(sites)))
    else:
        sites = [ask_skuev(argv)]

    done = [s for s in sites if zip_site(s)]
    print("\n" + "=" * 64)
    print("Zabalené: %d z %d" % (len(done), len(sites)))
    if len(done) != len(sites):
        print("Nezabalené: %s" % ", ".join(s for s in sites if s not in done))


if __name__ == "__main__":
    main()
