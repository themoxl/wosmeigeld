# test-fixtures

Ausschliesslich anonymisierte Importdateien (CSV, XLSX, MT940, CAMT, PayPal).

**Keine produktiven Bankdaten** in diesem Package - siehe Architekturplan
Abschnitt 5, Punkt 10 und Abschnitt 15 ("Tests und Qualitaetsstrategie").

Wird ab Phase 3 (Importplattform) befuellt: Golden Files je Format und Bank,
mehrere CAMT-Namespace-Versionen, MT940 mit Soll/Haben/Storno/mehrzeiligem
`:86:`, verschiedene Encodings. Als Referenz fuer die Anonymisierung dienen
echte Auszuege ausserhalb dieses Repositorys - jedoch niemals unveraendert
uebernommen.
