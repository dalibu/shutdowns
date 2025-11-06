## Kurzüberblick

Dieses Repository enthält mehrere kleine Python-Skripte, die die Seite
`https://www.dtek-dnem.com.ua/ua/shutdowns` mit Playwright scrapen und
Informationen zu geplanten/aktuellen Abschaltungen extrahieren. Wichtige
Entrypoints:

- `dtek_disconnection_gemini.py` — robustere, asynchrone Variante: schließt
  Modalfenster, tippt mit Verzögerung in Autocomplete-Felder, klickt das
  erste Ergebnis an, wartet auf Laden und parst die Tabelle; schreibt
  `discon-fact.json` und `discon-fact.png`.
- `dtek_disconnection.py` — alternative Implementierung mit `page.evaluate`
  (synchronere DOM-Auswertung, headful mode). 
- `dtek_disconnection_chatgpt.py`, `dtek_disconnection_grok.py` — Hilfs-/Experiment-Skripte.
- Prompt-/Konfigurationsdateien: `dtek_disconnection_prompt.txt`,
  `dtek_disconnection_bot_prompt.txt` — verwenden diese Skripte als Texteingaben.

## Warum so aufgebaut

- Ziel ist ein zuverlässiger Browser-basierten Scraper gegen dynamische
  Autocomplete-Elemente und eine tabellarische Auswertung. Playwright wird
  asynchron benutzt, um Ablaufsteuerung und Timeouts präzise zu steuern.

## Schnellstart / Dev-Setup

1. Python-virtuelle Umgebung anlegen und aktivieren.
2. Playwright installieren und Browser-Binaries: `pip install playwright` + `playwright install`.
3. Skript lokal ausführen: `python dtek_disconnection_gemini.py` (oder die gewünschte Variante).

Hinweis: Es gibt (noch) keine `requirements.txt` im Repo; wenn Du neue
Dependencies hinzufügst, lege eine solche Datei an.

## Wichtige Patterns und Konventionen (Repository-spezifisch)

- Async Playwright: Skripte verwenden `async_playwright` + `asyncio.run(...)`.
- Autocomplete-Interaktion:
  - Verwende `page.type(selector, value, delay=...)` statt `fill` bei Autocomplete.
  - Warte auf das Autocomplete-Widget (`wait_for_selector(..., state="visible")`).
  - Klicke das erste Ergebnis (`{autocomplete_selector} > div:first-child`).
  - Warte bis das Widget verschwindet (`state="hidden"`).
- Modalfenster: `dtek_disconnection_gemini.py` prüft `div.modal__container.m-attention__container` und schließt mit `button.modal__close.m-attention__close`.
- Timeouts: typische Zeitlimits sind 5–20s (konstruiere robuste Fallbacks,
  verwende `wait_for_timeout` nur als letztes Mittel).
- Logging: einfache `log(msg)`-Hilfsfunktion (grüne Präfixe) — benutze sie
  zur Konsistenz in neuen Skripten.

## Parsing-Kontrakte (Datenformate)

- Ausgabe-Datei: `discon-fact.json` im Repo-Root.
- JSON-Shape (Beispiel):

```
[{
  "date": "2025-11-02",
  "slots": [
    {"time": "09:00 – 11:00", "disconection": "full"},
    {"time": "11:00 – 13:00", "disconection": "half"}
  ]
}]
```

- Mapping CSS -> Status:
  - `cell-scheduled` -> `full`
  - `cell-first-half` / `cell-second-half` -> `half`

- Wichtige Selektoren (als Beispiele):
  - Datum: `#discon-fact > div.dates > div.date.active > div:nth-child(2) > span`
  - Tabelle: `#discon-fact > div.discon-fact-tables table`

## Debugging-Tipps (konkret)

- Um UI zu sehen setze `headless=False` in `p.chromium.launch(...)` und
  reduziere `slow_mo` nicht zu stark. Das zeigt Modal/Autocomplete-Interaktionen.
- Wenn Parsen fehlschlägt: erstelle Screenshots von `#discon-fact` oder
  der ganzen Seite (`page.screenshot`) — existiert im Code als Fallback.
- Modal blockiert Flow? Prüfe die oben genannten Modal-Selektoren; wenn
  die Seite das Modal ändert, aktualisiere die Selektoren.

## Integration & externe Abhängigkeiten

- Externe Dienste: nur die Zielwebsite `dtek-dnem.com.ua` und lokale
  Playwright-Browserbinaries. Keine API-Keys in Repo.
- Konsequenz: Tests, die Live-Requests ausführen, sind fragil; stub/Mock
  DOM oder schreibe kleine HTML-Fixtures für Parsing-Unit-Tests.

## Vorschläge / nächste Schritte (low-risk)

- Ergänze `requirements.txt` mit `playwright` und evtl. `pytest`.
- Füge eine kleine Integrationstestdatei hinzu, die lokal gespeicherte
  HTML-Snippets parst (verifiziert Parsing-Logic ohne Live-Requests).
- Entferne oder guardiere Blocking-`input(...)`-Aufrufe, wenn du CI-kompatibel
  laufen möchtest.

## Dateien, die beim Start zu prüfen sind

- `dtek_disconnection_gemini.py` (robustes Beispiel, bevorzugte Vorlage)
- `dtek_disconnection.py` (DOM-evaluate-Version — gutes Referenzbeispiel für direktes JS-Extrahieren)
- `dtek_disconnection_prompt.txt`, `dtek_disconnection_bot_prompt.txt` (gute Beispiele für prompt-Design im Repo)

Wenn etwas unklar ist oder Du möchtest, dass ich die Instruktionen ausführlicher
mit Commands/CI-Hooks erweitere, sag mir kurz welche Bereiche priorisiert werden sollen.
