# configure_forms.py
#
# Run once in the QGIS Python console (Plugins > Python Console > open script).
# Regenerates all widget, alias, default-value, and form-layout configuration
# to exactly match the current MapovaciFormularPS.qgs project state.

from qgis.core import (
    QgsProject,
    QgsRelation,
    QgsVectorLayer,
    QgsEditorWidgetSetup,
    QgsEditFormConfig,
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsAttributeEditorRelation,
    QgsDefaultValue,
    QgsOptionalExpression,
    QgsExpression,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsProperty,
)
from qgis.PyQt.QtGui import QColor

project          = QgsProject.instance()
gpkg_path        = project.homePath() + "/MapovaniePrePS.gpkg"
aktlkp_gpkg_path = project.homePath() + "/AktivityLookup.gpkg"

# ── helpers ────────────────────────────────────────────────────────────────────

def get_or_load_layer(name, source_gpkg=None):
    layers = project.mapLayersByName(name)
    if layers:
        return layers[0]
    path = source_gpkg or gpkg_path
    layer = QgsVectorLayer(f"{path}|layername={name}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Cannot load layer '{name}' from {path}")
    project.addMapLayer(layer)
    print(f"  + loaded layer: {name}")
    return layer

def set_widget(layer, field, widget_type, config=None):
    idx = layer.fields().indexOf(field)
    if idx < 0:
        print(f"  WARNING: field not found: {layer.name()}.{field}")
        return
    layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup(widget_type, config or {}))

def set_hidden(layer, field):
    set_widget(layer, field, "Hidden")

def set_alias(layer, field, alias):
    idx = layer.fields().indexOf(field)
    if idx >= 0:
        layer.setFieldAlias(idx, alias)

def set_read_only(layer, field):
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return
    cfg = layer.editFormConfig()
    cfg.setReadOnly(idx, True)
    layer.setEditFormConfig(cfg)

def set_default(layer, field, expression, apply_on_update=False):
    idx = layer.fields().indexOf(field)
    if idx >= 0:
        layer.setDefaultValueDefinition(idx, QgsDefaultValue(expression, applyOnUpdate=apply_on_update))

def add_field(container, layer, field):
    idx = layer.fields().indexOf(field)
    if idx < 0:
        print(f"  WARNING: field missing in form: {layer.name()}.{field}")
        return
    container.addChildElement(QgsAttributeEditorField(field, idx, container))

def make_container(parent, name, ctype="groupbox", cols=1,
                   visibility=None, show_label=True):
    """
    ctype: "groupbox" | "tab" | "row"
    visibility: None (disabled) or QGIS expression string (enabled)
    """
    c = QgsAttributeEditorContainer(name, parent)
    if hasattr(QgsAttributeEditorContainer, "Tab"):
        # QGIS 3.32+: proper enum
        if ctype == "tab":
            c.setType(QgsAttributeEditorContainer.Tab)
        elif ctype == "row":
            c.setType(QgsAttributeEditorContainer.Row)
        else:
            c.setType(QgsAttributeEditorContainer.GroupBox)
    else:
        # Older QGIS: only groupbox vs tab
        c.setIsGroupBox(ctype == "groupbox")
    c.setColumnCount(cols)
    c.setShowLabel(show_label)
    if visibility is not None:
        c.setVisibilityExpression(QgsOptionalExpression(QgsExpression(visibility)))
    return c

def make_rel_editor(name, relation, parent, extra_cfg=None, widget_type_id=None):
    elem = QgsAttributeEditorRelation(name, relation, parent)
    cfg = {"buttons": "AllButtons"}
    if extra_cfg:
        cfg.update(extra_cfg)
    if hasattr(elem, "setRelationEditorConfiguration"):
        elem.setRelationEditorConfiguration(cfg)
    if widget_type_id and hasattr(elem, "setRelationWidgetTypeId"):
        elem.setRelationWidgetTypeId(widget_type_id)
    return elem

