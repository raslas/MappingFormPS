# -*- coding: utf-8 -*-
"""
09_import_zmeny.py – zapíše zmeny atribútov urobené na webe hotových území
(C:\\wamp64\\www\\MapovaniePrePSHotovo, stiahnuté v zmeny.php ako JSON) do GPKG.

Použitie:
    python 09_import_zmeny.py SKUEV0092 SKUEV0092_zmeny_20261022_1200.json
    python 09_import_zmeny.py SKUEV0092 zmeny.json --gpkg D:\\kopia\\MapovaniePrePS.gpkg
    python 09_import_zmeny.py SKUEV0092 zmeny.json --vsetko   # konflikty bez pýtania prepíše

GPKG: predvolene C:\\Users\\RASLAS\\QField\\cloud\\SKUEV####\\MapovaniePrePS.gpkg
(ten istý súbor, z ktorého číta 08_web_hotovo.py).

Postup:
  1. každú zmenu nájde podľa [fid] v jej tabuľke (tblHabHlavna, tblHabBiotopy,
     tblHabBiotopyOpatrenia, tblHabDruhy, tblAktivity) a overí, že riadok patrí
     polygónu zo zmeny ([RECORDID], pri podriadených tabuľkách [fkRECORDID])
  2. porovná hodnotu v GPKG s hodnotami z webu:
       rovná pôvodnej  -> zmena sa zapíše
       rovná novej     -> už je zapísaná, len sa označí ako prenesená
       iná             -> konflikt: GPKG sa medzitým zmenilo (napr. v QField) –
                          skript sa opýta, či prepísať hodnotou z webu (predvolene nie)
  3. vypíše plán a po potvrdení vytvorí zálohu MapovaniePrePS_backup_*.gpkg
     a zapíše všetky zmeny v jednej transakcii; zmena [KOD] v tblHabDruhy
     nastaví aj [NAZOV_LAT] z tblHabDruhyLookup (ako formulár)
  4. do priečinka GPKG zapíše report SKUEV####_ZmenyReport_YYYYMMDD_HHMM.txt
     a výsledok SKUEV####_zmeny_vysledok_YYYYMMDD_HHMM.json – ten sa nahrá
     v zmeny.php a prenesené zmeny sa tam označia

Potom: python 08_web_hotovo.py SKUEV#### a nahrať databázu územia na web.

Vyžaduje: osgeo (GDAL) – interpreter s GDAL, napr.
  C:\\Users\\RASLAS\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

from osgeo import ogr

ogr.UseExceptions()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLOUD_DIR = r"C:\Users\RASLAS\QField\cloud"
GPKG_NAME = "MapovaniePrePS.gpkg"
FORMAT_VSTUP = "MapovaniePrePS-zmeny"
FORMAT_VYSLEDOK = "MapovaniePrePS-zmeny-vysledok"

# stĺpec s RECORDID polygónu, ktorému riadok patrí (opatrenia sa viažu na biotop)
OWNER_COLUMN = {
    "tblHabHlavna": "RECORDID",
    "tblHabBiotopy": "fkRECORDID",
    "tblHabBiotopyOpatrenia": None,
    "tblHabDruhy": "fkRECORDID",
    "tblAktivity": "fkRECORDID",
}
# stĺpce, ktoré web dovolí upraviť (lib.php UPRAVY) – iné sa nezapíšu
ALLOWED = {
    "tblHabHlavna": {"typ_polygon", "lokalita", "datum", "hlavny_mapovatel",
                     "druhy_mapovatel", "podlaorta", "e0", "e1", "e2", "e3",
                     "E1_invaz", "E2_invaz", "E3_invaz", "poznamka"},
    "tblHabBiotopy": {"biotop_cislo_new", "biotop_cislo", "biotop_pokryv",
                      "kvalita_biotopu_good", "kvalita_biotopu_bad",
                      "kvalita_biotopu_unsiut", "manazment_biotopu_vhod",
                      "manazment_biotopu_nevhod", "vyhliadky_biotopu_good",
                      "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut"},
    "tblHabBiotopyOpatrenia": {"kod_opatrenia", "percento_z_plochy_biotopu",
                               "detailny_opis_opatrenia"},
    "tblHabDruhy": {"KOD", "etaz", "POKRYVNOST", "pokryvnost_perc", "kod_kbx"},
    "tblAktivity": {"Aktivita", "Intenzita", "Perc_Plochy", "Vplyv"},
}
INTEGER_TYPES = (ogr.OFTInteger, ogr.OFTInteger64)


# ----------------------------------------------------------------------
# Pomocné funkcie
# ----------------------------------------------------------------------

def norm(v):
    """Hodnota na porovnanie: prázdne -> None, text bez okrajových medzier,
    číselný text ako číslo (50 == '50' == 50.0)."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            f = float(s.replace(",", "."))
            return int(f) if f.is_integer() else f
        except ValueError:
            return s
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def show(v):
    """Hodnota do konzoly a reportu – viacriadkový text na jednom riadku."""
    if v is None or v == "":
        return "<prázdne>"
    return str(v).replace("\r\n", "\n").replace("\n", " ⏎ ")


