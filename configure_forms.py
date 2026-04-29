# configure_forms.py
#
# Run once in the QGIS Python console (Plugins > Python Console > open script).
# Configures all relations, field widgets, aliases, and form layouts
# for the MappingFormPS field-mapping project.

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
)

project  = QgsProject.instance()
gpkg_path = project.homePath() + "/SKUEV0257.gpkg"

# ── helpers ────────────────────────────────────────────────────────────────────

def get_or_load_layer(name):
    layers = project.mapLayersByName(name)
    if layers:
        return layers[0]
    layer = QgsVectorLayer(f"{gpkg_path}|layername={name}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Cannot load layer '{name}' from {gpkg_path}")
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

def add_field_elem(container, layer, field):
    idx = layer.fields().indexOf(field)
    if idx < 0:
        print(f"  WARNING: cannot add field to form: {layer.name()}.{field}")
        return
    container.addChildElement(QgsAttributeEditorField(field, idx, container))

def make_group(parent, title, is_tab=False):
    grp = QgsAttributeEditorContainer(title, parent)
    grp.setIsGroupBox(not is_tab)
    return grp

# ── load layers ────────────────────────────────────────────────────────────────

print("\n=== Loading layers ===")
hlavna         = get_or_load_layer("tblHabHlavna")
biotopy        = get_or_load_layer("tblHabBiotopy")
opatrenia      = get_or_load_layer("tblHabBiotopyOpatrenia")
druhy          = get_or_load_layer("tblHabDruhy")
aktivity       = get_or_load_layer("tblAktivity")
fotky          = get_or_load_layer("tblHabFotky")
lkp_aktivita   = get_or_load_layer("tblAktivityLookup")
lkp_druhy      = get_or_load_layer("tblHabDruhyLookup")
lkp_biotop     = get_or_load_layer("tblHabBiotopyLookup")
lkp_biotop_new = get_or_load_layer("tblHabBiotopyNewLookup")

# ── relations ──────────────────────────────────────────────────────────────────

print("\n=== Defining relations ===")
rel_mgr = project.relationManager()

def make_relation(rel_id, name, parent_layer, parent_field, child_layer, child_field):
    # Remove stale copy if re-running the script
    existing = rel_mgr.relation(rel_id)
    if existing.isValid():
        rel_mgr.removeRelation(rel_id)
    r = QgsRelation()
    r.setId(rel_id)
    r.setName(name)
    r.setReferencedLayer(parent_layer.id())   # 1-side
    r.setReferencingLayer(child_layer.id())   # many-side
    r.addFieldPair(child_field, parent_field)
    r.setStrength(QgsRelation.Association)
    if not r.isValid():
        print(f"  ERROR: invalid relation — {name}")
        return None
    rel_mgr.addRelation(r)
    print(f"  OK: {name}")
    return r

r_biotopy   = make_relation("r_hlavna_biotopy",    "Biotopy",    hlavna,  "RECORDID", biotopy,   "fkRECORDID")
r_druhy     = make_relation("r_hlavna_druhy",      "Druhy",      hlavna,  "RECORDID", druhy,     "fkRECORDID")
r_aktivity  = make_relation("r_hlavna_aktivity",   "Aktivity",   hlavna,  "RECORDID", aktivity,  "fkRECORDID")
r_fotky     = make_relation("r_hlavna_fotky",      "Fotky",      hlavna,  "RECORDID", fotky,     "fkRECORDID")
r_opatrenia = make_relation("r_biotopy_opatrenia", "Opatrenia",  biotopy, "fid",      opatrenia, "fkHabBiotopyID")

# ── tblHabHlavna ───────────────────────────────────────────────────────────────

print("\n=== tblHabHlavna ===")

# Hidden (internal identifiers, not needed in the form)
for f in ["fid", "polygon_id"]:
    set_hidden(hlavna, f)

# Read-only (informational, pre-filled before field work)
for f in ["RECORDID", "KOD_UEV", "podlaorta", "p"]:
    set_read_only(hlavna, f)

# Aliases
for f, a in {
    "RECORDID":          "ID záznamu",
    "KOD_UEV":           "SKUEV",
    "podlaorta":         "Podľa orta",
    "p":                 "Plocha (m²)",
    "datum":             "Dátum mapovania",
    "lokalita":          "Lokalita",
    "hlavny_mapovatel":  "Hlavný mapovateľ",
    "druhy_mapovatel":   "Druhý mapovateľ",
    "poznamka":          "Poznámka",
    "e0":                "E0 – prízemná vrstva (%)",
    "e1":                "E1 – byliny (%)",
    "e2":                "E2 – kry (%)",
    "e3":                "E3 – stromy (%)",
    "E1_invaz":          "E1 – invázne druhy (%)",
    "E2_invaz":          "E2 – invázne druhy (%)",
    "E3_invaz":          "E3 – invázne druhy (%)",
}.items():
    set_alias(hlavna, f, a)

