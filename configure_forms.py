# configure_forms.py
#
# Run once in the QGIS Python console (Plugins > Python Console > open script).
# Regenerates all widget, alias, default-value, and form-layout configuration
# to exactly match the current MapovaciFormularPS.qgs project state.

import json

from qgis.core import (
    Qgis,
    QgsProject,
    QgsRelation,
    QgsVectorLayer,
    QgsEditorWidgetSetup,
    QgsEditFormConfig,
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsAttributeEditorRelation,
    QgsAttributeEditorQmlElement,
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
prevod_gpkg_path = project.homePath() + "/PrevodKatalogy.gpkg"

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

# The init-code-source value moved twice: QgsEditFormConfig.CodeSourceDialog ->
# QgsEditFormConfig.PythonInitCodeSource.CodeSourceDialog ->
# Qgis.AttributeFormPythonInitCodeSource.Dialog
try:
    _CODE_SOURCE_DIALOG = Qgis.AttributeFormPythonInitCodeSource.Dialog
except AttributeError:
    try:
        _CODE_SOURCE_DIALOG = QgsEditFormConfig.PythonInitCodeSource.CodeSourceDialog
    except AttributeError:
        _CODE_SOURCE_DIALOG = QgsEditFormConfig.CodeSourceDialog

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
lkp_prevod     = get_or_load_layer("PrevodKatalogy", prevod_gpkg_path)

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

r_biotopy   = make_relation("r_hlavna_biotopy",    "Biotopy",   hlavna,  "RECORDID", biotopy,   "fkRECORDID")
r_druhy     = make_relation("r_hlavna_druhy",      "Druhy",     hlavna,  "RECORDID", druhy,     "fkRECORDID")
r_aktivity  = make_relation("r_hlavna_aktivity",   "Aktivity",  hlavna,  "RECORDID", aktivity,  "fkRECORDID")
r_opatrenia = make_relation("r_biotopy_opatrenia", "Opatrenia", biotopy, "id", opatrenia, "fkHabBiotopyID")

# ── tblHabHlavna ───────────────────────────────────────────────────────────────

print("\n=== tblHabHlavna ===")

set_hidden(hlavna, "fid")
# RECORDID is shown (read-only) instead of polygon_id: it is the unique key of
# the record, and [polygon_id_form] is meant to reference it. polygon_id is not
# unique — QField-added polygons inherit the polygon_id of the polygon they were
# split from — so it cannot identify a linked form unambiguously.
# Child-table relations need a parent key before the new polygon is saved.
# The provider fid is only assigned after insert, so QField cannot use it while
# the attribute form is still open.
# The value must stay a short, human-readable integer: the mapper reads it off
# the map label and types it into [polygon_id_form], and step 06 pads it into
# gis_id_origin as SKUEV####_N_XX_YYY (3 digits). It therefore continues the
# existing sequence per project (max + 1, first feature = 1) instead of the
# timestamp-based value used before, which produced 18-digit numbers.
set_default(hlavna, "RECORDID", "coalesce(maximum(\"RECORDID\") + 1, 1)")

for f in ["KOD_UEV", "RECORDID", "polygon_id", "p", "podlaorta"]:
    set_read_only(hlavna, f)

for f, a in {
    "KOD_UEV":          "SKUEV",
    "podlaorta":        "Podľa orta",
    "poznamka":         "Poznámka",
    "p":                "Plocha (m²)",
    "RECORDID":         "ID polygónu",
    "polygon_id":       "ID polygónu (staré)",
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
set_widget(hlavna, "RECORDID",         "TextEdit", {"IsMultiline": False, "UseHtml": False})
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
for f in ["RECORDID", "podlaorta", "p", "datum", "lokalita", "hlavny_mapovatel"]:
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

# ── "Kopíruj druhy z prepojeného formulára" ────────────────────────────────────
#
# The Druhy tab gets a button that copies every tblHabDruhy record of the form
# referenced by [polygon_id_form] onto this polygon ([RECORDID]). It is enabled
# only while [polygon_id_form] is filled.
#
# Two implementations are needed because the form runs in two apps:
#   * QGIS desktop — a real QPushButton injected by the form init code below.
#     QField has no Python, so it ignores the init code entirely.
#   * QField — a QML widget at the top of the tab. QField supports neither QGIS
#     layer actions nor Python, so a QML form widget is the only documented way
#     to put an interactive element inside a form. LayerUtils/FeatureUtils exist
#     only in QField, so the QML pulls them in through Qt.createQmlObject();
#     in QGIS desktop that import fails, the widget collapses to zero height and
#     the QPushButton is used instead.
#
# Both skip records the target polygon already has (same values in every column
# except fid/fkRECORDID), so pressing the button twice adds nothing the second
# time — and 03_validacia.py does not have to clean up after it.

_COPY_BUTTON_LABEL = "Kopíruj druhy z prepojeného formulára"

_DRUHY_COPY_QML = r"""
import QtQuick

Item {
    id: root

    property var fieldNames: __FIELD_NAMES__
    property var api: null
    property string apiSource: "?"
    property string apiError: ""
    property string valueSource: "-"
    property string statusText: ""
    property string copyMode: "-"
    property var sourceId: null
    property var targetId: null

    // QField widgetu vlastnú veľkosť nedáva – bez pevnej výšky sa nezobrazí nič.
    width: parent ? parent.width : 400
    implicitWidth: 400
    height: content.implicitHeight
    implicitHeight: content.implicitHeight

    Component.onCompleted: {
        resolveApi();
        refresh();
    }

    // Formulár o zmene hodnôt neinformuje, takže sa čítajú opakovane.
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: root.refresh()
    }

    Column {
        id: content
        width: root.width
        spacing: 6

        // Zámerne bez QtQuick.Controls – čistý QtQuick funguje v QGIS aj QField.
        Rectangle {
            id: copyButton
            width: root.width
            height: 48
            radius: 4
            property bool active: root.isFilled(root.sourceId)
            color: !active ? "#b0b0b0" : (touch.pressed ? "#1f6f3f" : "#2e8b57")

            Text {
                anchors.centerIn: parent
                width: parent.width - 16
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: "white"
                font.pixelSize: 14
                text: "__BUTTON_LABEL__"
            }

            MouseArea {
                id: touch
                anchors.fill: parent
                enabled: copyButton.active
                onClicked: root.copyDruhy()
            }
        }

        Text {
            id: statusLabel
            width: root.width
            wrapMode: Text.WordWrap
            font.pixelSize: 12
            color: "#777777"
            visible: text !== ""
            text: root.statusText !== ""
                  ? root.statusText
                  : (copyButton.active
                     ? ""
                     : "Tlačidlo sa zapne, keď je vyplnené [ID rovnakého formulára].")
        }
    }

    function refresh() {
        valueSource = "-";
        sourceId = fieldValue("polygon_id_form");
        targetId = fieldValue("RECORDID");
    }

    // Hodnotu poľa dáva model formulára (QField 3 aj 4 majú na ňom
    // Q_INVOKABLE attribute()), inak expression kontext (len QField 4)
    // alebo priamo prvok. Poradie je od najspoľahlivejšieho zdroja.
    function fieldValue(name) {
        var value = null;
        if (typeof form !== 'undefined') {
            try {
                value = form.model.attribute(name);
                if (isFilled(value)) {
                    valueSource = "form.model";
                    return value;
                }
            } catch (err) {
            }
        }
        if (typeof expression !== 'undefined') {
            try {
                value = expression.evaluate('"' + name + '"');
                if (isFilled(value)) {
                    valueSource = "expression";
                    return value;
                }
            } catch (err) {
            }
            try {
                value = expression.evaluate("attribute('" + name + "')");
                if (isFilled(value)) {
                    valueSource = "attribute()";
                    return value;
                }
            } catch (err) {
            }
        }
        var features = [];
        if (typeof feature !== 'undefined')
            features.push(feature);
        if (typeof currentFeature !== 'undefined')
            features.push(currentFeature);
        if (typeof form !== 'undefined') {
            try {
                features.push(form.model.featureModel.feature);
            } catch (err) {
            }
        }
        for (var i = 0; i < features.length; ++i) {
            try {
                value = features[i].attribute(name);
                if (isFilled(value)) {
                    valueSource = "prvok";
                    return value;
                }
            } catch (err) {
            }
        }
        return null;
    }

    // LayerUtils/FeatureUtils sú len v QField a podľa verzie sú buď priamo v
    // kontexte widgetu, alebo dostupné až cez import org.qfield. Skúsia sa obe
    // cesty; čo sa podarilo, vypíše diagnostics() pri neúspešnom kopírovaní.
    function resolveApi() {
        if (typeof LayerUtils !== 'undefined' && typeof FeatureUtils !== 'undefined') {
            api = {
                iterate: function (layer, filter) { return LayerUtils.createFeatureIteratorFromExpression(layer, filter); },
                add: function (layer, feature) { return LayerUtils.addFeature(layer, feature); },
                create: function (layer) { return FeatureUtils.createFeature(layer); }
            };
            apiSource = "kontext";
            return;
        }
        var imports = ["import org.qfield", "import org.qfield 1.0"];
        for (var i = 0; i < imports.length; ++i) {
            try {
                api = Qt.createQmlObject(
                    imports[i] + '; import QtQuick; QtObject {' +
                    '  function iterate(layer, filter) { return LayerUtils.createFeatureIteratorFromExpression(layer, filter); }' +
                    '  function add(layer, feature) { return LayerUtils.addFeature(layer, feature); }' +
                    '  function create(layer) { return FeatureUtils.createFeature(layer); }' +
                    '}', root);
            } catch (err) {
                api = null;
                apiError = String(err).substring(0, 160);
            }
            if (api !== null && api !== undefined) {
                apiSource = imports[i];
                apiError = "";
                return;
            }
        }
        apiSource = "chyba";
    }

    function projectRef() {
        if (typeof qgisProject !== 'undefined')
            return qgisProject;
        if (typeof project !== 'undefined')
            return project;
        return null;
    }

    function diagnostics() {
        return "API: " + apiSource
            + ", projekt: " + (projectRef() !== null ? "ano" : "nie")
            + ", formular: " + (typeof form !== 'undefined' ? "ano" : "nie")
            + ", hodnoty: " + valueSource
            + ", ID formulara: " + (isFilled(sourceId) ? sourceId : "-")
            + ", ID polygonu: " + (isFilled(targetId) ? targetId : "-")
            + (apiError !== "" ? ", " + apiError : "");
    }

    function isFilled(value) {
        return value !== undefined && value !== null && String(value).trim() !== "";
    }

    function isNumber(value) {
        return isFilled(value) && !isNaN(Number(value));
    }

    // Názvy stĺpcov sa berú z prvku – balený projekt môže mať inú štruktúru
    // než tá, s ktorou bol formulár nastavený.
    function namesOf(feature, fallback) {
        try {
            var names = feature.fields.names;
            if (names && names.length > 0)
                return names;
        } catch (err) {
        }
        return fallback;
    }

    // Kľúčové stĺpce, ktoré si kópia nesmie priniesť zo zdroja. Balenie pre
    // QFieldCloud dá tabuľke vlastné [fid] a pôvodné premenuje na [fid_1],
    // ktoré má tiež jedinečný index – prevzatá hodnota by insert zhodila na
    // "UNIQUE constraint failed: ....fid_1".
    function isKeyField(name) {
        return /^fid(_[0-9]+)?$/i.test(name);
    }

    // zhoda vo všetkých stĺpcoch okrem kľúčov a fkRECORDID = ten istý druh.
    // Hodnoty sa čítajú cez Q_INVOKABLE attribute() – zoznam attributes je
    // v QML prázdny, takže by každý záznam vyšiel rovnako.
    function signature(names, feature) {
        var parts = [];
        for (var i = 0; i < names.length; ++i) {
            if (isKeyField(names[i]) || names[i] === "fkRECORDID")
                continue;
            var value = null;
            try {
                value = feature.attribute(names[i]);
            } catch (err) {
            }
            parts.push(value === undefined || value === null ? "" : String(value));
        }
        return parts.join(String.fromCharCode(1));
    }

    // Nový záznam sa robí tak, ako ho robí samotný QField – prvok vrstvy s
    // predvolenými hodnotami, do ktorého sa prepíšu hodnoty zdroja. Kľúčové
    // stĺpce ostanú prázdne, aby ich pridelil GeoPackage. Ak by to neprešlo,
    // použije sa zdrojový prvok s vymazanými kľúčmi.
    function setField(feature, names, name, value) {
        try {
            if (feature.setAttribute(name, value) !== false)
                return true;
        } catch (err) {
        }
        var index = names.indexOf(name);
        if (index < 0)
            return false;
        try {
            return feature.setAttribute(index, value) !== false;
        } catch (err) {
            return false;
        }
    }

    function textOf(value) {
        return value === undefined || value === null ? "" : String(value);
    }

    function sameValues(copy, row, names, targetValue) {
        if (String(copy.attribute("fkRECORDID")) !== String(targetValue))
            return false;
        for (var i = 0; i < names.length; ++i) {
            if (isKeyField(names[i]) || names[i] === "fkRECORDID")
                continue;
            if (textOf(copy.attribute(names[i])) !== textOf(row.attribute(names[i])))
                return false;
        }
        return true;
    }

    function keysCleared(feature, names) {
        for (var i = 0; i < names.length; ++i) {
            if (isKeyField(names[i]) && isFilled(feature.attribute(names[i])))
                return false;
        }
        return true;
    }

    function buildCopy(row, names, layer, targetValue) {
        try {
            var fresh = api.create(layer);
            for (var i = 0; i < names.length; ++i) {
                if (isKeyField(names[i]))
                    continue;
                setField(fresh, names, names[i],
                         names[i] === "fkRECORDID" ? targetValue : row.attribute(names[i]));
            }
            if (sameValues(fresh, row, names, targetValue) && keysCleared(fresh, names)) {
                copyMode = "novy prvok";
                return fresh;
            }
        } catch (err) {
        }
        try {
            for (var j = 0; j < names.length; ++j) {
                if (isKeyField(names[j]))
                    setField(row, names, names[j], null);
            }
            setField(row, names, "fkRECORDID", targetValue);
            if (!keysCleared(row, names))
                return null;
            if (String(row.attribute("fkRECORDID")) !== String(targetValue))
                return null;
            copyMode = "zdrojovy prvok";
            return row;
        } catch (err) {
        }
        return null;
    }

    function filterFor(value) {
        return isNumber(value)
            ? '"fkRECORDID" = ' + Number(value)
            : '"fkRECORDID" = \'' + String(value).replace(/'/g, "''") + '\'';
    }

    function copyDruhy() {
        statusText = "";
        refresh();
        var source = sourceId;
        var target = targetId;
        if (!isFilled(source)) {
            statusText = "Pole [ID rovnakého formulára] nie je vyplnené.";
            return;
        }
        if (!isFilled(target)) {
            statusText = "Polygón ešte nemá [ID polygónu] – najskôr ulož formulár.";
            return;
        }
        if (String(source) === String(target)) {
            statusText = "[ID rovnakého formulára] ukazuje na tento istý polygón.";
            return;
        }
        if (api === null || api === undefined)
            resolveApi();
        if (api === null || api === undefined) {
            statusText = "Kopírovanie tu nie je dostupné (" + diagnostics() + ").";
            return;
        }
        var qgis = projectRef();
        if (qgis === null) {
            statusText = "Projekt nie je dostupný (" + diagnostics() + ").";
            return;
        }
        var layers = qgis.mapLayersByName("tblHabDruhy");
        if (!layers || layers.length === 0) {
            statusText = "Vrstva tblHabDruhy sa v projekte nenašla.";
            return;
        }
        var layer = layers[0];
        var targetValue = isNumber(target) ? Number(target) : target;
        try {
            var existing = {};
            var iterator = api.iterate(layer, filterFor(target));
            while (iterator.hasNext()) {
                var current = iterator.next();
                existing[signature(namesOf(current, fieldNames), current)] = true;
            }

            // Nový záznam je kópia zdrojového prvku aj s jeho štruktúrou polí –
            // LayerUtils.addFeature vkladá prvok priamo do vrstvy, takže stĺpce
            // musia sedieť s vrstvou.
            var copies = [];
            var skipped = 0;
            var failed = 0;
            iterator = api.iterate(layer, filterFor(source));
            while (iterator.hasNext()) {
                var row = iterator.next();
                var names = namesOf(row, fieldNames);
                var key = signature(names, row);
                if (existing[key]) {
                    skipped += 1;
                    continue;
                }
                existing[key] = true;
                var copy = buildCopy(row, names, layer, targetValue);
                if (copy === null) {
                    failed += 1;
                    continue;
                }
                copies.push(copy);
            }

            if (copies.length === 0) {
                if (failed > 0)
                    statusText = "Záznamy sa nepodarilo pripraviť (" + failed + " prvkov).";
                else if (skipped > 0)
                    statusText = "Všetkých " + skipped + " druhov formulára " + source + " tento polygón už má.";
                else
                    statusText = "Formulár " + source + " nemá žiadne druhy na skopírovanie.";
                return;
            }

            var started = layer.startEditing();
            var added = 0;
            for (var j = 0; j < copies.length; ++j) {
                if (api.add(layer, copies[j]))
                    added += 1;
            }
            if (added === 0) {
                layer.rollBack();
                statusText = "Do vrstvy sa nepodarilo pridať nič (pripravených "
                    + copies.length + ", start " + started + ", sposob " + copyMode + ").";
                return;
            }
            // commitChanges() zapisuje do GeoPackage a až tým vzniká zmena,
            // ktorú QField pošle do cloudu (LayerObserver počúva na
            // committedFeaturesAdded). Bez kontroly by sa strata prejavila
            // až po zatvorení projektu.
            if (layer.commitChanges()) {
                statusText = "Skopírovaných " + added + " druhov z formulára " + source
                    + (skipped > 0 ? " (" + skipped + " už existovalo)" : "")
                    + (failed > 0 ? " (" + failed + " sa nepodarilo pripraviť)" : "") + "."
                    + " Ak sa zoznam neobnoví, zavri a otvor formulár.";
                return;
            }

            // Hromadný zápis zlyhal – skús záznamy po jednom, tak ako ich
            // zapisuje samotný QField pri pridávaní druhu tlačidlom +.
            layer.rollBack();
            var written = 0;
            for (var k = 0; k < copies.length; ++k) {
                layer.startEditing();
                if (api.add(layer, copies[k]) && layer.commitChanges()) {
                    written += 1;
                } else {
                    layer.rollBack();
                    break;
                }
            }
            if (written > 0) {
                statusText = "Zapísaných " + written + " z " + copies.length
                    + " druhov (hromadný zápis zlyhal, išlo to po jednom)."
                    + " Ak sa zoznam neobnoví, zavri a otvor formulár.";
                return;
            }
            statusText = "Zápis do vrstvy zlyhal (pripravených " + copies.length
                + ", pridaných " + added + ", start " + started
                + ", sposob " + copyMode + "). Dôvod je v Správach QFieldu.";
        } catch (err) {
            statusText = "Chyba: " + err;
        }
    }
}
"""

# ── "Možné biotopy" – biotopy, na ktoré ukazujú zapísané druhy ────────────────
#
# Panel at the top of the Druhy tab (QGIS desktop only for now) that lists the
# Katalóg biotopov 2023 biotopes the polygon's species point to: how many of
# each biotope's diagnostic, dominant and constant species are recorded, which
# ones, and whether the biotope is already in tblHabBiotopy. The catalogue comes
# from KatalogDruhy.gpkg next to the project, built by 10_katalog_druhy.py from
# the Botany Explorer database (catalogue species already resolved to Tax_id).
# The panel reads that file directly, so it is not a project layer and nothing
# changes for QFieldCloud.
#
# The scoring below is plain Python without QGIS imports; test_biotop_panel.py
# runs it on its own. It is pasted into the form init code.

_BIOTOP_SCORE_CODE = r'''
BOT_URL = "http://localhost/bot/?biotope=katalog:"
# váha druhu podľa roly v katalógu (diagnostický > dominantný > konštantný)
WEIGHTS = {"diag": 3, "dom": 2, "konst": 1}
# (rola, skratka v počtoch, nadpis zoznamu druhov – None = druhy sa nevypisujú)
ROLE_LABELS = (("diag", "diag", "diagnostické"), ("dom", "dom", "dominantné"),
               ("konst", "konšt", None))
MAX_ROWS = 8


def _html(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def score_biotopes(druhy, katalog, biotopy, zapisane=()):
    """Biotopy, na ktoré ukazujú druhy polygónu, od najlepšej zhody.

    druhy    – [(Tax_id, etáž, názov)] záznamy tblHabDruhy polygónu
    katalog  – {Tax_id: [(kód, druh katalógu, diag, konst, dom, etáže)]},
               etáže = množina ('E3', ...), prázdna = ľubovoľná etáž
    biotopy  – {kód: (názov, nadradený kód, n_diag, n_konst, n_dom)}
    zapisane – kódy biotopov zapísané v tblHabBiotopy polygónu

    Vráti (výsledky, zapísané bez zhody). Výsledok je dict s kľúčmi kod, nazov,
    diag/konst/dom ({druh katalógu: [názvy z terénu]}), n_diag/n_konst/n_dom,
    skore a zapisany. Druh katalógu sa ráta raz, aj keď ho v teréne pokrýva
    viac záznamov (druh aj jeho poddruh). Druh s etážou v katalógu sa ráta len
    vtedy, keď je zapísaný v tej istej etáži (alebo etáž chýba).
    """
    zapisane = {str(z).strip().upper() for z in zapisane if z and str(z).strip()}
    hits = {}
    for tax_id, etaz, nazov in druhy:
        if tax_id is None:
            continue
        etaz = (etaz or "").strip().upper()
        for kod, druh, diag, konst, dom, etaze in katalog.get(tax_id, ()):
            if etaz and etaze and etaz not in etaze:
                continue
            h = hits.setdefault(kod, {"diag": {}, "konst": {}, "dom": {}})
            for role, flag in (("diag", diag), ("konst", konst), ("dom", dom)):
                if flag:
                    names = h[role].setdefault(druh, [])
                    if nazov and nazov not in names:
                        names.append(nazov)

    def recorded(kod):
        info = biotopy.get(kod)
        parent = info[1] if info else None
        return kod.upper() in zapisane or bool(parent and parent.upper() in zapisane)

    results = []
    for kod, h in hits.items():
        nazov, _parent, n_diag, n_konst, n_dom = biotopy.get(kod, ("", None, 0, 0, 0))
        results.append({
            "kod": kod, "nazov": nazov, "diag": h["diag"], "konst": h["konst"],
            "dom": h["dom"], "n_diag": n_diag, "n_konst": n_konst, "n_dom": n_dom,
            "skore": sum(WEIGHTS[r] * len(h[r]) for r in WEIGHTS),
            "zapisany": recorded(kod),
        })
    results.sort(key=lambda r: (-r["skore"], -len(r["diag"]),
                                -(len(r["diag"]) / r["n_diag"] if r["n_diag"] else 0),
                                r["kod"]))

    covered = set()
    for r in results:
        if r["diag"] or r["dom"]:
            covered.add(r["kod"].upper())
            parent = (biotopy.get(r["kod"]) or ("", None))[1]
            if parent:
                covered.add(parent.upper())
    missing = sorted(z for z in zapisane if z not in covered)
    return results, missing


def render_html(results, missing, biotopy, druhy_count, max_rows=MAX_ROWS):
    """HTML pre panel: najlepšie biotopy, zapísané vždy, upozornenia na konci."""
    if not druhy_count:
        return "<i>Polygón zatiaľ nemá žiadne druhy.</i>"
    shown = [r for r in results[:max_rows] if r["skore"] > 0]
    shown += [r for r in results[max_rows:] if r["zapisany"]]
    parts = []
    if not shown:
        parts.append("<i>Žiadny zo zapísaných druhov nie je v Katalógu biotopov 2023 "
                     "diagnostický, dominantný ani konštantný.</i>")
    for r in shown:
        mark = (' <span style="color:#2e7d32"><b>&#10003; zapísaný</b></span>'
                if r["zapisany"] else "")
        counts = " &middot; ".join("%s %d/%d" % (short, len(r[key]), r["n_" + key])
                                   for key, short, _title in ROLE_LABELS)
        lines = ['<b><a href="%s%s">%s</a></b> %s%s<br><small>%s</small>'
                 % (BOT_URL, _html(r["kod"]), _html(r["kod"]), _html(r["nazov"]),
                    mark, counts)]
        for key, _short, title in ROLE_LABELS:
            if title and r[key]:
                lines.append('<br><small><span style="color:#555">%s:</span> %s</small>'
                             % (title, _html(", ".join(sorted(r[key])))))
        parts.append("".join(lines))
    shown_codes = {r["kod"] for r in shown}
    rest = sum(1 for r in results if r["skore"] > 0 and r["kod"] not in shown_codes)
    if rest > 0:
        parts.append("<small><i>… a ďalších %d biotopov so slabšou zhodou</i></small>" % rest)
    for kod in missing:
        info = biotopy.get(kod) or next(
            (v for k, v in biotopy.items() if k.upper() == kod), None)
        if info is None:
            text = "%s nie je kód Katalógu biotopov 2023." % _html(kod)
        elif not (info[2] or info[4]):
            continue  # katalóg pri ňom diagnostické ani dominantné druhy neuvádza
        else:
            text = ("zapísaný biotop <b>%s</b> nemá medzi druhmi žiadny diagnostický "
                    "ani dominantný druh." % _html(kod))
        parts.append('<span style="color:#c62828">&#9888; %s</span>' % text)
    return "<br><br>".join(parts)
'''

_FORM_INIT_CODE = r'''# -*- coding: utf-8 -*-
# Generované skriptom configure_forms.py – needituj priamo v projekte.
# Na kartu "Druhy" pridá tlačidlo, ktoré skopíruje druhy z formulára, ktorého
# [RECORDID] je zapísané v poli [polygon_id_form], do práve otvoreného polygónu.
# Tlačidlo je aktívne len vtedy, keď je [polygon_id_form] vyplnené.
# Beží len v QGIS na počítači; v QField to isté robí QML widget nad zoznamom.
# Nad zoznam druhov pridá aj panel "Možné biotopy" (Katalóg biotopov 2023).
import os
import re
import sqlite3
from pathlib import Path

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsFeature, QgsFeatureRequest, QgsMessageLog, QgsProject

try:
    from qgis.gui import QgsCollapsibleGroupBox as _PanelBox
except ImportError:
    _PanelBox = QGroupBox

DRUHY_LAYER = "tblHabDruhy"
DRUHY_TAB = "Druhy"
BUTTON_NAME = "btn_kopiruj_druhy"
BUTTON_LABEL = "__BUTTON_LABEL__"
# Kľúčové stĺpce sa nekopírujú: okrem [fid] to je aj [fid_1], ktoré vzniká
# pri balení projektu pre QFieldCloud a má tiež jedinečný index.
KEY_FIELD = re.compile(r"^fid(_\d+)?$", re.IGNORECASE)


def _is_copied(name):
    return not KEY_FIELD.match(name) and name.lower() != "fkrecordid"


def _druhy_layer():
    layers = QgsProject.instance().mapLayersByName(DRUHY_LAYER)
    return layers[0] if layers else None


def _value(dialog, feature, field):
    """Hodnota poľa – prednostne živá z formulára, inak z uloženého prvku."""
    widget = dialog.findChild(QLineEdit, field)
    if widget is not None:
        text = widget.text().strip()
        return text if text else None
    try:
        value = feature[field]
    except KeyError:
        return None
    text = "" if value is None else str(value).strip()
    return text if text and text.upper() != "NULL" else None


def _find_tab(dialog, title):
    for tabs in dialog.findChildren(QTabWidget):
        for i in range(tabs.count()):
            if tabs.tabText(i).strip() == title:
                return tabs.widget(i)
    return None


def _place(tab, button):
    layout = tab.layout()
    if layout is None:
        layout = QVBoxLayout(tab)
        tab.setLayout(layout)
    if isinstance(layout, QBoxLayout):
        layout.insertWidget(0, button)
    elif isinstance(layout, QGridLayout):
        layout.addWidget(button, layout.rowCount(), 0, 1, max(layout.columnCount(), 1))
    else:
        layout.addWidget(button)


def _refresh_relations(dialog, feature):
    """Po pridaní záznamov prekreslí zoznam v editore vzťahu."""
    for widget in dialog.findChildren(QWidget):
        if "RelationEditor" not in widget.metaObject().className():
            continue
        try:
            widget.setFeature(feature, True)
        except Exception:
            try:
                widget.updateUi()
            except Exception:
                pass


def _copy(dialog, feature):
    layer = _druhy_layer()
    if layer is None:
        QMessageBox.warning(dialog, BUTTON_LABEL,
                            "Vrstva %s nie je v projekte." % DRUHY_LAYER)
        return
    source = _value(dialog, feature, "polygon_id_form")
    target = _value(dialog, feature, "RECORDID")
    if source is None:
        QMessageBox.information(dialog, BUTTON_LABEL,
                                "Pole [ID rovnakého formulára] nie je vyplnené.")
        return
    if target is None:
        QMessageBox.warning(dialog, BUTTON_LABEL,
                            "Polygón ešte nemá [ID polygónu] – najskôr ulož zmeny.")
        return
    if source == target:
        QMessageBox.warning(dialog, BUTTON_LABEL,
                            "[ID rovnakého formulára] ukazuje na tento istý polygón.")
        return

    # zhoda vo všetkých stĺpcoch okrem fid a fkRECORDID = ten istý druh
    copy_fields = [f.name() for f in layer.fields() if _is_copied(f.name())]
    source_rows = []
    target_signatures = set()
    for row in layer.getFeatures():
        value = row["fkRECORDID"]
        value = "" if value is None else str(value).strip()
        if value == source:
            source_rows.append(row)
        elif value == target:
            target_signatures.add(tuple(str(row[f]) for f in copy_fields))

    if not source_rows:
        QMessageBox.information(dialog, BUTTON_LABEL,
                                "Formulár %s nemá žiadne druhy na skopírovanie."
                                % source)
        return

    fk_value = int(target) if target.lstrip("-").isdigit() else target
    new_rows = []
    skipped = 0
    for row in source_rows:
        signature = tuple(str(row[f]) for f in copy_fields)
        if signature in target_signatures:
            skipped += 1
            continue
        target_signatures.add(signature)
        new_row = QgsFeature(layer.fields())
        for name in copy_fields:
            new_row[name] = row[name]
        new_row["fkRECORDID"] = fk_value
        new_rows.append(new_row)

    if not new_rows:
        QMessageBox.information(dialog, BUTTON_LABEL,
                                "Všetkých %d druhov formulára %s tento polygón už má."
                                % (skipped, source))
        return

    started = layer.startEditing() if not layer.isEditable() else False
    if not layer.addFeatures(new_rows):
        if started:
            layer.rollBack()
        QMessageBox.critical(dialog, BUTTON_LABEL, "Záznamy sa nepodarilo pridať.")
        return
    if started:
        layer.commitChanges()
    _refresh_relations(dialog, feature)
    QMessageBox.information(
        dialog, BUTTON_LABEL,
        "Skopírovaných %d druhov z formulára %s%s."
        % (len(new_rows), source,
           " (%d už existovalo)" % skipped if skipped else ""))


def _hide_qml_widget(tab):
    """QML widget je verzia tlačidla pre QField – v QGIS ho netreba."""
    for widget in tab.findChildren(QWidget):
        if widget.metaObject().className() == "QQuickWidget":
            widget.hide()


# ── panel "Možné biotopy" ─────────────────────────────────────────────────────
__BIOTOP_SCORE_CODE__

KATALOG_FILE = "KatalogDruhy.gpkg"
BIOTOPY_LAYER = "tblHabBiotopy"
PANEL_NAME = "grp_mozne_biotopy"
PANEL_TITLE = "Možné biotopy podľa druhov (Katalóg biotopov 2023)"
# init kód sa spúšťa pri každom otvorení formulára – katalóg sa načíta raz
_mozne_biotopy_cache = globals().setdefault("_mozne_biotopy_cache", {})


def _plain(value):
    """NULL z QGIS (QVariant) -> None."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    return value


def _load_katalog():
    """(katalog, biotopy) z KatalogDruhy.gpkg v priečinku projektu."""
    path = os.path.join(QgsProject.instance().homePath(), KATALOG_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError("Chýba %s v priečinku projektu – spusti "
                                "10_katalog_druhy.py." % KATALOG_FILE)
    stamp = (path, os.path.getmtime(path))
    if _mozne_biotopy_cache.get("stamp") != stamp:
        con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
        try:
            katalog = {}
            for tax_id, kod, druh, diag, konst, dom, etaz in con.execute(
                    "SELECT Tax_id, kod_2023, druh_katalog, diag, konst, dom, etaz "
                    "FROM KatalogDruhy"):
                etaze = frozenset(e.strip().upper() for e in (etaz or "").split(",") if e.strip())
                katalog.setdefault(tax_id, []).append((kod, druh, diag, konst, dom, etaze))
            biotopy = {kod: (nazov, nadradeny, n_diag, n_konst, n_dom)
                       for kod, nazov, nadradeny, n_diag, n_konst, n_dom in con.execute(
                           "SELECT kod_2023, nazov_2023, nadradeny, n_diag, n_konst, n_dom "
                           "FROM KatalogBiotopy")}
        finally:
            con.close()
        _mozne_biotopy_cache.update(stamp=stamp, data=(katalog, biotopy))
    return _mozne_biotopy_cache["data"]


def _children(layer_name, recordid, fields):
    """Záznamy podriadenej tabuľky polygónu (aj neuložené) ako n-tice hodnôt."""
    layers = QgsProject.instance().mapLayersByName(layer_name)
    if not layers:
        return []
    value = recordid if recordid.lstrip("-").isdigit() else "'%s'" % recordid.replace("'", "''")
    request = QgsFeatureRequest().setFilterExpression('"fkRECORDID" = %s' % value)
    return [tuple(_plain(f[name]) for name in fields) for f in layers[0].getFeatures(request)]


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _place_top(tab, widget):
    """Vloží widget úplne hore na kartu."""
    layout = tab.layout()
    if layout is None:
        layout = QVBoxLayout(tab)
        tab.setLayout(layout)
    if isinstance(layout, QBoxLayout):
        layout.insertWidget(0, widget)
    elif isinstance(layout, QGridLayout):
        # mriežka nemá vkladanie riadku – všetko sa posunie o riadok nižšie
        items = []
        while layout.count():
            i = layout.count() - 1
            items.append((layout.getItemPosition(i), layout.takeAt(i)))
        rows = max([r + rs for (r, _c, rs, _cs), _it in items] or [0])
        for r in range(rows, 0, -1):
            layout.setRowStretch(r, layout.rowStretch(r - 1))
        layout.setRowStretch(0, 0)
        for (r, c, rs, cs), item in reversed(items):
            layout.addItem(item, r + 1, c, rs, cs)
        layout.addWidget(widget, 0, 0, 1, max(layout.columnCount(), 1))
    else:
        layout.addWidget(widget)


def _disconnect(signals, slot):
    for signal in signals:
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass


class _MozneBiotopy(_PanelBox):
    """Biotopy, na ktoré ukazujú druhy polygónu; obnoví sa pri každej zmene druhov."""

    def __init__(self, dialog, feature, parent):
        super().__init__(PANEL_TITLE, parent)
        self.setObjectName(PANEL_NAME)
        self._dialog = dialog
        self._feature = feature
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setMinimumHeight(80)
        self.browser.setMaximumHeight(260)
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        box.addWidget(self.browser)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.refresh)

        signals = []
        for name in (DRUHY_LAYER, BIOTOPY_LAYER):
            for layer in QgsProject.instance().mapLayersByName(name)[:1]:
                for signal_name in ("featureAdded", "featureDeleted", "attributeValueChanged",
                                    "afterCommitChanges", "afterRollBack"):
                    signal = getattr(layer, signal_name)
                    signal.connect(self.schedule)
                    signals.append(signal)
        recordid_widget = dialog.findChild(QLineEdit, "RECORDID")
        if recordid_widget is not None:
            # formulár v tabuľke atribútov prepína prvky bez nového otvorenia
            recordid_widget.textChanged.connect(self.schedule)
        slot = self.schedule
        self.destroyed.connect(lambda *_args: _disconnect(signals, slot))
        self.refresh()

    def schedule(self, *_args):
        try:
            self._timer.start()
        except RuntimeError:
            pass  # formulár je už zatvorený

    def refresh(self):
        try:
            recordid = _value(self._dialog, self._feature, "RECORDID")
            if recordid is None:
                html = "<i>Polygón ešte nemá [ID polygónu].</i>"
            else:
                katalog, biotopy = _load_katalog()
                druhy = [(_as_int(kod), etaz, nazov) for kod, etaz, nazov
                         in _children(DRUHY_LAYER, recordid, ("KOD", "etaz", "NAZOV_LAT"))]
                zapisane = [b for (b,) in _children(BIOTOPY_LAYER, recordid,
                                                    ("biotop_cislo_new",))]
                results, missing = score_biotopes(druhy, katalog, biotopy, zapisane)
                html = render_html(results, missing, biotopy, len(druhy))
        except Exception as err:  # panel nesmie zablokovať formulár
            html = '<span style="color:#c62828">%s</span>' % _html(err)
        try:
            self.browser.setHtml(html)
        except RuntimeError:
            pass


def _add_biotop_panel(dialog, feature, tab):
    if tab.findChild(QGroupBox, PANEL_NAME) is None:
        _place_top(tab, _MozneBiotopy(dialog, feature, tab))


def kopiruj_druhy_form_open(dialog, layer, feature):
    tab = _find_tab(dialog, DRUHY_TAB)
    if tab is None:
        return
    _hide_qml_widget(tab)
    try:
        _add_biotop_panel(dialog, feature, tab)
    except Exception as err:  # chyba panelu nesmie zastaviť tlačidlo kopírovania
        QgsMessageLog.logMessage("Panel Možné biotopy: %s" % err, "MappingFormPS")
    button = tab.findChild(QPushButton, BUTTON_NAME)
    if button is None:
        button = QPushButton(BUTTON_LABEL, tab)
        button.setObjectName(BUTTON_NAME)
        button.setToolTip("Skopíruje druhy z formulára, ktorého ID je v poli "
                          "[ID rovnakého formulára].")
        _place(tab, button)
        button.clicked.connect(lambda: _copy(dialog, feature))

    def sync():
        button.setEnabled(_value(dialog, feature, "polygon_id_form") is not None)

    source_widget = dialog.findChild(QLineEdit, "polygon_id_form")
    if source_widget is not None:
        source_widget.textChanged.connect(lambda _text: sync())
    sync()
'''

t_druhy = make_container(root_h, "Druhy", "tab", visibility="True") #_VIS_A)
qml_copy = QgsAttributeEditorQmlElement("Kopírovanie druhov", t_druhy)
qml_copy.setQmlCode(
    _DRUHY_COPY_QML
    .replace("__FIELD_NAMES__", json.dumps([f.name() for f in druhy.fields()]))
    .replace("__BUTTON_LABEL__", _COPY_BUTTON_LABEL))
qml_copy.setShowLabel(False)
t_druhy.addChildElement(qml_copy)
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

cfg_h.setInitCodeSource(_CODE_SOURCE_DIALOG)
cfg_h.setInitCode(_FORM_INIT_CODE
                  .replace("__BUTTON_LABEL__", _COPY_BUTTON_LABEL)
                  .replace("__BIOTOP_SCORE_CODE__", _BIOTOP_SCORE_CODE))
cfg_h.setInitFunction("kopiruj_druhy_form_open")

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

# PrevodKatalogy is a many-to-many conversion table: it has placeholder rows
# ('-') where a biotope has no counterpart in the other catalogue, and codes
# repeated on several rows. first_2002/first_2023 = 1 marks the first row of
# each code — filtering on it keeps every dropdown entry unique.
set_widget(biotopy, "biotop_cislo", "ValueRelation", {
    "Layer": lkp_prevod.id(), "Key": "kod_2002", "Value": "nazov_2002",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
    "FilterExpression": "\"first_2002\" = 1 AND \"kod_2002\" IS NOT NULL AND trim(\"kod_2002\") NOT IN ('', '-')",
})
set_widget(biotopy, "biotop_cislo_new", "ValueRelation", {
    "Layer": lkp_prevod.id(), "Key": "kod_2023", "Value": "nazov_2023",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
    "FilterExpression": "\"first_2023\" = 1 AND \"kod_2023\" IS NOT NULL AND trim(\"kod_2023\") NOT IN ('', '-')",
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
    "attribute(get_feature('tblHabHlavna', 'RECORDID', \"fkRECORDID\"), 'typ_polygon') != 'B'"
)

grp_bio = make_container(root_b, "Biotop")
for f in ["biotop_cislo_new", "biotop_pokryv", "biotop_cislo"]:
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

# kod_kbx: new-catalogue biotope code, same PrevodKatalogy dropdown (and the same
# first_2023 de-duplication) as tblHabBiotopy.biotop_cislo_new
set_widget(druhy, "kod_kbx", "ValueRelation", {
    "Layer": lkp_prevod.id(), "Key": "kod_2023", "Value": "nazov_2023",
    "AllowNull": True, "UseCompleter": True, "OrderByValue": False,
    "CompleterMatchFlags": 1,
    "FilterExpression": "\"first_2023\" = 1 AND \"kod_2023\" IS NOT NULL AND trim(\"kod_2023\") NOT IN ('', '-')",
})

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
    lkp_prevod:       "copy",
}.items():
    layer.setCustomProperty("QFieldSync/action", action)
    print(f"  {layer.name()}: {action}")

hlavna.setDisplayExpression('"RECORDID" || \' – \' || "podlaorta" || \' – \' || "datum"')
# concat() (not ||) so a NULL code or coverage does not collapse the whole
# expression to NULL — QGIS/QField would then fall back to the raw fid in the
# Biotopy relation-editor list instead of "<kod> - <pokryvnost>".
biotopy.setDisplayExpression('concat("biotop_cislo_new", \' - \', "biotop_pokryv")')
druhy.setDisplayExpression('"NAZOV_LAT" || \' – \' || "etaz" || \' – \' || "POKRYVNOST"')
aktivity.setDisplayExpression('"Aktivita" || \' – \' || "Perc_Plochy"')
print("  display expressions set")

root = project.layerTreeRoot()
for lyr in [lkp_aktivita, lkp_aktivita_new, lkp_druhy, lkp_biotop, lkp_biotop_new, lkp_prevod]:
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
# RECORDID, not polygon_id: this is the ID mappers read off the map and type into
# [polygon_id_form], and it matches the "ID polygónu" field in the form.
pal.fieldName = (
    'if("polygon_id_form" IS NOT NULL, '
    'concat("RECORDID", \'=\', "polygon_id_form"), '
    '"RECORDID")'
)
pal.isExpression = True
pal.enabled = True

buf = QgsTextBufferSettings()
buf.setEnabled(True)
buf.setSize(1.0)
buf.setColor(QColor("white"))

fmt = QgsTextFormat()
fmt.setSize(9)
fmt.setBuffer(buf)

# Small red labels mark polygons that are done with: either already mapped
# ([typ_polygon] filled in) or linked to another polygon's form
# ([polygon_id_form] filled in). Everything still to map stays black at 9 pt.
_LBL_DONE = '("typ_polygon" IS NOT NULL) OR ("polygon_id_form" IS NOT NULL)'

dd = pal.dataDefinedProperties()
dd.setProperty(
    QgsPalLayerSettings.Color,
    QgsProperty.fromExpression(
        f"if({_LBL_DONE}, '#ff0000', '#000000')"
    ),
)
dd.setProperty(
    QgsPalLayerSettings.Size,
    QgsProperty.fromExpression(
        f"if({_LBL_DONE}, 7, 9)"
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