def ask(prompt, default):
    try:
        s = input(prompt).strip()
    except EOFError:
        print()
        return default
    return s or default


def to_field(value, ftype):
    """Hodnota pre stĺpec daného typu OGR (None = NULL)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if ftype in INTEGER_TYPES:
        return int(norm(value))
    if ftype == ogr.OFTReal:
        return float(norm(value))
    return str(value).strip()


def field_value(feature, name):
    idx = feature.GetFieldIndex(name)
    if idx < 0 or not feature.IsFieldSetAndNotNull(idx):
        return None
    return feature.GetField(idx)


# ----------------------------------------------------------------------
# Hlavná logika
# ----------------------------------------------------------------------

def load_changes(path, skuev):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit("Súbor %s sa nedá prečítať: %s" % (path, e))
    if not isinstance(data, dict) or data.get("format") != FORMAT_VSTUP:
        sys.exit("%s nie je súbor zmien z webu (zmeny.php -> stiahnuť JSON)." % path)
    if data.get("skuev") != skuev:
        sys.exit("Zmeny v %s patria územiu %s, nie %s." % (path, data.get("skuev"), skuev))
    zmeny = data.get("zmeny") or []
    if not zmeny:
        sys.exit("V %s nie sú žiadne zmeny." % path)
    return data, zmeny


def main():
    parser = argparse.ArgumentParser(
        description="Zapíše zmeny atribútov z webu hotových území do GPKG.")
    parser.add_argument("skuev", help="kód územia, napr. SKUEV0092")
    parser.add_argument("json", help="súbor zmien stiahnutý v zmeny.php")
    parser.add_argument("--gpkg", help="GPKG (predvolene %s\\SKUEV####\\%s)"
                        % (CLOUD_DIR, GPKG_NAME))
    parser.add_argument("--vsetko", action="store_true",
                        help="pri konflikte bez pýtania zapíše hodnotu z webu")
    args = parser.parse_args()

    skuev = args.skuev.strip().upper()
    if re.fullmatch(r"\d{1,4}", skuev):
        skuev = "SKUEV" + skuev.zfill(4)
    if not re.fullmatch(r"SKUEV\d{4}", skuev):
        sys.exit("Neplatný kód územia '%s' – očakávam SKUEV####." % args.skuev)
    gpkg = args.gpkg or os.path.join(CLOUD_DIR, skuev, GPKG_NAME)
    if not os.path.isfile(gpkg):
        sys.exit("Súbor neexistuje: %s" % gpkg)

    data, zmeny = load_changes(args.json, skuev)
    print("GPKG:   %s" % gpkg)
    print("Zmeny:  %s (%d, stiahnuté %s)" % (args.json, len(zmeny), data.get("vytvorene", "?")))

    ds = ogr.Open(gpkg, 0)
    layers = {}

    def layer(name):
        if name not in layers:
            layers[name] = ds.GetLayerByName(name)
        return layers[name]

    zapisat = []      # (zmena, nová hodnota pre GPKG)
    hotove = []       # už v GPKG
    konflikty = []    # (zmena, hodnota v GPKG)
    chyby = []        # (zmena, text)
    for z in sorted(zmeny, key=lambda x: x.get("id") or 0):
        tabulka, stlpec = z.get("tabulka"), z.get("stlpec")
        try:
            fid = int(z.get("gpkg_fid"))
        except (TypeError, ValueError):
            chyby.append((z, "chýba [fid]"))
            continue
        if stlpec not in ALLOWED.get(tabulka, ()):
            chyby.append((z, "stĺpec %s.%s sa z webu neupravuje" % (tabulka, stlpec)))
            continue
        lyr = layer(tabulka)
        if lyr is None:
            chyby.append((z, "v GPKG chýba tabuľka %s" % tabulka))
            continue
        defn = lyr.GetLayerDefn()
        if defn.GetFieldIndex(stlpec) < 0:
            chyby.append((z, "v %s chýba stĺpec %s" % (tabulka, stlpec)))
            continue
        feature = lyr.GetFeature(fid)
        if feature is None:
            chyby.append((z, "%s fid=%d v GPKG nie je (zmazaný záznam?)" % (tabulka, fid)))
            continue
        owner = OWNER_COLUMN[tabulka]
        if owner and z.get("recordid") is not None \
                and norm(field_value(feature, owner)) != norm(z["recordid"]):
            chyby.append((z, "%s fid=%d patrí polygónu %s=%s, nie %s – GPKG sa zmenilo, "
                             "zmenu treba urobiť ručne"
                          % (tabulka, fid, owner, show(field_value(feature, owner)),
                             z["recordid"])))
            continue
        aktualna = field_value(feature, stlpec)
        ftype = defn.GetFieldDefn(defn.GetFieldIndex(stlpec)).GetType()
        try:
            nova = to_field(z.get("nova_hodnota"), ftype)
        except (TypeError, ValueError):
            chyby.append((z, "hodnota '%s' sa nehodí do stĺpca %s" % (z.get("nova_hodnota"), stlpec)))
            continue
        if norm(aktualna) == norm(nova):
            hotove.append(z)
        elif norm(aktualna) == norm(z.get("stara_hodnota")):
            zapisat.append((z, nova))
        else:
            konflikty.append((z, aktualna, nova))

    # ---- konflikty ----
    vyriesene = []
    ponechane = []
    for z, aktualna, nova in konflikty:
        print("\n  KONFLIKT #%s: %s" % (z.get("id"), z.get("popis")))
        print("    pôvodne na webe: %s" % show(z.get("stara_hodnota")))
        print("    teraz v GPKG:    %s" % show(aktualna))
        print("    nová z webu:     %s  (%s, %s)" % (show(nova), z.get("autor"), z.get("upravene")))
        if args.vsetko:
            volba = "2"
        else:
            print("    1) nechať hodnotu v GPKG")
            print("    2) zapísať hodnotu z webu")
            volba = ask("  Voľba [1]: ", "1")
            while volba not in ("1", "2"):
                volba = ask("  Neplatná voľba, 1 alebo 2 [1]: ", "1")
        if volba == "2":
            vyriesene.append((z, nova))
        else:
            ponechane.append((z, aktualna))
    zapisat.extend(vyriesene)

    # [NAZOV_LAT] podľa nového [KOD] (ako formulár: z tblHabDruhyLookup)
    nazvy = {}
    kody = {nova for z, nova in zapisat if z["tabulka"] == "tblHabDruhy"
            and z["stlpec"] == "KOD" and nova is not None}
    lookup = layer("tblHabDruhyLookup")
    if kody and lookup is not None:
        lookup.SetAttributeFilter("Tax_id IN (%s)" % ", ".join(str(int(k)) for k in kody))
        for f in lookup:
            nazvy[int(f.GetField("Tax_id"))] = f.GetField("Taxon_meno")
        lookup.SetAttributeFilter(None)
    ds = None

    # ---- plán ----
    print("\n================ PLÁN IMPORTU ZMIEN ================")
    print("Zapísať do GPKG:             %d" % len(zapisat))
    for z, nova in zapisat:
        extra = ""
        if z["tabulka"] == "tblHabDruhy" and z["stlpec"] == "KOD":
            extra = "  (+ [NAZOV_LAT] = %s)" % show(nazvy.get(nova))
        print("   #%-5s %s: %s -> %s%s" % (z.get("id"), z.get("popis"),
                                          show(z.get("stara_hodnota")), show(nova), extra))
    print("Už v GPKG (len označiť):     %d" % len(hotove))
    print("Konflikt, ponechané v GPKG:  %d" % len(ponechane))
    for z, aktualna in ponechane:
        print("   #%-5s %s: v GPKG %s, z webu %s"
              % (z.get("id"), z.get("popis"), show(aktualna), show(z.get("nova_hodnota"))))
    print("Chyby (nezapísané):          %d" % len(chyby))
    for z, text in chyby:
        print("   #%-5s %s: %s" % (z.get("id"), z.get("popis"), text))

    backup = None
    if zapisat:
        if ask("\nZapísať zmeny do GPKG? [ano/nie]: ", "nie").lower() not in ("ano", "a", "y", "yes"):
            print("Zrušené – GPKG sa nezmenilo, výsledok sa nezapísal.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.splitext(gpkg)[0] + "_backup_" + stamp + ".gpkg"
        shutil.copy2(gpkg, backup)
        print("Záloha vytvorená: %s" % backup)

        ds = ogr.Open(gpkg, 1)
        ds.StartTransaction()
        try:
            for z, nova in zapisat:
                lyr = ds.GetLayerByName(z["tabulka"])
                feature = lyr.GetFeature(int(z["gpkg_fid"]))
                indices = [feature.GetFieldIndex(z["stlpec"])]
                values = {z["stlpec"]: nova}
                if z["tabulka"] == "tblHabDruhy" and z["stlpec"] == "KOD" \
                        and feature.GetFieldIndex("NAZOV_LAT") >= 0:
                    values["NAZOV_LAT"] = nazvy.get(nova)
                    indices.append(feature.GetFieldIndex("NAZOV_LAT"))
                for name, value in values.items():
                    if value is None:
                        feature.SetFieldNull(name)
                    else:
                        feature.SetField(name, value)
                if hasattr(lyr, "UpdateFeature"):   # GDAL >= 3.7: len zmenené stĺpce
                    lyr.UpdateFeature(feature, indices, [], False)
                else:
                    lyr.SetFeature(feature)
            ds.CommitTransaction()
        except Exception as e:
            ds.RollbackTransaction()
            ds = None
            sys.exit("CHYBA pri zápise, GPKG ostalo bez zmien: %s" % e)
        ds = None

    # ---- report a výsledok pre zmeny.php ----
    cas = datetime.now()
    folder = os.path.dirname(os.path.abspath(gpkg))
    vysledok_path = os.path.join(folder, "%s_zmeny_vysledok_%s.json"
                                 % (skuev, cas.strftime("%Y%m%d_%H%M")))
    report_path = os.path.join(folder, "%s_ZmenyReport_%s.txt" % (skuev, cas.strftime("%Y%m%d_%H%M")))
    vysledok = {
        "format": FORMAT_VYSLEDOK,
        "verzia": 1,
        "skuev": skuev,
        "gpkg": gpkg,
        "zaloha": backup,
        "cas": cas.strftime("%Y-%m-%d %H:%M:%S"),
        "zdroj": os.path.abspath(args.json),
        "prenesene": [z.get("id") for z, _ in zapisat] + [z.get("id") for z in hotove],
        "konflikty": [{"id": z.get("id"), "gpkg_hodnota": a} for z, a in ponechane],
        "chyby": [{"id": z.get("id"), "chyba": t} for z, t in chyby],
    }
    with open(vysledok_path, "w", encoding="utf-8") as f:
        json.dump(vysledok, f, ensure_ascii=False, indent=1)

    lines = ["Import zmien z webu – %s" % skuev,
             "Čas:      %s" % vysledok["cas"],
             "GPKG:     %s" % gpkg,
             "Záloha:   %s" % (backup or "-"),
             "Zmeny:    %s" % vysledok["zdroj"],
             "",
             "ZAPÍSANÉ (%d):" % len(zapisat)]
    lines += ["  #%s %s: %s -> %s (%s)" % (z.get("id"), z.get("popis"), show(z.get("stara_hodnota")),
                                          show(n), z.get("autor")) for z, n in zapisat]
    lines += ["", "UŽ BOLI V GPKG (%d):" % len(hotove)]
    lines += ["  #%s %s: %s" % (z.get("id"), z.get("popis"), show(z.get("nova_hodnota")))
              for z in hotove]
    lines += ["", "KONFLIKT – PONECHANÁ HODNOTA V GPKG (%d):" % len(ponechane)]
    lines += ["  #%s %s: GPKG %s, web %s" % (z.get("id"), z.get("popis"), show(a),
                                             show(z.get("nova_hodnota"))) for z, a in ponechane]
    lines += ["", "CHYBY (%d):" % len(chyby)]
    lines += ["  #%s %s: %s" % (z.get("id"), z.get("popis"), t) for z, t in chyby]
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\nReport:   %s" % report_path)
    print("Výsledok: %s" % vysledok_path)
    print("HOTOVO: zapísané %d, už v GPKG %d, konflikty ponechané %d, chyby %d."
          % (len(zapisat), len(hotove), len(ponechane), len(chyby)))
    print("Ďalej: python 08_web_hotovo.py %s, nahrať databázu na web a v zmeny.php "
          "nahrať výsledok – %d zmien sa označí ako prenesené."
          % (skuev, len(vysledok["prenesene"])))


if __name__ == "__main__":
    main()
