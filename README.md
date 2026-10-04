# ikea-image2STL

Python-CLI, die bemaßte IKEA-Möbelzeichnungen (PNG, Maße in cm und Zoll) in parametrische 3D-Modelle umwandelt und als STEP und STL exportiert.

```
Zeichnung.png ──extract──▶ specs/<name>.json ──build──▶ out/<name>/*.step|*.stl ──validate──▶ validation.json
                (Claude)    (von Hand prüfen)  (build123d)
```

## Setup

```bash
uv sync --dev                      # Python 3.12, build123d, anthropic, pytest
uv run furniture --help
```

`cadquery-ocp-novtk` (OCP-Kernel von build123d) linkt direkt gegen `libGL.so.1` (per `ldd` geprüft). Ohne `libgl1` schlägt der Import fehl, der libGL-Block im Setup-Skript bleibt also nötig.

Umgebungsvariablen: `ANTHROPIC_API_KEY` (nur für `extract` und `all`), `CLAUDE_MODEL` (Default `claude-sonnet-5-5`).

## Befehle

| Befehl | Was passiert |
|---|---|
| `furniture extract BILD [--spec PFAD] [--type TYP] [--model M]` | Claude liest die Maße und schreibt `specs/<bildname>.json`. Ist die Spec ungültig, wird sie trotzdem geschrieben, Exit-Code 1 und Fehlerliste zum Korrigieren. |
| `furniture build SPEC [--out out]` | Validiert die Spec, baut die Teile und schreibt `out/<name>/<name>.step`, `<name>.stl` und `parts/<teil>.step/.stl`. |
| `furniture validate SPEC [--out out]` | Prüft die exportierten Dateien, schreibt `out/<name>/validation.json`, Exit-Code 1 bei Fehlern. |
| `furniture all BILD [...]` | `extract`, `build`, `validate` hintereinander. Stoppt, wenn die extrahierte Spec ungültig ist. |

Empfohlener Ablauf: `extract`, Spec von Hand prüfen und korrigieren, dann `build` und `validate`.

Beispiel mit den mitgelieferten, von Hand geprüften Specs (ohne API-Key):

```bash
uv run furniture build specs/couchtisch.json && uv run furniture validate specs/couchtisch.json
```

## Spec-Format

Alle Längen in mm. Koordinaten: X = Breite (links→rechts), Y = Tiefe (vorne y=0 → hinten), Z = Höhe (Boden z=0).

```jsonc
{
  "schema_version": 1,
  "name": "couchtisch",              // Ausgabeordner
  "furniture_type": "coffee_table",  // cabinet | coffee_table | bench
  "source_image": "fixtures/drawings/couchtisch.png",
  "units": "mm",
  "overall": {"width": 900, "depth": 550, "height": 450},
  "params": {"top_thickness": 50, ...},          // vollständig, je Typ fest definiert
  "measurements": [                               // was in der Zeichnung steht
    {"label": "height", "cm": 45, "inch": "17 3/4", "maps_to": "overall.height"}
  ],
  "assumptions": ["..."],
  "ambiguities": ["..."]
}
```

`validate_spec` (`src/furniture_cli/spec.py`) prüft vor jedem Build:

- Struktur, Typ, alle Parameter des Typs vorhanden, keine unbekannten, positive Werte, Stückzahlen ganzzahlig.
- cm- und Zoll-Angabe jeder Messung stimmen auf 8 mm überein (IKEA rundet cm auf ganze cm und Zoll auf 1/8"; größere Abweichungen sind Lesefehler wie 27 statt 72).
- Jede Messung stimmt auf 0,5 mm mit dem Spec-Wert überein, auf den `maps_to` zeigt. Korrigiert man von Hand einen Wert, muss man die Messung mit anpassen (oder entfernen).
- Geometrische Machbarkeit je Typ, z. B. beim Couchtisch `top_thickness + shelf_clearance_below_top + shelf_thickness + shelf_height == height`.

Die Parameter je Typ, ihre Bedeutung und die Default-Annahmen stehen in `FURNITURE_TYPES` in `spec.py`. Die Templates (`templates.py`) enthalten keine Maße: jede Länge kommt aus der Spec. Defaults werden nur in `extract` eingesetzt und landen dabei sichtbar in der Spec und in `assumptions`.

## Validierung

`validate` liest die exportierten Dateien, nicht die In-Memory-Geometrie:

- Jede Teil-STEP-Datei wird neu importiert: genau ein Solid, `is_valid`, alle Shells geschlossen (jede Kante an genau zwei Flächen), Volumen > 0.
- Jede Teil-STL ist wasserdicht: jede Kante gehört zu genau zwei Dreiecken, die sie in entgegengesetzter Richtung durchlaufen (keine Löcher, nicht-manifold Kanten oder gekippten Normalen). Eigener STL-Parser in `mesh.py`, nur Standardbibliothek.
- Bounding Box der Gesamt-STEP und der Gesamt-STL entspricht Breite, Tiefe und Höhe der Spec auf 0,5 mm.

## Extract

`extract.py` schickt das Bild an die Messages API mit Structured Outputs (`output_config.format` mit JSON-Schema). Der Prompt enthält alle Typen und Parameter mit Bedeutung und Default. Das Modell markiert jeden Parameter als `drawing` oder `assumed`. Angenommene Werte und fehlende Parameter (mit Default aufgefüllt) werden in `assumptions` vermerkt.

Hinweise:

- `extract` wurde in dieser Umgebung nicht live gegen die API ausgeführt (kein API-Key in der Session). Die Tests decken die Anfrage und die Umwandlung mit einem Fake-Client ab.
- Bei `stop_reason == "refusal"` bricht `extract` mit Fehlermeldung ab; ein serverseitiger Fallback auf ein anderes Modell ist nicht konfiguriert.
- Tests brauchen keinen API-Key (`tests/conftest.py` entfernt ihn sogar).

## Referenzzeichnungen und Annahmen

Die drei Zeichnungen liegen in `fixtures/drawings/`, die von Hand geprüften Specs in `specs/`. Jede Spec enthält ihre Annahmen und Mehrdeutigkeiten auch maschinenlesbar.

### Schrank (`schrank.png`, Typ `cabinet`): 70 × 35 × 70 cm

Gelesen: Breite 70 cm (27 ½"), Höhe 70 cm (27 ½"), Tiefe 35 cm (13 ¾").

Annahmen:
- Die Tiefe 35 cm enthält die Türen; Türen liegen bündig innen in der Front (inset).
- Korpusplatten und Türen 18 mm, Rückwand 3 mm (zwischen Seiten, Boden und Deckel eingesetzt), Fugen 3 mm. Nicht bemaßt.
- Zwei Türen wegen der senkrechten Teilung in der Vorderansicht.
- Keine Einlegeböden (`shelf_count: 0`), die Zeichnung zeigt keine. Der Parameter ist vorhanden.

Mehrdeutigkeiten:
- Die abgeschrägten Ecken des Außenrahmens deuten auf eine Gehrung oder gerundete Kante hin; modelliert als rechtwinklige Kanten.
- Die Doppellinie innerhalb des Rahmens kann ein Türrahmenprofil oder die Fuge sein; modelliert als einfache Fuge.

### Couchtisch (`couchtisch.png`, Typ `coffee_table`): 90 × 55 × 45 cm

Gelesen: Länge 90 cm (35 ⅜"), Breite 55 cm (21 ⅝"), Höhe 45 cm (17 ¾"), 19 cm (7 ½") und 20 cm (7 ⅞") seitlich.

Annahmen:
- 19 cm = Unterkante Tischplatte bis Oberkante Ablage, 20 cm = Boden bis Unterkante Ablage. Die kleine Lücke zwischen den beiden Maßketten ist die Ablagenstärke.
- Ablage 10 mm (nicht bemaßt), daraus Plattenstärke 450 − 190 − 10 − 200 = 50 mm.
- Beine 50 × 50 mm, bündig mit den Plattenkanten.
- Die Ablage liegt zwischen den Innenseiten der Beine und nutzt die volle Tiefe.

Mehrdeutigkeiten:
- Die Maßlinien 19/20 cm sitzen in der Perspektive an einem hinteren Bein. Läse man 19 cm ab Oberkante Platte, ginge die Summe nicht auf 45 cm auf; deshalb die obige Lesart.
- Die Ablagenhalter unter der Mitte sind nicht bemaßt und weggelassen.

### Bank (`bank.png`, Typ `bench`): 113 × 58 × 84 cm

Gelesen: Breite 113 cm (44 ½"), Tiefe 58 cm (22 ⅞"), Höhe 84 cm (33 ⅛"), Sitzhöhe 44 cm (17 ⅜"), 103 cm (40 ½"), 41 cm (16 ⅛").

Annahmen:
- Das Rattangestell wird zu Rechteckprofilen vereinfacht: je Seite Vorderpfosten, Hinterpfosten (volle Höhe), Armlehne und untere Strebe, zu einem Solid vereinigt. Seitenrahmenbreite (113 − 103) / 2 = 5 cm.
- Gewölbte Sitzfläche und Rückenlehne als ebene Platten. Die Lehne neigt sich von der Hinterkante der Sitzfläche zur oberen hinteren Ecke.
- Nicht bemaßt, aus der Zeichnung geschätzt: Armlehnenoberkante 620 mm, Strebe 150 mm über Boden, Profil 40 mm, Sitzplatte 40 mm, Lehne 30 mm.
- Vorderkante der Sitzfläche bündig mit den Vorderpfosten.

Mehrdeutigkeiten:
- 41 cm ist auf Armlehnenhöhe von der Lehne bis zum Vorderpfosten eingezeichnet; gelesen als Sitztiefe, könnte auch die Armlehnenlänge sein.
- 103 cm ist perspektivisch diagonal eingezeichnet; gelesen als lichte Breite zwischen den Seitenrahmen.
- 58 cm ist am Boden zwischen ausgestellten Beinen gemessen; das Modell hat senkrechte Pfosten und nutzt die volle Tiefe in jeder Höhe.
- 84 cm wird als Oberkante der Lehne genommen; die gerollte Oberkante ist abgeflacht.

## Allgemeine Annahmen

- Alle Teile sind Volumenkörper ohne Verbindungsmittel (Dübel, Schrauben, Beschläge).
- Teile berühren sich, durchdringen sich aber nicht. Jedes Teil wird einzeln exportiert und geprüft; die Gesamtdateien enthalten alle Teile als Compound (STEP mit Teilnamen als Label).
- STL-Tesselierung mit 0,01 mm Sehnentoleranz; bei den aktuellen, rein ebenen Teilen ohne Einfluss auf die Maße.

## Tests

```bash
uv run pytest
```

Abgedeckt: Spec-Validierung, Zoll-Parser, STL-Wasserdichtheit (Loch, gekipptes Dreieck, Binär- und ASCII-STL), Build und Validierung aller drei Referenz-Specs, Parametrik (andere Gesamtmaße, Türen und Böden verschieben die Geometrie), Erkennung einer 1-mm-Abweichung, `extract` und `all` mit Fake-Client.