# ── load layers ────────────────────────────────────────────────────────────────

print("\n=== Loading layers ===")
hlavna         = get_or_load_layer("tblHabHlavna")
biotopy        = get_or_load_layer("tblHabBiotopy")
opatrenia      = get_or_load_layer("tblHabBiotopyOpatrenia")
druhy          = get_or_load_layer("tblHabDruhy")
aktivity       = get_or_load_layer("tblAktivity")
lkp_aktivita     = get_or_load_layer("tblAktivityLookup")
lkp_aktivita_new = get_or_load_layer("AktivityLookup", aktlkp_gpkg_path)
lkp_druhy      = get_or_load_layer("tblHabDruhyLookup")
lkp_biotop     = get_or_load_layer("tblHabBiotopyLookup")
lkp_biotop_new = get_or_load_layer("tblHabBiotopyNewLookup")

# ── relations ──────────────────────────────────────────────────────────────────

print("\n=== Defining relations ===")
rel_mgr = project.relationManager()

def make_relation(rel_id, name, parent_layer, parent_field, child_layer, child_field):
    existing = rel_mgr.relation(rel_id)
    if existing.isValid():
        rel_mgr.removeRelation(rel_id)
    r = QgsRelation()
    r.setId(rel_id)
    r.setName(name)
    r.setReferencedLayer(parent_layer.id())
    r.setReferencingLayer(child_layer.id())
    r.addFieldPair(child_field, parent_field)
    r.setStrength(QgsRelation.Association)
    if not r.isValid():
        print(f"  ERROR: invalid relation — {name}")
        return None
    rel_mgr.addRelation(r)
    print(f"  OK: {name}")
    return r

r_biotopy   = make_relation("r_hlavna_biotopy",    "Biotopy",   hlavna,  "fid", biotopy,   "fkRECORDID")
r_druhy     = make_relation("r_hlavna_druhy",      "Druhy",     hlavna,  "fid", druhy,     "fkRECORDID")
r_aktivity  = make_relation("r_hlavna_aktivity",   "Aktivity",  hlavna,  "fid", aktivity,  "fkRECORDID")
r_opatrenia = make_relation("r_biotopy_opatrenia", "Opatrenia", biotopy, "id", opatrenia, "fkHabBiotopyID")

# ── tblHabHlavna ───────────────────────────────────────────────────────────────

print("\n=== tblHabHlavna ===")

set_hidden(hlavna, "fid")
set_hidden(hlavna, "RECORDID")

for f in ["KOD_UEV", "polygon_id", "p", "podlaorta"]:
    set_read_only(hlavna, f)

for f, a in {
    "KOD_UEV":          "SKUEV",
    "podlaorta":        "Podľa orta",
    "poznamka":         "Poznámka",
    "p":                "Plocha (m²)",
    "polygon_id":       "ID polygónu",
    "datum":            "Dátum",
    "lokalita":         "Lokalita",
    "hlavny_mapovatel": "Mapovateľ",
    "druhy_mapovatel":  "Druhý mapovateľ",
    "e0":               "E0",
    "e1":               "E1",
    "e2":               "E2",
    "e3":               "E3",
    "E1_invaz":         "E1 invázky",
    "E2_invaz":         "E2 invázky",
    "E3_invaz":         "E3 invázky",
    "typ_polygon":      "Typ polygónu 'A' alebo 'B'",
    "polygon_id_form":  "ID rovnakého formulára",
}.items():
    set_alias(hlavna, f, a)

