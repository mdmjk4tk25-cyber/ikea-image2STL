# ikea-image2STL

Python-CLI, die bemaßte IKEA-Möbelzeichnungen (PNG, Maße in cm und Zoll) in parametrische 3D-Modelle umwandelt und als STEP und STL exportiert.

```
Zeichnung.png ──extract──▶ specs/<name>.json ──build──▶ out/<name>/*.step|*.stl ──validate──▶ validation.json
              (lokale OCR)  (von Hand prüfen)  (build123d)
```

## Setup

```bash
uv sync --dev                      # Python 3.12, build123d, rapidocr-onnxruntime, pytest
uv run furniture --help
```

`cadquery-ocp-novtk` (OCP-Kernel von build123d) linkt direkt gegen `libGL.so.1` (per `ldd` geprüft). Ohne `libgl1` schlägt der Import fehl, der libGL-Block im Setup-Skript bleibt also nötig.

Alles läuft lokal. Netzwerk wird nur für `uv sync` (PyPI) gebraucht, zur Laufzeit gibt es keine externen Aufrufe und keinen API-Key.

## Befehle

| Befehl | Was passiert |
|---|---|
| `furniture extract BILD... --type TYP [--spec PFAD] [--ocr-scales 1,2,3]` | Lokale OCR liest die Maße, eine Regel je Möbeltyp ordnet sie den Spec-Feldern zu, Ergebnis `specs/<bildname>.json`. `TYP` ist `cabinet`, `coffee_table` oder `bench`. Ist die Spec ungültig, wird sie trotzdem geschrieben, Exit-Code 1 und Fehlerliste zum Korrigieren. |
| `furniture build SPEC... [--out out]` | Validiert die Spec, baut die Teile und schreibt `out/<name>/<name>.step`, `<name>.stl` und `parts/<teil>.step/.stl`. |
| `furniture validate SPEC... [--out out]` | Prüft die exportierten Dateien, schreibt `out/<name>/validation.json`, Exit-Code 1 bei Fehlern. |
| `furniture all BILD... --type TYP [...]` | `extract`, `build`, `validate` hintereinander. Stoppt, wenn die extrahierte Spec ungültig ist. |

Alle Befehle nehmen mehrere Eingaben und verarbeiten sie parallel (`--jobs N`, Default: ein Prozess je logischem Kern, höchstens so viele wie Eingaben). `--spec` und `--name` gehen nur mit einem Bild; bei mehreren landet jede Spec unter `specs/<bildname>.json`.

Empfohlener Ablauf: `extract`, Spec von Hand prüfen und korrigieren, dann `build` und `validate`.

Beispiel mit den mitgelieferten, von Hand geprüften Specs:

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

## Extract (lokal)

`extract.py` arbeitet in drei Schritten, ohne Netzwerk:

