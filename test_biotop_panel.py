# -*- coding: utf-8 -*-
"""Testy panelu "Možné biotopy" (configure_forms.py) a 10_katalog_druhy.py – bez QGIS.

Z configure_forms.py sa číta len reťazec _BIOTOP_SCORE_CODE (cez ast, skript
samotný potrebuje QGIS) a overí sa aj, že vygenerovaný init kód formulára je
platný Python.
"""

import ast
import importlib.util
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))


def _constants():
    with open(os.path.join(HERE, "configure_forms.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {n.targets[0].id: n.value.value for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}


CONSTS = _constants()
panel = {}
exec(CONSTS["_BIOTOP_SCORE_CODE"], panel)

_spec = importlib.util.spec_from_file_location(
    "katalog_druhy", os.path.join(HERE, "10_katalog_druhy.py"))
katalog_druhy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(katalog_druhy)

# Tax_id: 1 Arrhenatherum elatius, 2 Trisetum flavescens, 3 Alnus glutinosa,
#         4 Salvia pratensis, 5 Trisetum flavescens subsp. x
KATALOG = {
    1: [("LKP01", "Arrhenatherum elatius", 1, 1, 1, frozenset())],
    2: [("LKP01", "Trisetum flavescens", 1, 0, 0, frozenset()),
        ("LKP02", "Trisetum flavescens", 0, 1, 0, frozenset())],
    3: [("LES01", "Alnus glutinosa", 1, 0, 1, frozenset({"E3"}))],
    4: [("TRA01", "Salvia pratensis", 0, 1, 0, frozenset())],
    5: [("LKP01", "Trisetum flavescens", 1, 0, 0, frozenset())],
}
BIOTOPY = {
    "LKP01": ("Nížinné a podhorské lúky", None, 19, 28, 6),
    "LKP02": ("Horské kosné lúky", None, 14, 24, 5),
    "LES01": ("Lužné lesy", None, 10, 10, 3),
    "TRA01": ("Suché trávniky", None, 20, 20, 4),
    "LKP03a": ("Mezofilné pasienky", "LKP03", 14, 13, 5),
    "LKP03": ("Mezofilné pasienky", None, 0, 0, 0),
}


def score(druhy, zapisane=()):
    return panel["score_biotopes"](druhy, KATALOG, BIOTOPY, zapisane)


def test_init_code_compiles():
    code = (CONSTS["_FORM_INIT_CODE"].replace("__BUTTON_LABEL__", "x")
            .replace("__BIOTOP_SCORE_CODE__", CONSTS["_BIOTOP_SCORE_CODE"]))
    compile(code, "<form init>", "exec")
    assert "def score_biotopes" in code and "__BIOTOP_SCORE_CODE__" not in code


def test_ranks_by_weighted_roles():
    results, missing = score([(1, "E1", "Arrhenatherum elatius"),
                              (2, "E1", "Trisetum flavescens"),
                              (4, "E1", "Salvia pratensis")])
    assert [r["kod"] for r in results] == ["LKP01", "LKP02", "TRA01"]
    best = results[0]
    # diag 2×3 + dom 1×2 + konšt 1×1
    assert best["skore"] == 9 and set(best["diag"]) == {"Arrhenatherum elatius",
                                                        "Trisetum flavescens"}
    assert missing == []


def test_catalog_species_counted_once_for_species_and_subspecies():
    results, _ = score([(2, "E1", "Trisetum flavescens"),
                        (5, "E1", "Trisetum flavescens subsp. x")])
    lkp01 = next(r for r in results if r["kod"] == "LKP01")
    assert len(lkp01["diag"]) == 1
    assert lkp01["diag"]["Trisetum flavescens"] == ["Trisetum flavescens",
                                                    "Trisetum flavescens subsp. x"]


def test_respects_catalog_stratum():
    assert score([(3, "E1", "Alnus glutinosa")])[0] == []
    assert [r["kod"] for r in score([(3, "E3", "Alnus glutinosa")])[0]] == ["LES01"]
    assert [r["kod"] for r in score([(3, None, "Alnus glutinosa")])[0]] == ["LES01"]


def test_recorded_biotope_marked_and_missing_reported():
    results, missing = score([(1, "E1", "Arrhenatherum elatius")], ["lkp01", "TRA01"])
    assert results[0]["zapisany"] is True
    assert missing == ["TRA01"]


def test_parent_code_counts_as_recorded():
    biotopy = dict(BIOTOPY)
    katalog = {9: [("LKP03a", "Cynosurus cristatus", 1, 0, 0, frozenset())]}
    results, missing = panel["score_biotopes"]([(9, "E1", "Cynosurus cristatus")],
                                               katalog, biotopy, ["LKP03"])
    assert results[0]["zapisany"] is True and missing == []


def test_render_html_links_and_warnings():
    results, missing = score([(1, "E1", "Arrhenatherum <x>")], ["TRA01", "ABC99"])
    html = panel["render_html"](results, missing, BIOTOPY, 1)
    assert 'href="http://localhost/bot/?biotope=katalog:LKP01"' in html
    assert "diag 1/19" in html and "dom 1/6" in html and "konšt 1/28" in html
    assert "Arrhenatherum &lt;x&gt;" not in html  # vypisujú sa názvy z katalógu
    assert "TRA01</b> nemá medzi druhmi" in html
    assert "ABC99 nie je kód" in html
    assert panel["render_html"]([], [], BIOTOPY, 0).startswith("<i>Polygón zatiaľ")


def test_render_html_limits_rows():
    results = [{"kod": "B%02d" % i, "nazov": "", "diag": {"x": []}, "konst": {}, "dom": {},
                "n_diag": 1, "n_konst": 0, "n_dom": 0, "skore": 20 - i,
                "zapisany": i == 10} for i in range(12)]
    html = panel["render_html"](results, [], {}, 5, max_rows=3)
    assert "B02" in html and "B03" not in html
    assert "B10" in html  # zapísaný sa ukáže vždy
    assert "ďalších 8 biotopov" in html


def _bot_db(tmp_path):
    db = tmp_path / "bot.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE biotopes (biotope_key TEXT, source TEXT, code TEXT, name TEXT,
                               group_name TEXT, sort_order INTEGER, details_json TEXT);
        CREATE TABLE biotope_species (biotope_key TEXT, species_id INTEGER,
            species_name TEXT, role TEXT, role_order INTEGER, note TEXT, sort_order INTEGER);
        CREATE TABLE names (species_id INTEGER, name TEXT, name_role TEXT);
        CREATE TABLE source_records (source TEXT, details_json TEXT);
        INSERT INTO biotopes VALUES
          ('katalog:LES01', 'katalog', 'LES01', 'Lužné lesy', 'Lesy', 1, '{"parent": null}'),
          ('katalog:LKP03a', 'katalog', 'LKP03a', 'Pasienky', 'Lúky', 2,
           '{"parent": {"code": "LKP03"}}'),
          ('katalog2002:Lk1', 'katalog2002', 'Lk1', 'Staré', 'Lúky', 3, '{}');
        INSERT INTO biotope_species VALUES
          ('katalog:LES01', 10, 'Salix alba (E3', 'Diagnostický a dominantný', 5, '', 1),
          ('katalog:LES01', NULL, 'E2)', 'Diagnostický a dominantný', 5, '', 2),
          ('katalog:LES01', 11, 'Eriophorum vaginatum', 'Konštantný', 2, '', 3),
          ('katalog:LKP03a', 12, 'Achillea millefolium agg.', 'Diagnostický', 1, '', 1),
          ('katalog:LKP03a', 13, 'Sesleria albicans', 'Dominantný', 3, 'E1', 2),
          ('katalog2002:Lk1', 99, 'Salix alba', 'Diagnostický', 1, '', 1);
        INSERT INTO names VALUES
          (10, 'Salix alba', 'accepted'),
          (11, 'Eriophorum vaginatum', 'accepted'),
          (11, 'Eriophorum polystachion', 'synonym'),
          (14, 'Eriophorum latifolium', 'accepted'),
          (14, 'Eriophorum polystachion', 'synonym'),
          (13, 'Sesleria albicans', 'accepted'),
          (13, 'Sesleria caerulea', 'synonym'),
          (13, 'Salix alba', 'misapplied');
        INSERT INTO source_records VALUES ('katalog',
          '{"fields": [{"label": "Taxonomická skupina", "value": "Achillea millefolium agg."},
                       {"label": "Zahrnuté taxóny", "value": "Achillea collina; Achillea millefolium"}]}');
    """)
    con.commit()
    con.close()
    return db


def test_builder_matches_species_aggregates_and_synonyms(tmp_path):
    bot = sqlite3.connect(_bot_db(tmp_path))
    biotopy, druhy = katalog_druhy.read_catalog(bot)
    assert [b["kod"] for b in biotopy] == ["LES01", "LKP03a"]
    assert biotopy[1]["nadradeny"] == "LKP03"
    salix = next(d for d in druhy if d["druh"] == "Salix alba")
    assert salix["etaz"] == {"E2", "E3"} and (salix["diag"], salix["dom"]) == (1, 1)
    assert len(druhy) == 4  # 'E2)' nie je samostatný druh

    taxa = [(1, "Salix alba"), (2, "Achillea collina"), (3, "Sesleria caerulea"),
            (4, "Eriophorum latifolium"), (5, "Salix alba subsp. vitellina"), (6, None)]
    rows = katalog_druhy.match(taxa, druhy, katalog_druhy.read_groups(bot),
                               katalog_druhy.read_names(bot))
    got = {(r[0], r[1], r[2]) for r in rows}
    assert (1, "LES01", "Salix alba") in got
    assert (2, "LKP03a", "Achillea millefolium agg.") in got   # člen agregátu
    assert (3, "LKP03a", "Sesleria albicans") in got           # synonymum
    assert (5, "LES01", "Salix alba") in got                   # poddruh -> druh
    # spoločné synonymum nesmie spojiť dva rôzne druhy
    assert not any(r[0] == 4 for r in rows)
    # nesprávne použité meno ('misapplied') sa neberie
    assert (1, "LKP03a", "Sesleria albicans") not in got
    assert {r[6] for r in rows if r[2] == "Salix alba"} == {"E2,E3"}
    assert katalog_druhy.counts(biotopy, druhy)["LES01"] == (1, 1, 1)