set_widget(hlavna, "datum", "DateTime", {
    "allow_null":             True,
    "calendar_popup":         True,
    "display_format":         "dd.MM.yyyy",
    "field_format":           "dd.MM.yyyy",
    "field_format_overwrite": False,
    "field_iso_format":       False,
})
set_widget(hlavna, "KOD_UEV",          "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_widget(hlavna, "polygon_id",       "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_widget(hlavna, "hlavny_mapovatel", "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_widget(hlavna, "druhy_mapovatel",  "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_widget(hlavna, "poznamka",         "TextEdit", {"IsMultiline": True,  "UseHtml": False})

for f in ["e0", "e1", "e2", "e3", "E1_invaz", "E2_invaz", "E3_invaz"]:
    set_widget(hlavna, f, "TextEdit", {"IsMultiline": False, "UseHtml": False})
    set_default(hlavna, f, "0")

# ValueMap stored as ordered list to preserve A/B order
set_widget(hlavna, "typ_polygon", "ValueMap", {
    "map": [{"A": "A"}, {"B": "B"}],
})
set_widget(hlavna, "polygon_id_form", "TextEdit", {"IsMultiline": False, "UseHtml": False})

# Form layout
cfg_h = hlavna.editFormConfig()
cfg_h.setLayout(QgsEditFormConfig.TabLayout)
root_h = cfg_h.invisibleRootContainer()
root_h.clear()

# ── Tab: Základné info ──
t_zak = make_container(root_h, "Základné info", "tab")
add_field(t_zak, hlavna, "typ_polygon")
add_field(t_zak, hlavna, "polygon_id_form")

# Unnamed 2-column GroupBox — hidden when polygon_id_form is filled (linked form)
grp_ids = make_container(t_zak, "", "groupbox", cols=2,
                         visibility='"polygon_id_form" is null')
for f in ["polygon_id", "podlaorta", "p", "datum", "lokalita", "hlavny_mapovatel"]:
    add_field(grp_ids, hlavna, f)
t_zak.addChildElement(grp_ids)

# Unnamed 1-column GroupBox — same visibility guard
grp_outer = make_container(t_zak, "", "groupbox", cols=1,
                           visibility='"polygon_id_form" is null')

# Vegetation cover — only for type A polygons
grp_pokryv = make_container(grp_outer, "Pokryvnosť etáží (%)", "groupbox", cols=2,
                             visibility=" \"typ_polygon\" = 'A'")
grp_vsetky = make_container(grp_pokryv, "Všetky druhy", "groupbox", cols=1)
for f in ["e0", "e1", "e2", "e3"]:
    add_field(grp_vsetky, hlavna, f)
grp_pokryv.addChildElement(grp_vsetky)

grp_invaz = make_container(grp_pokryv, "Invázne druhy", "groupbox", cols=1)
for f in ["E1_invaz", "E2_invaz", "E3_invaz"]:
    add_field(grp_invaz, hlavna, f)
grp_pokryv.addChildElement(grp_invaz)
grp_outer.addChildElement(grp_pokryv)

# Poznámka row (label suppressed — field label is enough)
row_pozn = make_container(grp_outer, "Poznámka", "row", show_label=False)
add_field(row_pozn, hlavna, "poznamka")
grp_outer.addChildElement(row_pozn)

t_zak.addChildElement(grp_outer)
root_h.addChildElement(t_zak)

# ── Tab: Biotopy (hidden for linked-form polygons) ──
t_bio = make_container(root_h, "Biotopy", "tab",
                       visibility='"polygon_id_form" is null')
if r_biotopy:
    t_bio.addChildElement(make_rel_editor(
        "r_hlavna_biotopy", r_biotopy, t_bio,
        extra_cfg={"allow_add_child_feature_with_no_geometry": False,
                   "show_first_feature": True},
        widget_type_id="relation_editor",
    ))
root_h.addChildElement(t_bio)

# ── Tabs: Druhy and Aktivity (type A only, not linked forms) ──
_VIS_A = " (\"typ_polygon\" = 'A') AND (\"polygon_id_form\" is null)"

t_druhy = make_container(root_h, "Druhy", "tab", visibility="True") #_VIS_A)
if r_druhy:
    t_druhy.addChildElement(make_rel_editor(
        "r_hlavna_druhy", r_druhy, t_druhy,
        extra_cfg={"allow_add_child_feature_with_no_geometry": False,
                   "show_first_feature": True},
        widget_type_id="relation_editor",
    ))
root_h.addChildElement(t_druhy)

t_akt = make_container(root_h, "Aktivity", "tab", visibility=_VIS_A)
if r_aktivity:
    t_akt.addChildElement(make_rel_editor("r_hlavna_aktivity", r_aktivity, t_akt))
root_h.addChildElement(t_akt)

hlavna.setEditFormConfig(cfg_h)

# ── tblHabBiotopy ──────────────────────────────────────────────────────────────

print("\n=== tblHabBiotopy ===")

set_hidden(biotopy, "fid")
set_hidden(biotopy, "fkRECORDID")
set_hidden(biotopy, "id")
# id is the parent key for the Opatrenia relation; auto-generate a unique integer.
set_default(biotopy, "id", "coalesce(maximum(\"id\") + 1, 1)")

for f, a in {
    "id":                       "Poradie biotopu",
    "biotop_cislo":             "Biotop – pôvodný kód",
    "biotop_pokryv":            "Pokryvnosť (%)",
    "biotop_cislo_new":         "Biotop – nový kód",
    "kvalita_biotopu_good":     "Dobrá",
    "kvalita_biotopu_bad":      "Zlá",
    "kvalita_biotopu_unsiut":   "Nevyhovujúca",
    "manazment_biotopu_vhod":   "Vhodný",
    "manazment_biotopu_nevhod": "Nevhodný",
    "vyhliadky_biotopu_good":   "Dobré",
    "vyhliadky_biotopu_bad":    "Zlé",
    "vyhliadky_biotopu_unsiut": "Nevyhovujúce",
}.items():
    set_alias(biotopy, f, a)

set_widget(biotopy, "biotop_cislo", "ValueRelation", {
    "Layer": lkp_biotop.id(), "Key": "code", "Value": "biotop_name",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
})
set_widget(biotopy, "biotop_cislo_new", "ValueRelation", {
    "Layer": lkp_biotop_new.id(), "Key": "codenew", "Value": "biotopnew_name",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
})
set_widget(biotopy, "biotop_pokryv", "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_default(biotopy, "biotop_pokryv", "100")

for f in ["kvalita_biotopu_good", "kvalita_biotopu_bad", "kvalita_biotopu_unsiut",
          "manazment_biotopu_vhod", "manazment_biotopu_nevhod",
          "vyhliadky_biotopu_good", "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut"]:
    set_widget(biotopy, f, "TextEdit", {"IsMultiline": False, "UseHtml": False})
    set_default(biotopy, f, "0")

# Form layout
cfg_b = biotopy.editFormConfig()
cfg_b.setLayout(QgsEditFormConfig.TabLayout)
root_b = cfg_b.invisibleRootContainer()
root_b.clear()

# Kvalita/Manažment/Vyhliadky hidden for type-B polygons (no quality assessment needed)
_VIS_NOT_B = (
    "attribute(get_feature('tblHabHlavna', 'polygon_id', \"fkRECORDID\"), 'typ_polygon') != 'B'"
)

grp_bio = make_container(root_b, "Biotop")
for f in ["biotop_cislo", "biotop_pokryv", "biotop_cislo_new"]:
    add_field(grp_bio, biotopy, f)
root_b.addChildElement(grp_bio)

grp_kval = make_container(root_b, "Kvalita biotopu", visibility=_VIS_NOT_B)
for f in ["kvalita_biotopu_good", "kvalita_biotopu_bad", "kvalita_biotopu_unsiut"]:
    add_field(grp_kval, biotopy, f)
root_b.addChildElement(grp_kval)

grp_man = make_container(root_b, "Manažment", visibility=_VIS_NOT_B)
for f in ["manazment_biotopu_vhod", "manazment_biotopu_nevhod"]:
    add_field(grp_man, biotopy, f)
root_b.addChildElement(grp_man)

grp_vyh = make_container(root_b, "Vyhliadky", visibility=_VIS_NOT_B)
for f in ["vyhliadky_biotopu_good", "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut"]:
    add_field(grp_vyh, biotopy, f)
root_b.addChildElement(grp_vyh)

if r_opatrenia:
    grp_opatr = make_container(root_b, "Opatrenia")
    grp_opatr.addChildElement(make_rel_editor("r_biotopy_opatrenia", r_opatrenia, grp_opatr))
    root_b.addChildElement(grp_opatr)

biotopy.setEditFormConfig(cfg_b)

# ── tblHabBiotopyOpatrenia ─────────────────────────────────────────────────────

print("\n=== tblHabBiotopyOpatrenia ===")

set_hidden(opatrenia, "fid")
set_hidden(opatrenia, "fkHabBiotopyID")

for f, a in {
    "kod_opatrenia":             "Kód opatrenia",
    "detailny_opis_opatrenia":   "Podrobný opis",
    "percento_z_plochy_biotopu": "% z plochy biotopu",
}.items():
    set_alias(opatrenia, f, a)

set_widget(opatrenia, "kod_opatrenia", "ValueRelation", {
    "Layer": lkp_aktivita.id(), "Key": "node_code", "Value": "namex",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
})
set_widget(opatrenia, "detailny_opis_opatrenia",   "TextEdit", {"IsMultiline": True,  "UseHtml": False})
set_widget(opatrenia, "percento_z_plochy_biotopu", "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_default(opatrenia, "percento_z_plochy_biotopu", "0")

# ── tblAktivity ────────────────────────────────────────────────────────────────

print("\n=== tblAktivity ===")

set_hidden(aktivity, "fid")
set_hidden(aktivity, "fkRECORDID")

for f, a in {
    "Aktivita":    "Aktivita",
    "Intenzita":   "Intenzita",
    "Perc_Plochy": "% plochy",
    "Vplyv":       "Vplyv",
}.items():
    set_alias(aktivity, f, a)

set_widget(aktivity, "Aktivita", "ValueRelation", {
    "Layer": lkp_aktivita_new.id(), "Key": "kod", "Value": "namex",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
})
set_widget(aktivity, "Intenzita", "ValueMap", {
    "map": {"A – vysoká": "A", "B – stredná": "B", "C – nízka": "C"},
})
set_widget(aktivity, "Vplyv", "ValueMap", {
    "map": {"n – negatívny": "n", "p – pozitívny": "p"},
})
set_widget(aktivity, "Perc_Plochy", "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_default(aktivity, "Perc_Plochy", "0")

# ── tblHabDruhy ────────────────────────────────────────────────────────────────

print("\n=== tblHabDruhy ===")

set_hidden(druhy, "fid")
set_hidden(druhy, "fkRECORDID")
set_hidden(druhy, "is_characetristic")

for f, a in {
    "KOD":             "Druh (výber zo zoznamu)",
    "NAZOV_LAT":       "Latinský názov (auto)",
    "kod_kbx":         "Kód KB",
    "POKRYVNOST":      "Pokryvnosť",
    "etaz":            "Etáž",
    "pokryvnost_perc": "Pokryvnosť (%)",
}.items():
    set_alias(druhy, f, a)

set_widget(druhy, "KOD", "ValueRelation", {
    "Layer": lkp_druhy.id(), "Key": "Tax_id", "Value": "Taxon_meno",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": True,
    "CompleterMatchFlags": 1,
})
# NAZOV_LAT: read-only, auto-filled from species lookup on every save
set_read_only(druhy, "NAZOV_LAT")
set_default(druhy, "NAZOV_LAT",
    "attribute(get_feature('tblHabDruhyLookup', 'Tax_id', \"KOD\"), 'Taxon_meno')",
    apply_on_update=True)

set_widget(druhy, "POKRYVNOST", "ValueMap", {
    "map": {"1": "1", "2a": "2a", "2b": "2b", "3": "3"},
})
set_default(druhy, "POKRYVNOST", "'1'")
set_widget(druhy, "etaz", "ValueMap", {
    "map": {"E0": "E0", "E1": "E1", "E2": "E2", "E3": "E3"},
})
set_default(druhy, "etaz", "'E1'")
set_widget(druhy, "pokryvnost_perc", "TextEdit", {"IsMultiline": False, "UseHtml": False})
set_default(druhy, "pokryvnost_perc", "0")

# ── QFieldSync / QFieldCloud configuration ─────────────────────────────────────

print("\n=== QFieldSync configuration ===")

for layer, action in {
    hlavna:         "offline",
    biotopy:        "offline",
    opatrenia:      "offline",
    druhy:          "offline",
    aktivity:       "offline",
    lkp_aktivita:     "copy",
    lkp_aktivita_new: "copy",
    lkp_druhy:        "copy",
    lkp_biotop:       "copy",
    lkp_biotop_new:   "copy",
}.items():
    layer.setCustomProperty("QFieldSync/action", action)
    print(f"  {layer.name()}: {action}")

hlavna.setDisplayExpression('"polygon_id" || \' – \' || "podlaorta" || \' – \' || "datum"')
biotopy.setDisplayExpression('"biotop_cislo" || \' – \' || "biotop_pokryv"')
druhy.setDisplayExpression('"NAZOV_LAT" || \' – \' || "etaz" || \' – \' || "POKRYVNOST"')
aktivity.setDisplayExpression('"Aktivita" || \' – \' || "Perc_Plochy"')
print("  display expressions set")

root = project.layerTreeRoot()
for lyr in [lkp_aktivita, lkp_aktivita_new, lkp_druhy, lkp_biotop, lkp_biotop_new]:
    node = root.findLayer(lyr.id())
    if node:
        node.setItemVisibilityChecked(False)
        print(f"  hidden: {lyr.name()}")

aoi = hlavna.extent()
aoi.grow(500)
project.writeEntry("QFieldSync", "areaOfInterest",     aoi.asWktPolygon())
project.writeEntry("QFieldSync", "areaOfInterestCrs",  hlavna.crs().authid())
project.writeEntry("QFieldSync", "offlineCopyOnlyAoi", 1)
print(f"  AOI: {aoi.toString(0)} ({hlavna.crs().authid()})")

# ── label styling for tblHabHlavna ────────────────────────────────────────────

print("\n=== Label styling for tblHabHlavna ===")

pal = QgsPalLayerSettings()
pal.fieldName = '"polygon_id"'
pal.isExpression = True
pal.enabled = True

buf = QgsTextBufferSettings()
buf.setEnabled(True)
buf.setSize(1.0)
buf.setColor(QColor("white"))

fmt = QgsTextFormat()
fmt.setSize(9)
fmt.setBuffer(buf)

dd = pal.dataDefinedProperties()
dd.setProperty(
    QgsPalLayerSettings.Color,
    QgsProperty.fromExpression(
        "if(\"typ_polygon\" IS NOT NULL, '#ff0000', '#000000')"
    ),
)
dd.setProperty(
    QgsPalLayerSettings.Size,
    QgsProperty.fromExpression(
        "if(\"typ_polygon\" IS NOT NULL, 7, 9)"
    ),
)
pal.setDataDefinedProperties(dd)
pal.setFormat(fmt)

hlavna.setLabeling(QgsVectorLayerSimpleLabeling(pal))
hlavna.setLabelsEnabled(True)
print("  label styling set")

# ── save project ───────────────────────────────────────────────────────────────

project.write()
print("\n=== Done — project saved ===")