1. **OCR:** [RapidOCR](https://github.com/RapidAI/RapidOCR) (`rapidocr-onnxruntime`, PaddleOCR-Modelle als ONNX, im Wheel enthalten) liefert jede Textbox mit Position und Ausrichtung. Boxen mit `NN cm` werden zu Maßen. Zoll-Text in einer eigenen Box (zweizeilige Beschriftung) wird dem nächsten cm-Maß zugeordnet.
2. **Zuordnung je Typ:** Eine Regel ordnet die cm-Werte den Spec-Feldern zu. Eine Box gilt als senkrecht, wenn sie mehr als 1,5-mal so hoch wie breit ist.
   - `cabinet`: senkrecht = Höhe; waagerecht größter Wert = Breite, nächster = Tiefe.
   - `coffee_table`: waagerecht größter = Länge, nächster = Breite; senkrecht größter = Höhe; von den übrigen senkrechten das obere = Plattenunterkante bis Ablage, das untere = Boden bis Ablage. `top_thickness` wird daraus und aus der angenommenen Ablagenstärke berechnet.
   - `bench`: waagerecht nach Größe = Breite, Sitzbreite, Tiefe, Sitztiefe; senkrecht nach Größe = Höhe, Sitzhöhe.
3. **Defaults:** Nicht bemaßte Parameter bekommen ihren Default und einen Eintrag in `assumptions`. Fehlende Gesamtmaße werden auf 0 gesetzt und in `ambiguities` gemeldet; die Spec ist dann ungültig und muss von Hand ergänzt werden.

Zoll-Angaben dienen nur der Gegenprüfung. OCR liest Brüche oft falsch („½“ als `12` oder `%2`, `173/4` statt `17 3/4`, `s` statt `8`). Diese Fälle werden korrigiert; ist der Zoll-Wert danach nicht plausibel zum cm-Wert, bleibt `inch` leer und die Gegenprüfung entfällt.

### Genauigkeit auf den Referenzzeichnungen

| Zeichnung | cm-Maße gelesen | richtig zugeordnet | Zoll lesbar |
|---|---|---|---|
| Schrank | 3/3 | 3/3 | 3/3 |
| Couchtisch | 5/5 | 5/5 | 5/5 |
| Bank | 6/6 | 6/6 | 2/6 |
| **Summe** | **14/14** | **14/14** | **10/14** |

Die erzeugten Specs sind identisch mit den Hand-Specs in `specs/` (Gesamtmaße und Parameter). Bei den Parametern liegt das daran, dass die Hand-Specs dieselben Defaults verwenden; gelesen werden nur die bemaßten Werte. Mit nur einem OCR-Durchgang (`--ocr-scales 1`) sind es 9/14 Zoll-Werte bei gleichen cm-Werten.

Grenzen: Die Zuordnungsregeln sind auf das Layout dieser IKEA-Zeichnungen zugeschnitten (Wertrangfolge, Ausrichtung, Position). Bei anderen Layouts kann eine Zuordnung falsch sein; die Validierung fängt das nur ab, wenn die Werte geometrisch nicht zusammenpassen. Deshalb die Spec vor `build` prüfen.

Verworfene Alternativen:
- **Tesseract 5.3:** las auf denselben Zeichnungen nur 4 von 14 cm-Maßen richtig (2 weitere falsch, z. B. `403` statt `103`); gedrehte und schräge Beschriftungen fehlten.
- **Lokales Vision-Sprachmodell (z. B. über Ollama):** mehrere GB Modell, auf CPU langsam und nicht deterministisch. Die OCR liest hier schon alle Maße; das Schwierige ist die Zuordnung, und die ist mit Regeln nachvollziehbar und testbar.

## Hardware und Leistung

Zielsystem: Framework Desktop, AMD Ryzen AI Max+ 395 (16 Kerne, 32 Threads, Radeon 8060S, NPU), 128 GB RAM, Windows x64.

- **Windows:** Alle Abhängigkeiten haben Wheels für `win_amd64` / CPython 3.12 (in `uv.lock` geprüft: `cadquery-ocp-novtk`, `onnxruntime`, `opencv-python`, `numpy`). Der libGL-Hinweis oben betrifft nur Linux. Bilder werden über `np.fromfile` gelesen, damit Pfade mit Umlauten unter Windows funktionieren.
- **Mehrere Kerne:** `--jobs` startet Worker-Prozesse mit `spawn` (unter Windows ohnehin die einzige Methode; `fork` neben ONNX-Runtime- und OCCT-Threads kann hängen). Bei `extract` teilen sich die Worker die Kerne: jeder ONNX-Runtime-Prozess bekommt `logische Kerne / Jobs` Threads, damit 32 Threads nicht 32-fach überbucht werden.
- **Rechenreserve für Genauigkeit:** Standardmäßig läuft die OCR dreimal (1×, 2×, 3× hochskaliert), die Ergebnisse werden per Mehrheitsentscheid zusammengeführt. Das kostet etwa die dreifache OCR-Zeit und bringt hier einen zusätzlichen Zoll-Wert (10 statt 9 von 14); die cm-Werte sind in beiden Fällen 14/14. `--ocr-scales 1` schaltet das ab.
- **Gemessen** (Linux-Container mit 4 Kernen, nicht auf dem Zielsystem): 8 Zeichnungen `extract` seriell 21,9 s, mit `-j 4` 15,4 s. ONNX Runtime nutzt schon im Einzelprozess mehrere Threads, deshalb skaliert es nicht linear. Auf dem Zielsystem wurde nichts gemessen.

Bewusst nicht genutzt:
- **GPU (Radeon 8060S über DirectML):** bräuchte `onnxruntime-directml` statt `onnxruntime` (beide liefern dasselbe Python-Modul, also nicht gleichzeitig installierbar) und ist hier nicht testbar. Die OCR-Modelle sind klein; Kopieren zur GPU frisst den Gewinn bei einzelnen Bildern weitgehend auf.
- **NPU (XDNA 2):** braucht die Ryzen-AI-Software und quantisierte Modelle; viel Aufwand bei ohnehin kurzer CPU-Laufzeit.
- **128 GB RAM:** Das Programm braucht pro Prozess wenige hundert MB; Speicher begrenzt hier nichts.

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

Abgedeckt: Spec-Validierung, Zoll-Parser, STL-Wasserdichtheit (Loch, gekipptes Dreieck, Binär- und ASCII-STL), Build und Validierung aller drei Referenz-Specs, Parametrik (andere Gesamtmaße, Türen und Böden verschieben die Geometrie), Erkennung einer 1-mm-Abweichung, `extract` mit echter OCR auf allen drei Zeichnungen (Werte und Zuordnung müssen den Hand-Specs entsprechen), Zuordnungsregeln und Zoll-Normalisierung mit Fake-OCR, `all` komplett lokal.
