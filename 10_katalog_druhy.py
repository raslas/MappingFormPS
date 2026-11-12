# -*- coding: utf-8 -*-
"""Vytvorí KatalogDruhy.gpkg – druhy biotopov Katalógu biotopov 2023 pre formulár.

Zdroj je databáza webu Botany Explorer (C:\\wamp64\\www\\bot\\data\\botany_app.sqlite),
do ktorej sa katalóg spracoval. Druhy katalógu sa vopred prevedú na [Tax_id]
číselníka tblHabDruhyLookup, takže formulár porovnáva len čísla s [KOD]
v tblHabDruhy. Panel "Možné biotopy" na karte Druhy (configure_forms.py)
z toho určí, na ktoré biotopy ukazujú zapísané druhy.

Druh z terénu sa s druhom katalógu zhoduje, keď
  - majú rovnaký názov (bez 'agg.', 'sp.', medzier – species_key z 03_validacia.py),
  - poddruh sa hľadá aj ako druh a druh aj ako agregát, do ktorého patrí,
  - alebo je názov z terénu synonymom druhu katalógu v databáze webu
    (nesprávne použité mená – 'misapplied' – sa neberú).

Výstup (prepíše sa celý, ostatné súbory sa len čítajú):
  KatalogDruhy  – Tax_id, kod_2023, druh_katalog, diag/konst/dom (0/1), etaz
  KatalogBiotopy – kod_2023, nazov_2023, skupina, nadradeny, n_diag/n_konst/n_dom

Spustenie (po každej prestavbe databázy webu alebo zmene číselníka druhov):
    python 10_katalog_druhy.py [cesta\\botany_app.sqlite]
"""

import importlib.util
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DB = r"C:\wamp64\www\bot\data\botany_app.sqlite"
LOOKUP_GPKG = os.path.join(SCRIPT_DIR, "MapovaniePrePS.gpkg")
OUT_GPKG = os.path.join(SCRIPT_DIR, "KatalogDruhy.gpkg")

# normalizácia názvov je tá istá ako pri dopĺňaní [kod_kbx] vo validácii
_spec = importlib.util.spec_from_file_location(
    "validacia", os.path.join(SCRIPT_DIR, "03_validacia.py"))
_validacia = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validacia)
species_key = _validacia.species_key
species_keys = _validacia.species_keys


def roles(role):
    """'Diagnostický a dominantný' -> (diag, konst, dom) ako 0/1."""
    r = (role or "").lower()
    return (int("diagnost" in r), int("konštant" in r or "konstant" in r),
            int("dominant" in r))


def read_catalog(bot):
    """Biotopy a ich druhy z databázy webu.

    Vráti (biotopy, druhy):
      biotopy = [{kod, nazov, skupina, nadradeny, poradie}]
      druhy   = [{kod, druh, species_id, diag, konst, dom, etaz: set}]
    Názvy rozdelené pri extrakcii z PDF ('Salix alba (E3' a ďalší záznam 'E2)')
    sa spoja do jedného druhu s oboma etážami.
    """
    biotopy = []
    for kod, nazov, skupina, poradie, details in bot.execute(
            "SELECT code, name, group_name, sort_order, details_json FROM biotopes "
            "WHERE source = 'katalog' ORDER BY sort_order"):
        parent = (json.loads(details).get("parent") or {}).get("code") or None
        biotopy.append({"kod": kod, "nazov": nazov, "skupina": skupina,
                        "nadradeny": parent, "poradie": poradie})

    druhy = []
    last = None
    for key, name, species_id, role, note in bot.execute(
            "SELECT s.biotope_key, s.species_name, s.species_id, s.role, s.note "
            "FROM biotope_species s JOIN biotopes b USING (biotope_key) "
            "WHERE b.source = 'katalog' ORDER BY s.biotope_key, s.sort_order"):
        kod = key.split(":", 1)[1]
        m = re.fullmatch(r"(E\d)\)", name.strip())
        if m:
            if last is not None and last["kod"] == kod:
                last["etaz"].add(m.group(1))
            continue
        etaz = set()
        if note and re.fullmatch(r"E\d", note.strip()):
            etaz.add(note.strip())
        m = re.search(r"\((E\d)", name)
        if m:
            etaz.add(m.group(1))
        diag, konst, dom = roles(role)
        last = {"kod": kod, "druh": re.sub(r"\s*\((E\d.*)?$", "", name.strip()),
                "species_id": species_id, "diag": diag, "konst": konst,
                "dom": dom, "etaz": etaz}
        druhy.append(last)
    return biotopy, druhy


