# Sicherheit

## Was dieses Projekt ist

wosmeigeld ist ein selbst gehosteter Haushaltsfinanzplaner. Es gibt keine
zentrale Instanz, keinen Dienst, bei dem man sich anmeldet, und keine Server
unter unserer Kontrolle. Jede Installation gehoert der Person, die sie
betreibt - und die Daten darin sind Kontostaende, Vertraege, Einkommen und
Gesundheitsausgaben eines Haushalts.

Ein Fehler hier trifft also niemanden ausser den Betreiber selbst. Genau
deshalb ist er trotzdem ernst: Es gibt keinen Anbieter, der im Hintergrund
etwas gerade zieht.

## Eine Luecke melden

**Bitte kein oeffentliches Issue.** Der Weg ist die private
Schwachstellenmeldung von GitHub:

> Reiter **Security** > **Report a vulnerability**

Damit bleibt die Meldung sichtbar nur fuer die Projektbetreuung, bis eine
Korrektur vorliegt.

Hilfreich in der Meldung:

- betroffene Datei oder Endpunkt, moeglichst mit Zeilenangabe
- was ein Angreifer damit erreicht - konkrete Eingabe, konkretes Ergebnis
- ob die Instanz dafuer aus dem Internet erreichbar sein muss oder ob
  Mitgliedschaft im selben Haushalt genuegt
- Version bzw. Commit

Dies ist ein Freizeitprojekt einer einzelnen Person. Eine erste Antwort
kommt in der Regel innerhalb von zwei Wochen. Ein Bug-Bounty gibt es nicht.

## Unterstuetzte Staende

Nur der aktuelle Stand von `main`. Es gibt keine gepflegten aelteren Zweige
und keine Rueckportierung von Korrekturen.

## Im Rahmen

- Haushaltsuebergreifender Datenzugriff. Die Trennung laeuft ueber
  PostgreSQL Row Level Security plus Rollenpruefung in der API; beides zu
  umgehen ist die schwerwiegendste Fehlerklasse hier.
- Umgehung der Authentifizierung oder der Rollen (`viewer` schreibt,
  `member` verwaltet Mitglieder).
- Einschleusen von SQL oder Kommandos, auch ueber Importwege
  (Kontoauszuege, Excel-Preislisten, Dokumentenextraktion).
- Preisgabe von Zugangsdaten oder Tokens ueber Logs, Fehlermeldungen oder
  API-Antworten.
- Schwachstellen in den Auslieferungsartefakten: Docker-Images,
  Build-Kontext, Expo-Builds.

## Ausserhalb

- Angriffe, die volle Kontrolle ueber den Rechner des Betreibers
  voraussetzen. Wer die Datenbank ohnehin lesen kann, braucht keine Luecke.
- Der Betrieb mit den Demo-Zugangsdaten aus `.env.example`. Sie erzeugt
  `supabase start` fuer jedes lokale Projekt identisch und sie sind
  oeffentlich dokumentiert. Die API bricht deshalb ausserhalb von
  `ENVIRONMENT=development` beim Start ab, wenn sie noch gesetzt sind
  (siehe `services/api/app/core/config.py`). Ein Betrieb, der diesen Abbruch
  umgeht, ist kein Fund.
- Fehlende Sicherheitsheader oder Ratenbegrenzung an einer Instanz, die nur
  an `localhost` gebunden ist.
- Meldungen, die ausschliesslich aus der Ausgabe eines Scanners bestehen,
  ohne dass ein Weg zum Schaden gezeigt wird.

## Wenn du selbst betreibst

Drei Dinge, an denen es erfahrungsgemaess haengt:

1. **Eigene Supabase-Zugangsdaten.** Wer das JWT-Secret kennt, kann sich ein
   gueltiges Token fuer eine beliebige Nutzer-ID selbst unterschreiben. Die
   Werte aus `.env.example` kennt jeder.
2. **Nicht ungeschuetzt ins Internet.** Vorgesehen ist der Betrieb im
   eigenen Netz oder ueber ein privates Overlay-Netz (Tailscale o. ae.),
   nicht ein offener Port am Router.
3. **Sicherungen liegen im Klartext.** Datenbankabzuege und die Ordner
   `.backups/` und `db-snapshots/` enthalten alles ungeschuetzt. Sie gehoeren
   nicht in ein Repository und nicht in einen Docker-Build-Kontext - beides
   ist ueber `.gitignore` und `.dockerignore` abgedeckt, aber nur solange
   diese Dateien gepflegt bleiben.
