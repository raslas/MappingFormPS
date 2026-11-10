# -*- coding: utf-8 -*-
"""Testy plánu zmeny RECORDID v edit_changeID.py (bez GDAL, sqlite v pamäti)."""

import importlib.util
import os
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location(
    "changeid", os.path.join(os.path.dirname(os.path.abspath(__file__)), "edit_changeID.py"))
changeid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changeid)


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE tblHabHlavna (fid INTEGER PRIMARY KEY, RECORDID INTEGER,
                                   polygon_id_form MEDIUMINT);
        CREATE TABLE tblHabBiotopy (fid INTEGER PRIMARY KEY, id MEDIUMINT, fkRECORDID INTEGER);
        CREATE TABLE tblHabDruhy (fid INTEGER PRIMARY KEY, KOD MEDIUMINT, fkRECORDID INTEGER);
        CREATE TABLE tblAktivity (fid INTEGER PRIMARY KEY, Aktivita TEXT, fkRECORDID INTEGER);
        INSERT INTO tblHabHlavna VALUES (1, 10, NULL), (2, 20, 10), (3, 30, NULL), (4, NULL, NULL);
        INSERT INTO tblHabBiotopy VALUES (1, 1, 10), (2, 2, 20);
        INSERT INTO tblHabDruhy VALUES (1, 100, 10), (2, 101, 10), (3, 102, 30);
        INSERT INTO tblAktivity VALUES (1, 'A01', 10);
    """)
    yield c
    c.close()


def apply(con, plan, move=True):
    for sql in changeid.write_sql(plan, move):
        con.execute(sql)


def test_change_moves_children_and_form_refs(con):
    plan = changeid.make_plan(con, 1, 99)
    assert plan["old"] == 10
    assert plan["children"] == {"tblHabBiotopy": 1, "tblHabDruhy": 2, "tblAktivity": 1}
    assert plan["form_refs"] == [(2, 20)]
    apply(con, plan)
    assert con.execute("SELECT RECORDID FROM tblHabHlavna WHERE fid = 1").fetchone()[0] == 99
    assert con.execute("SELECT COUNT(*) FROM tblHabDruhy WHERE fkRECORDID = 99").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM tblHabDruhy WHERE fkRECORDID = 30").fetchone()[0] == 1
    assert con.execute("SELECT polygon_id_form FROM tblHabHlavna WHERE fid = 2").fetchone()[0] == 99


def test_new_id_already_used_is_rejected(con):
    with pytest.raises(ValueError):
        changeid.make_plan(con, 1, 30)
    with pytest.raises(ValueError):
        changeid.make_plan(con, 1, 10)      # rovnaké ako staré
    with pytest.raises(ValueError):
        changeid.make_plan(con, 999, 50)    # neexistujúce fid


def test_empty_old_recordid_sets_only_main_row(con):
    plan = changeid.make_plan(con, 4, 40)
    assert plan["old"] is None
    assert changeid.write_sql(plan) == ["UPDATE tblHabHlavna SET RECORDID = 40 WHERE fid = 4"]


def test_duplicate_old_recordid_can_keep_children(con):
    con.execute("INSERT INTO tblHabHlavna VALUES (5, 10, NULL)")
    plan = changeid.make_plan(con, 5, 50)
    assert plan["shared"] == [1]
    apply(con, plan, move=False)
    assert con.execute("SELECT RECORDID FROM tblHabHlavna WHERE fid = 5").fetchone()[0] == 50
    assert con.execute("SELECT COUNT(*) FROM tblHabDruhy WHERE fkRECORDID = 10").fetchone()[0] == 2
    assert con.execute("SELECT polygon_id_form FROM tblHabHlavna WHERE fid = 2").fetchone()[0] == 10
