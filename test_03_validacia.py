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
