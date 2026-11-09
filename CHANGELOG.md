# Changelog

## 0.3.0

- Wählt Verbrauchsartikel und Mengen direkt beim Anlegen einer Wartung aus.
- Wählt eine Wartungsmeldung als Auslöser aus und filtert ihre Entitäten nach Gerät.
- Erinnert am Fälligkeitstag, eine Woche vorher oder bei einem separaten Entitätssignal mit aktuellen Materialmengen.
- Unterstützt neben Home-Assistant-Erinnerungen optional Companion-App-Push.

## 0.2.1

- Behebt den HTTP-500-Fehler beim Öffnen der Konfigurationsformulare.
- Zahlenfelder verwenden native Selektoren. Die Formulare prüfen beim Speichern,
  dass Planungshorizont und Wartungsrhythmus ganze Zahlen sind.
- Testet die Konfigurationsformulare über die HTTP-Endpunkte von Home Assistant 2026.11.0.

## 0.2.0

- Direkte Auswahl von Tagen, Wochen oder Monaten für Wiederholungen.
- Wartungen durch Entitätszustände, Attribute oder numerische Grenzen auslösen.
- Gespeicherte Auslösesperre verhindert erneute Wartungen bei dauerhaft aktiven
  Meldungen; ein gültiger Rücksetzwert aktiviert die Regel wieder.
- Beispiel für den BWT-Sensor „Salzvorrat leer“ und automatische Materialplanung.
- Bestehende Aufgaben und Materialbestände werden weiterverwendet.

## 0.1.0

- Erste lokale Version mit Wartungen, Wiederholungen und Materialbestand.
- Native Aufgabenlisten, Übersichtssensoren und Automationsaktionen.
- Verwaltung über deutsche und englische Home-Assistant-Dialoge.
- Geschützte Verbrauchsbuchungen und lokale Historie.
