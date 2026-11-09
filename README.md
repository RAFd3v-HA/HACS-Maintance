# Maintenance Planner für Home Assistant

Lokale Wartungsplanung und Materialverwaltung als Custom Integration. Für
beliebige Geräte, Fahrzeuge, Anlagen oder andere wiederkehrende Aufgaben.

Version **0.3.0** benötigt Home Assistant **2026.10 oder neuer**.

Die Tests mit Home Assistant **2026.11.0** öffnen die Formulare über
dieselben HTTP-Endpunkte wie die Home-Assistant-Oberfläche.

## Funktionen

- Einrichtung und Verwaltung vollständig über die Home-Assistant-Oberfläche
- Wartungen mit Datum, Beschreibung und Wiederholung in Tagen, Wochen oder Monaten
- Wartungen anhand von Zuständen, Attributen oder Schwellenwerten vorhandener
  Entitäten automatisch fällig setzen
- Geräteauswahl zum Filtern der Entitäten für eine Wartungsmeldung
- Materialien mit Bestand, Einheit, Mindestbestand und Notizen
- Verbrauchsartikel und Mengen beim Anlegen auswählen und bei Erledigung
  automatisch abbuchen
- Erinnerungen zu einem Termin oder bei einem Entitätssignal mit Materialbestand
  und optionalem Handy-Push
- Kein Teilverbrauch, wenn für eine Wartung Material fehlt
- Native Aufgabenliste für Wartungen und berechnete Material-Einkaufsliste
- Sensoren für fällige Wartungen, nächsten Termin, fehlende Materialien und
  jeden Materialbestand
- Aktionen für Automationen und eine Historie der letzten 1000 Erledigungen
- Speicherung lokal im Home-Assistant-Konfigurationsverzeichnis
- Deutsche und englische Texte; keine Cloud und keine zusätzlichen Laufzeitpakete

## Installation

### Manuell

1. Kopiere den Ordner `custom_components/maintenance_planner` nach
   `<config>/custom_components/maintenance_planner`.
2. Starte Home Assistant neu.
3. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen** und
   suche nach **Maintenance Planner**.
4. Vergib einen Namen und lege den Planungshorizont für Einkäufe fest
   (standardmäßig 30 Tage).

Es ist kein Eintrag in der `configuration.yaml` erforderlich. Pro Installation
gibt es einen gemeinsamen Planer.

### Über HACS installieren