def read_groups(bot):
    """{kľúč taxónu: {kľúče agregátov}} z 'Zahrnuté taxóny' katalógu."""
    groups = {}
    for (details,) in bot.execute(
            "SELECT details_json FROM source_records WHERE source = 'katalog' "
            "AND details_json LIKE '%Zahrnut%'"):
        fields = {f.get("label"): f.get("value")
                  for f in json.loads(details).get("fields", [])}
        group, taxa = fields.get("Taxonomická skupina"), fields.get("Zahrnuté taxóny")
        if not group or not taxa:
            continue
        for taxon in taxa.split(";"):
            if taxon.strip():
                groups.setdefault(species_key(taxon), set()).add(species_key(group))
    return groups


def read_names(bot):
    """Kľúč názvu -> {species_id} databázy webu.

    Platné mená (a agregáty) majú prednosť: species_id podľa synonyma sa vráti
    len pre názov, ktorý nie je platným menom žiadneho druhu. Synonymá sa totiž
    prekrývajú ('Eriophorum polystachion' je synonymom viacerých druhov).
    Nesprávne použité mená ('misapplied') sa neberú vôbec.
    """
    accepted, synonyms = {}, {}
    for species_id, name, role in bot.execute(
            "SELECT species_id, name, name_role FROM names WHERE name_role <> 'misapplied'"):
        target = synonyms if role == "synonym" else accepted
        target.setdefault(species_key(name), set()).add(species_id)
    return {k: accepted.get(k) or synonyms[k] for k in set(accepted) | set(synonyms)}


def match(taxa, druhy, groups, names_by_key):
    """Priradí druhy katalógu taxónom číselníka.

    taxa: [(Tax_id, Taxon_meno)]. Vráti zoznam riadkov KatalogDruhy
    (Tax_id, kod, druh, diag, konst, dom, etaz) – jeden na dvojicu taxón × druh
    katalógu v biotope. Synonymá sa používajú len jedným smerom (názov z terénu
    -> druh webu -> jeho záznamy v katalógu), nie reťazením cez ďalšie synonymá.
    """
    by_key, by_sid = {}, {}
    for i, d in enumerate(druhy):
        by_key.setdefault(species_key(d["druh"]), set()).add(i)
        if d["species_id"] is not None:
            by_sid.setdefault(d["species_id"], set()).add(i)

    rows = []
    for tax_id, meno in taxa:
        if not meno or not str(meno).strip():
            continue
        keys = species_keys(str(meno), groups)
        sids = set().union(*(names_by_key.get(k, set()) for k in keys))
        hits = set().union(*(by_key.get(k, set()) for k in keys),
                           *(by_sid.get(s, set()) for s in sids))
        for i in sorted(hits):
            d = druhy[i]
            rows.append((tax_id, d["kod"], d["druh"], d["diag"], d["konst"], d["dom"],
                         ",".join(sorted(d["etaz"])) or None))
    return rows


def counts(biotopy, druhy):
    """Počet druhov katalógu v biotope podľa roly -> {kod: (n_diag, n_konst, n_dom)}."""
    result = {b["kod"]: [0, 0, 0] for b in biotopy}
    for d in druhy:
        c = result.setdefault(d["kod"], [0, 0, 0])
        c[0] += d["diag"]
        c[1] += d["konst"]
        c[2] += d["dom"]
    return {k: tuple(v) for k, v in result.items()}