# Widgets
set_widget(hlavna, "datum", "DateTime", {
    "field_format":   "dd.MM.yyyy",
    "display_format": "dd.MM.yyyy",
    "calendar_popup": True,
    "allow_null":     True,
})
for f in ["e0", "e1", "e2", "e3", "E1_invaz", "E2_invaz", "E3_invaz"]:
    set_widget(hlavna, f, "TextEdit", {"IsMultiline": False, "UseHtml": False})
    idx = hlavna.fields().indexOf(f)
    if idx >= 0:
        hlavna.setDefaultValueDefinition(idx, QgsDefaultValue("0"))
set_widget(hlavna, "poznamka", "TextEdit", {"IsMultiline": True, "UseHtml": False})

# Form layout: tabbed
cfg_h = hlavna.editFormConfig()
cfg_h.setLayout(QgsEditFormConfig.TabLayout)
root_h = cfg_h.invisibleRootContainer()
root_h.clear()

# Tab 1 – Basic info + vegetation layers
t1 = make_group(root_h, "Základné info", is_tab=True)
for f in ["RECORDID", "KOD_UEV", "podlaorta", "p",
          "datum", "lokalita", "hlavny_mapovatel", "druhy_mapovatel", "poznamka"]:
    add_field_elem(t1, hlavna, f)
grp_vrstvy = make_group(t1, "Zastúpenie vrstiev (%)")
for f in ["e0", "e1", "e2", "e3"]:
    add_field_elem(grp_vrstvy, hlavna, f)
t1.addChildElement(grp_vrstvy)
grp_invaz = make_group(t1, "Invázne druhy (%)")
for f in ["E1_invaz", "E2_invaz", "E3_invaz"]:
    add_field_elem(grp_invaz, hlavna, f)
t1.addChildElement(grp_invaz)
root_h.addChildElement(t1)

# Tab 2 – Biotopy
t3 = make_group(root_h, "Biotopy", is_tab=True)
if r_biotopy:
    t3.addChildElement(QgsAttributeEditorRelation("Biotopy", r_biotopy, t3))
root_h.addChildElement(t3)

# Tab 4 – Druhy
t4 = make_group(root_h, "Druhy", is_tab=True)
if r_druhy:
    t4.addChildElement(QgsAttributeEditorRelation("Druhy", r_druhy, t4))
root_h.addChildElement(t4)

# Tab 5 – Aktivity
t5 = make_group(root_h, "Aktivity", is_tab=True)
if r_aktivity:
    t5.addChildElement(QgsAttributeEditorRelation("Aktivity", r_aktivity, t5))
root_h.addChildElement(t5)

# Tab 6 – Fotky
t6 = make_group(root_h, "Fotky", is_tab=True)
if r_fotky:
    t6.addChildElement(QgsAttributeEditorRelation("Fotky", r_fotky, t6))
root_h.addChildElement(t6)

hlavna.setEditFormConfig(cfg_h)

# ── tblHabBiotopy ──────────────────────────────────────────────────────────────

print("\n=== tblHabBiotopy ===")

set_hidden(biotopy, "fid")
set_hidden(biotopy, "fkRECORDID")

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
})
set_widget(biotopy, "biotop_cislo_new", "ValueRelation", {
    "Layer": lkp_biotop_new.id(), "Key": "codenew", "Value": "biotopnew_name",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
})
set_widget(biotopy, "biotop_pokryv", "TextEdit", {"IsMultiline": False, "UseHtml": False})
biotopy.setDefaultValueDefinition(biotopy.fields().indexOf("biotop_pokryv"), QgsDefaultValue("0"))
for f in ["kvalita_biotopu_good", "kvalita_biotopu_bad", "kvalita_biotopu_unsiut",
          "manazment_biotopu_vhod", "manazment_biotopu_nevhod",
          "vyhliadky_biotopu_good", "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut"]:
    set_widget(biotopy, f, "CheckBox", {"CheckedState": "1", "UncheckedState": "0"})

# Form layout: grouped (no tabs — this form opens as a child record)
cfg_b = biotopy.editFormConfig()
cfg_b.setLayout(QgsEditFormConfig.TabLayout)
root_b = cfg_b.invisibleRootContainer()
root_b.clear()

grp_bio = make_group(root_b, "Biotop")
for f in ["id", "biotop_cislo", "biotop_pokryv", "biotop_cislo_new"]:
    add_field_elem(grp_bio, biotopy, f)
root_b.addChildElement(grp_bio)

grp_kval = make_group(root_b, "Kvalita biotopu")
for f in ["kvalita_biotopu_good", "kvalita_biotopu_bad", "kvalita_biotopu_unsiut"]:
    add_field_elem(grp_kval, biotopy, f)
root_b.addChildElement(grp_kval)

grp_man = make_group(root_b, "Manažment")
for f in ["manazment_biotopu_vhod", "manazment_biotopu_nevhod"]:
    add_field_elem(grp_man, biotopy, f)