1. Füge das Repository unter **HACS → Benutzerdefinierte Repositories** in der
   Kategorie **Integration** hinzu. [GitHub-Repository](https://github.com/RAFd3v-HA/HACS-Maintance)
2. Lade den aktuellen Stand des Wartungsplaners herunter und starte
   Home Assistant neu.
3. Füge **Maintenance Planner** unter **Einstellungen → Geräte & Dienste** hinzu.

Beim Hochladen einer neuen Version auf GitHub müssen `hacs.json` und
`custom_components/` direkt im Hauptverzeichnis des Repositorys liegen.

## Bedienung

Öffne bei der Integration **Konfigurieren** beziehungsweise das Zahnrad:

1. **Material hinzufügen**: beispielsweise „Filter“, Bestand 3, Mindestbestand 1,
   Einheit „Stück“.
2. Wähle **Wartung hinzufügen** und gib Name, Datum oder Wartungsmeldung und
   gegebenenfalls einen Rhythmus ein. Für „alle 8 Wochen“ wählst du die Anzahl 8
   und die Einheit **Wochen**. Anzahl 0 schaltet den Zeitrhythmus aus.
3. Wähle unter **Verbrauchsartikel** die benötigten Artikel und gib je Artikel
   die Menge pro Durchführung an. Über **Material zuordnen** kannst du die
   Mengen später ändern. Menge 0 entfernt dort eine Zuordnung.
4. Stelle bei Bedarf **Erinnerung** und **Handy-Push** ein.
5. Öffne in der Seitenleiste **Aufgabenlisten** die Liste **Wartungen**.
   Beim Abhaken wird Material verbraucht und der nächste Termin berechnet.
6. Fehlendes Material steht in **Material einkaufen**. Nach dem Einkauf den
   Bestand über **Material bearbeiten** setzen oder die Aktion
   `maintenance_planner.adjust_stock` verwenden.

Neue einmalige Aufgaben können auch direkt in der Aufgabenliste angelegt
werden. Wiederholung und Materialzuordnung werden in den Integrationsoptionen
ergänzt. Wartungen und Materialien lassen sich dort auch bearbeiten und löschen.
Vor dem Löschen eines Materials müssen seine Zuordnungen entfernt werden.

Die Einkaufsliste wird automatisch berechnet und ist schreibgeschützt. Ein
gekauftes Material verschwindet, sobald ausreichend Bestand eingetragen ist.

## Wartungen aus Entitätsmeldungen erkennen

Jede Wartung kann eine Regel für eine vorhandene Entität erhalten. Unterstützt
werden der genaue Zustand oder ein Attribut sowie numerische Vergleiche
**größer als** und **kleiner als**. Die Erkennung erfolgt über diese gezielt
konfigurierten Regeln; Texte aus beliebigen Meldungen werden nicht frei
interpretiert.

Für die BWT-Anlage legst du **Salz nachfüllen** an und aktivierst
**Wartungsmeldung verwenden**. Wähle die Anlage als **Gerät für Wartungsmeldung**.
Im nächsten Schritt wählst du `binary_sensor.bwt_perla_duplex_salzvorrat_leer`
und den Zustand `on`. Für eine Wartung allein nach Bedarf lässt du das Datum
leer und stellst die Wiederholung auf 0. Wenn **Salz** bereits als Material
angelegt ist, kannst du beim Anlegen auch die benötigte Menge in kg eintragen.

Sobald die Regel zutrifft, wird „Salz nachfüllen“ heute fällig. Ein bereits
überfälliger Termin bleibt erhalten. Ein früher erledigter Eintrag wird wieder
geöffnet. Material wird dabei noch nicht verbraucht: Der Bedarf geht in die
Einkaufsliste ein, und die Abbuchung erfolgt erst beim Erledigen.

Die Regel prüft auch einen bereits aktiven Sensor beim Einrichten oder Start.
Nach dem Erledigen löst ein weiterhin aktiver Sensor nicht erneut aus. Erst
ein gültiger, nicht passender Wert (hier `off`) und anschließend ein erneutes
`on` aktivieren die Wartung wieder. Diese Sperre bleibt über Neustarts erhalten.
`unknown`, `unavailable`, fehlende Attribute und ungültige Zahlen werden
ignoriert und gelten nicht als Entwarnung. Ein Wechsel auf `off` erledigt die
Wartung nicht automatisch.

Zeitplan und Entitätsregel lassen sich kombinieren: Ein Signal kann die nächste
Wartung vorziehen; nach dem Erledigen beginnt der gewählte Rhythmus erneut.
Für Attribute werden Wahrheitswerte als `true`/`false` verglichen, sonst als
Text. Der Vergleich **ist gleich** unterscheidet Groß- und Kleinschreibung;
numerische Grenzen sind strikt (`below: 5` bedeutet kleiner als 5).

Unter **Auslöseregel festlegen** kannst du Regeln nachträglich ändern,
deaktivieren oder mit Attributen und Schwellenwerten verfeinern. Falls eine
Quellentität umbenannt oder gelöscht wird, die Regel entsprechend aktualisieren.

## Erinnerungen

Beim Anlegen oder Bearbeiten einer Wartung wählst du **Aus**,
**Am Fälligkeitstag**, **Eine Woche vorher** oder **Bei Entitätssignal**.
Die Erinnerung erscheint in Home Assistant. Unter **Handy-Push** kannst du
zusätzlich ein Telefon auswählen, das bereits für Benachrichtigungen über die
Companion App registriert ist.

Mit **Bei Entitätssignal** wählst du im nächsten Schritt die Entität und ihren
auslösenden Zustand. Das ausgewählte Gerät grenzt die Liste ein. So kann bei
BWT **Salz fast leer** eine Vorwarnung senden, während **Salz leer** die Wartung
fällig setzt. Die Vorwarnung funktioniert ohne geplantes Datum und wird bei
einem erneuten Wechsel in den passenden Zustand wieder ausgelöst.

Die Nachricht nennt pro Artikel die benötigte, vorhandene und fehlende Menge
zum Zeitpunkt der Erinnerung. Bei Zeiterinnerungen prüft der Planer täglich um 9 Uhr Ortszeit,
beim Start und bei Änderungen des Termins oder der Erinnerungseinstellungen.
Eine offene Wartung im Erinnerungszeitraum kann daher auch direkt nach dem
Anlegen eine Nachricht auslösen. Wenn erst eine Wartungsmeldung das Datum
festlegt, ist die Wartung ab diesem Moment fällig und die Erinnerung kann
ab dann erscheinen.

## Planung und Bestände

- Wiederholungen starten am **tatsächlichen Erledigungsdatum**. Ein Abschluss
  am 6. November mit 6 Monaten Intervall wird am 6. Mai wieder fällig.
- Ein Wochenintervall entspricht genau 7 Tagen: 8 Wochen sind 56 Tage.
- Monatsintervalle berücksichtigen unterschiedlich lange Monate: Ein Abschluss
  am 31. Januar mit 1 Monat Intervall wird am letzten Februartag fällig.
- Wiederkehrende Aufgaben bleiben nach dem Abhaken mit dem Folgetermin offen.
  Einmalige Aufgaben wandern in die erledigten Einträge.
- Die Einkaufsliste summiert den Bedarf aller offenen Wartungen, deren
  **nächster Termin** bis zum Ende des Planungshorizonts liegt, einschließlich
  überfälliger Wartungen. Aufgaben ohne Termin und spätere Wiederholungen
  innerhalb desselben Horizonts werden nicht zusätzlich berücksichtigt.
- Zielbestand ist der größere Wert aus Mindestbestand und diesem Bedarf.
  Die Einkaufsmenge ist Zielbestand minus vorhandener Bestand, mindestens 0.
  Der Mindestbestand ist damit eine alternative Untergrenze, keine zusätzlich
  zum geplanten Verbrauch reservierte Menge.
- Material wird erst beim Erledigen verbraucht, nicht beim Planen reserviert.
- „Wieder öffnen“ macht den Verbrauch nicht rückgängig. Falls das Abhaken
  versehentlich war, den Bestand zusätzlich korrigieren.
- Ein gelöschtes Material hinterlässt zunächst einen nicht verfügbaren
  Bestandssensor; dessen Entität kann in den Einstellungen entfernt werden.

Die Übersicht aktualisiert sich auch beim lokalen Tageswechsel. Historische
Erledigungen können über `get_plan` abgerufen werden; sie erscheinen nicht als
eigene Historienansicht in dieser Version.

## Automationen

Die tatsächlichen Entitätsnamen richten sich nach dem gewählten Namen und der
Home-Assistant-Sprache. Die Beispiele verwenden exemplarische Entitätsnamen.

Bestand nach einem Einkauf um zwei Stück erhöhen:

```yaml
action: maintenance_planner.adjust_stock
data:
  material_id: HIER_DIE_MATERIAL_ID
  quantity: 2
```

Alle IDs, Bestände und Wartungen abrufen (in den Entwicklerwerkzeugen oder
einem Skript mit Antwortvariable):

```yaml
action: maintenance_planner.get_plan
response_variable: plan
```

Wartung aus einem Skript erledigen:

```yaml
action: maintenance_planner.complete_task
data:
  task_id: HIER_DIE_WARTUNGS_ID
  expected_generation: 0
```

`expected_generation` ist die Vorgangsnummer aus `get_plan`. Für wiederholbare
Automationen diese Nummer aus dem zuvor abgerufenen Plan übernehmen. Ein
erneuter Aufruf mit derselben Nummer bucht kein zweites Mal. Ohne Nummer gilt
jeder Aufruf bei einer wiederkehrenden Wartung als erneute Durchführung.
Die native Aufgabenliste übergibt die Vorgangsnummer automatisch.

Benachrichtigung, wenn Wartungen fällig sind, täglich um 08:00 Uhr:

```yaml
alias: An fällige Wartungen erinnern
triggers:
  - trigger: time
    at: "08:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.wartungsplaner_fallige_wartungen
    above: 0
actions:
  - action: persistent_notification.create
    data:
      title: Wartungen fällig
      message: In deiner Wartungsliste stehen fällige Aufgaben.
```

Nach erfolgreicher Erledigung wird außerdem das Ereignis
`maintenance_planner_completed` mit `task_id`, `name`, `completed_on` und
`next_due` ausgelöst. Benachrichtigungen sind bewusst über eigene
Home-Assistant-Automationen frei wählbar.

Weitere Aktionen: `upsert_material`, `remove_material`, `upsert_task`,
`remove_task` und `reopen_task`. `upsert_task.requirements` ersetzt die gesamte
Materialzuordnung durch eine Zuordnung von Material-IDs zu Mengen.
Die beiden `upsert`-Aktionen geben bei angeforderter Antwort die erzeugte ID
zurück.

Wartung mit Entitätsregel über eine Aktion anlegen:

```yaml
action: maintenance_planner.upsert_task
data:
  name: Salz nachfüllen
  trigger:
    entity_id: binary_sensor.bwt_perla_duplex_salzvorrat_leer
    operator: equals
    value: "on"
```

Optional `interval_weeks: 8` hinzufügen, um zusätzlich einen Rhythmus von acht
Wochen zu verwenden. Pro Wartung darf nur eines der Aktionsfelder
`interval_days`, `interval_weeks` oder `interval_months` größer als 0 sein.
Bei einem Wechsel des Rhythmus bisherige Intervallfelder explizit auf 0 setzen.
`trigger: null` entfernt eine Regel. Ohne `trigger` in einem Update bleibt die
bestehende Regel erhalten.

## Speicherung und Sicherung

Die Daten liegen in `.storage/maintenance_planner.<entry_id>` und werden mit
dem Home-Assistant-Konfigurationsverzeichnis gesichert. Schreibvorgänge
ersetzen die Datei atomar. Fehler werden an den Aufrufer gemeldet; der
veröffentlichte Bestand wird erst nach erfolgreichem Schreiben aktualisiert.
Ungültige gespeicherte Daten werden beim Start nicht durch einen leeren Plan
ersetzt.

**Das Entfernen der Integration löscht ihren Plan samt Materialbeständen und
Historie.** Ein Neustart oder Neuladen erhält die Daten.

## Entwicklung

Benötigt eine zur eingesetzten Home-Assistant-Version passende Python-Version.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Die reinen Modelltests lassen sich auch ohne Home Assistant ausführen:

```sh
python3 -m unittest discover -s tests -p test_models.py
```

## Lizenz

[MIT](LICENSE)