def write_gpkg(path, biotopy, n_roles, rows, source):
    """Zapíše GeoPackage len s tabuľkami atribútov (bez geometrie)."""
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA application_id = 1196444487")  # 'GPKG'
        con.execute("PRAGMA user_version = 10300")
        con.executescript("""
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
                organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL, description TEXT);
            INSERT INTO gpkg_spatial_ref_sys VALUES
                ('Undefined cartesian SRS', -1, 'NONE', -1, 'undefined', NULL),
                ('Undefined geographic SRS', 0, 'NONE', 0, 'undefined', NULL);
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
                identifier TEXT UNIQUE, description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
                srs_id INTEGER REFERENCES gpkg_spatial_ref_sys(srs_id));
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL, column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL, m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
            CREATE TABLE "KatalogBiotopy" (
                "fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                "kod_2023" TEXT, "nazov_2023" TEXT, "skupina" TEXT, "nadradeny" TEXT,
                "n_diag" MEDIUMINT, "n_konst" MEDIUMINT, "n_dom" MEDIUMINT,
                "poradie" MEDIUMINT);
            CREATE TABLE "KatalogDruhy" (
                "fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                "Tax_id" MEDIUMINT, "kod_2023" TEXT, "druh_katalog" TEXT,
                "diag" MEDIUMINT, "konst" MEDIUMINT, "dom" MEDIUMINT, "etaz" TEXT);
            CREATE INDEX "KatalogDruhy_Tax_id" ON "KatalogDruhy" ("Tax_id");
        """)
        note = "Katalóg biotopov 2023 z %s, %s" % (source, datetime.now().strftime("%Y-%m-%d %H:%M"))
        con.executemany(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES (?, 'attributes', ?, ?, 0)",
            [("KatalogBiotopy", "KatalogBiotopy", note), ("KatalogDruhy", "KatalogDruhy", note)])
        con.executemany(
            "INSERT INTO KatalogBiotopy (kod_2023, nazov_2023, skupina, nadradeny, "
            "n_diag, n_konst, n_dom, poradie) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(b["kod"], b["nazov"], b["skupina"], b["nadradeny"], *n_roles[b["kod"]],
              b["poradie"]) for b in biotopy])
        con.executemany(
            "INSERT INTO KatalogDruhy (Tax_id, kod_2023, druh_katalog, diag, konst, dom, etaz) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


def main(argv):
    bot_path = argv[1] if len(argv) > 1 else BOT_DB
    for p in (bot_path, LOOKUP_GPKG):
        if not os.path.exists(p):
            sys.exit("Súbor neexistuje: %s" % p)

    bot = sqlite3.connect("file:%s?mode=ro" % bot_path, uri=True)
    try:
        biotopy, druhy = read_catalog(bot)
        groups = read_groups(bot)
        names_by_key = read_names(bot)
    finally:
        bot.close()

    lkp = sqlite3.connect("file:%s?mode=ro" % LOOKUP_GPKG, uri=True)
    try:
        taxa = lkp.execute("SELECT Tax_id, Taxon_meno FROM tblHabDruhyLookup "
                           "WHERE Tax_id IS NOT NULL").fetchall()
    finally:
        lkp.close()

    rows = match(taxa, druhy, groups, names_by_key)
    write_gpkg(OUT_GPKG, biotopy, counts(biotopy, druhy), rows, bot_path)

    matched = {r[0] for r in rows}
    covered = {(r[1], r[2]) for r in rows}
    print("Biotopov: %d, druhov v biotopoch: %d" % (len(biotopy), len(druhy)))
    print("Taxónov číselníka: %d, z toho v katalógu: %d" % (len(taxa), len(matched)))
    print("Druhov katalógu bez taxónu v číselníku: %d"
          % len({(d["kod"], d["druh"]) for d in druhy} - covered))
    print("Zapísané: %s (%d riadkov)" % (OUT_GPKG, len(rows)))


if __name__ == "__main__":
    main(sys.argv)