root_b.addChildElement(grp_man)

grp_vyh = make_group(root_b, "Vyhliadky")
for f in ["vyhliadky_biotopu_good", "vyhliadky_biotopu_bad", "vyhliadky_biotopu_unsiut"]:
    add_field_elem(grp_vyh, biotopy, f)
root_b.addChildElement(grp_vyh)

if r_opatrenia:
    grp_opatr = make_group(root_b, "Opatrenia")
    grp_opatr.addChildElement(QgsAttributeEditorRelation("Opatrenia", r_opatrenia, grp_opatr))
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
    "Layer": lkp_aktivita.id(), "Key": "node_code", "Value": "name",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
})
set_widget(opatrenia, "detailny_opis_opatrenia", "TextEdit", {"IsMultiline": True, "UseHtml": False})
set_widget(opatrenia, "percento_z_plochy_biotopu", "TextEdit", {"IsMultiline": False, "UseHtml": False})
opatrenia.setDefaultValueDefinition(opatrenia.fields().indexOf("percento_z_plochy_biotopu"), QgsDefaultValue("0"))

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
    "Layer": lkp_aktivita.id(), "Key": "node_code", "Value": "name",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
})
# ValueMap format: {"map": {"Display label": "stored_value", ...}}
set_widget(aktivity, "Intenzita", "ValueMap", {
    "map": {"A – vysoká": "A", "B – stredná": "B", "C – nízka": "C"},
})
set_widget(aktivity, "Vplyv", "ValueMap", {
    "map": {"p – pozitívny": "p", "n – negatívny": "n"},
})
set_widget(aktivity, "Perc_Plochy", "TextEdit", {"IsMultiline": False, "UseHtml": False})
aktivity.setDefaultValueDefinition(aktivity.fields().indexOf("Perc_Plochy"), QgsDefaultValue("0"))

# ── tblHabDruhy ────────────────────────────────────────────────────────────────

print("\n=== tblHabDruhy ===")

set_hidden(druhy, "fid")
set_hidden(druhy, "fkRECORDID")
set_hidden(druhy, "is_characetristic")   # not needed in form per requirements

for f, a in {
    "KOD":             "Druh (výber zo zoznamu)",
    "NAZOV_LAT":       "Latinský názov (auto)",
    "KOD_KB":          "Kód KB",
    "POKRYVNOST":      "Pokryvnosť – B-B trieda",
    "etaz":            "Etáž",
    "pokryvnost_perc": "Pokryvnosť (%)",
}.items():
    set_alias(druhy, f, a)

# KOD: combobox — stores Tax_id, displays Taxon_meno (5 944 species, completer enabled)
set_widget(druhy, "KOD", "ValueRelation", {
    "Layer": lkp_druhy.id(), "Key": "Tax_id", "Value": "Taxon_meno",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": True,
})

# NAZOV_LAT: read-only; auto-filled from species lookup when record is saved
idx_nazov = druhy.fields().indexOf("NAZOV_LAT")
if idx_nazov >= 0:
    set_read_only(druhy, "NAZOV_LAT")
    druhy.setDefaultValueDefinition(idx_nazov, QgsDefaultValue(
        "attribute(get_feature('tblHabDruhyLookup', 'Tax_id', \"KOD\"), 'Taxon_meno')",
        applyOnUpdate=True,   # re-evaluate on every save, not only on new feature
    ))

set_widget(druhy, "POKRYVNOST", "ValueMap", {
    "map": {"1": "1", "2a": "2a", "2b": "2b", "3": "3"},
})
set_widget(druhy, "etaz", "ValueMap", {
    "map": {"E0": "E0", "E1": "E1", "E2": "E2", "E3": "E3"},
})
set_widget(druhy, "pokryvnost_perc", "TextEdit", {"IsMultiline": False, "UseHtml": False})
druhy.setDefaultValueDefinition(druhy.fields().indexOf("pokryvnost_perc"), QgsDefaultValue("0"))

# ── tblHabFotky ────────────────────────────────────────────────────────────────

print("\n=== tblHabFotky ===")

set_hidden(fotky, "fid")
set_hidden(fotky, "fkRECORDID")
set_alias(fotky, "fotoFileName", "Názov súboru")

# ExternalResource: QField captures photo with camera, stores filename only.
# Attachments are NOT synced via QFieldCloud — photos are transferred separately.
set_widget(fotky, "fotoFileName", "ExternalResource", {
    "UseLink":          False,
    "FullUrl":          False,
    "RelativeStorage":  1,       # 1 = relative to project folder
    "StorageMode":      1,       # 1 = capture media (camera)
    "DocumentViewer":   2,       # 2 = image viewer
    "FileWidgetFilter": "Images (*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG)",
    "PropertyCollection": {},
})

# ── save project ───────────────────────────────────────────────────────────────

project.write()
print("\n=== Done — project saved ===")
