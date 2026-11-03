# -*- coding: utf-8 -*-
"""Testy dopĺňania [kod_kbx] v 03_validacia.py (bez GPKG a bez Accessu)."""

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "validacia", os.path.join(os.path.dirname(os.path.abspath(__file__)), "03_validacia.py"))
validacia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validacia)


def druh(nazov, etaz="E1", kod_kbx=None):
    return {"NAZOV_LAT": nazov, "etaz": etaz, "kod_kbx": kod_kbx}


def fills(rows, covers, diag, groups=None):
    result = validacia.find_kbx_fills(rows, covers, diag, groups or {})
    return {kod: [r["NAZOV_LAT"] for r in vybrane] for kod, vybrane in result.items()}


def test_species_key_handles_catalog_artefacts():
    key = validacia.species_key
    assert key("Spirodela poly rhiza") == key("Spirodela polyrhiza")
    assert key("Capsella bursa- -pastoris") == key("Capsella bursa-pastoris")
    assert key("Quercus robur agg. (E3") == key("Quercus robur")
    assert key("Alchemilla spec. div.") == key("Alchemilla sp.")


def test_species_keys_subspecies_and_aggregate():
    groups = {"achilleacollina": {"achilleamillefolium"}}
    assert "calthapalustris" in validacia.species_keys("Caltha palustris subsp. laeta", groups)
    assert "achilleamillefolium" in validacia.species_keys("Achillea collina", groups)
    assert "carexflava" not in validacia.species_keys("Carex hirta", groups)


def test_minority_is_below_top_cover_and_tie_returns_all():
    assert validacia.minority_biotopes({"VOD01": 99, "BRP08": 1}) == ["BRP08"]
    assert validacia.minority_biotopes({"A": 60, "B": 30, "C": 10}) == ["B", "C"]
    assert validacia.minority_biotopes({"B": 50, "A": 50}) == ["A", "B"]


def test_fills_only_minority_biotope():
    diag = {"VOD08": {"alismaplantago": {None}}, "VOD01": {"nupharlutea": {None}}}
    rows = [druh("Nuphar lutea"), druh("Alisma plantago"), druh("Rubus sp.")]
    assert fills(rows, {"VOD01": 90, "VOD08": 10}, diag) == {"VOD08": ["Alisma plantago"]}


def test_keeps_filled_kbx_and_rows_without_name():
    diag = {"VOD08": {"alismaplantago": {None}}}
    rows = [druh("Alisma plantago", kod_kbx="LKP01"), druh(None)]
    assert fills(rows, {"VOD01": 90, "VOD08": 10}, diag) == {}


def test_respects_catalog_stratum():
    diag = {"LES01": {"alnusglutinosa": {"E3"}}}
    rows = [druh("Alnus glutinosa", etaz="E1")]
    assert fills(rows, {"LKP01": 90, "LES01": 10}, diag) == {}
    rows = [druh("Alnus glutinosa", etaz="E3")]
    assert fills(rows, {"LKP01": 90, "LES01": 10}, diag) == {"LES01": ["Alnus glutinosa"]}


def test_skips_species_diagnostic_for_other_biotope_of_complex():
    diag = {"LKP01": {"dactylisglomerata": {None}},
            "LKP06": {"dactylisglomerata": {None}, "scirpussylvaticus": {None}}}
    rows = [druh("Dactylis glomerata"), druh("Scirpus sylvaticus")]
    assert fills(rows, {"LKP01": 70, "LKP06": 30}, diag) == {"LKP06": ["Scirpus sylvaticus"]}


# ----------------------------------------------------------------------
# Duplicity v tblAktivity / tblHabBiotopyOpatrenia (plan_dedup)
# ----------------------------------------------------------------------

AKT_KEY = ["fkRECORDID", "Aktivita"]
AKT_VAL = ["Intenzita", "Perc_Plochy", "Vplyv"]


def akt(fid, rec, aktivita, intenzita=None, perc=0, vplyv=None):
    return {"fid": fid, "fkRECORDID": rec, "Aktivita": aktivita,
            "Intenzita": intenzita, "Perc_Plochy": perc, "Vplyv": vplyv}


def dedup(rows):
    return validacia.plan_dedup(rows, AKT_KEY, AKT_VAL, ("Perc_Plochy",))


def test_dedup_exact_copies_keep_lowest_fid():
    merges, conflicts = dedup([akt(5, 4, "I", "B", 70, "n"), akt(9, 4, "I", "B", 70, "n"),
                               akt(7, 4, "K01.03", "B", 70, "n")])
    assert conflicts == []
    assert [(k, keep["fid"], [d["fid"] for d in drop], fill)
            for k, keep, drop, fill in merges] == [(("4", "I"), 5, [9], {})]


def test_dedup_fills_empty_values_and_ignores_case_and_zero_default():
    merges, conflicts = dedup([akt(1, 4, "a03.03 ", "B", 0, None),
                               akt(2, 4, "A03.03", None, 100, "n")])
    assert conflicts == []
    (key, keep, drop, fill), = merges
    assert key == ("4", "A03.03")
    # fid=2 má viac vyplnených stĺpcov (Perc_Plochy=0 je nevyplnené)
    assert keep["fid"] == 2 and [d["fid"] for d in drop] == [1]
    assert fill == {"Intenzita": "B"}


def test_dedup_conflicting_values_are_not_merged():
    merges, conflicts = dedup([akt(1, 46, "A03.02", "A", 10, "p"),
                               akt(2, 46, "A03.02", "C", 60, "p")])
    assert merges == []
    (key, group, diff_cols), = conflicts
    assert [r["fid"] for r in group] == [1, 2]
    assert diff_cols == ["Intenzita", "Perc_Plochy"]


def test_dedup_conflict_still_merges_identical_rows():
    merges, conflicts = dedup([akt(1, 7, "M01.02", "C", 50, "p"), akt(2, 7, "M01.02", "C", 50, "n"),
                               akt(3, 7, "M01.02", "C", "50", "p")])
    assert len(conflicts) == 1
    assert [(keep["fid"], [d["fid"] for d in drop]) for _, keep, drop, _ in merges] == [(1, [3])]


def test_dedup_skips_rows_without_key_and_other_polygons():
    merges, conflicts = dedup([akt(1, None, "I"), akt(2, None, "I"), akt(3, 4, ""), akt(4, 4, " "),
                               akt(5, 4, "I"), akt(6, 5, "I")])
    assert merges == [] and conflicts == []


def test_dedup_opatrenia_text_compared_without_case():
    rows = [{"fid": 1, "fkHabBiotopyID": 134, "kod_opatrenia": "NEL-01.01",
             "detailny_opis_opatrenia": "Hovädzí dobytok", "percento_z_plochy_biotopu": 100},
            {"fid": 2, "fkHabBiotopyID": 134, "kod_opatrenia": "NEL-01.01",
             "detailny_opis_opatrenia": "hovädzí dobytok ", "percento_z_plochy_biotopu": 100},
            {"fid": 3, "fkHabBiotopyID": 134, "kod_opatrenia": "NEL-01.01",
             "detailny_opis_opatrenia": None, "percento_z_plochy_biotopu": 100}]
    merges, conflicts = validacia.plan_dedup(
        rows, ["fkHabBiotopyID", "kod_opatrenia"],
        ["detailny_opis_opatrenia", "percento_z_plochy_biotopu"], ("percento_z_plochy_biotopu",))
    assert conflicts == []
    (_, keep, drop, fill), = merges
    assert keep["fid"] == 1 and [d["fid"] for d in drop] == [2, 3] and fill == {}
