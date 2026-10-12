# infra/backup

Verschluesselte Postgres-Backups plus ein automatisierter Restoretest, der
tatsaechlich beweist, dass ein Backup wiederhergestellt werden kann - nicht
nur, dass die Datei existiert (Architekturplan Abschnitt 15 "Betrieb" und
Abschnitt 17, Phase 10, Abnahmekriterium "Backup wurde tatsaechlich
wiederhergestellt").

Einzige harte Host-Abhaengigkeit ist **Docker** (ohnehin Grundvoraussetzung
fuer dieses Repo). `pg_dump`/`pg_restore`/`psql` laufen immer ueber
`docker run postgres:15-alpine ...` (exakte Versionsgleichheit mit dem im
Repo gepinnten Server-Image), Verschluesselung ueber ein selbst gebautes
Alpine-Image mit [`age`](https://github.com/FiloSottile/age)
(`Dockerfile.age`). Kein `pg_dump`/`age`-Install auf dem Host noetig.

## Dateien

| Datei                       | Zweck                                                                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `backup.sh`                 | Erstellt ein verschluesseltes Backup (nur `public`-Schema, Daten only) + Manifest, wendet Aufbewahrungsrichtlinie an                  |
| `restore_test.sh`           | **Der automatisierte Beweis**: restauriert in eine isolierte Wegwerfdatenbank und verifiziert Tabelle fuer Tabelle gegen das Manifest |
| `restore.sh`                | Manueller Disaster-Recovery-Restore in eine echte Zieldatenbank (destruktiv, erfordert `--yes`)                                       |
| `lib.sh`                    | Gemeinsame Hilfsfunktionen (wird von den drei Skripten oben eingebunden)                                                              |
| `diff_manifests.py`         | Vergleicht zwei Manifeste Tabelle fuer Tabelle (laeuft in einem Wegwerf-Container, keine Python-Abhaengigkeit auf dem Host)           |
| `restoretest/auth_stub.sql` | Minimaler Ersatz fuer Supabases `auth`-Schema, nur fuer den isolierten Restoretest                                                    |
| `Dockerfile.age`            | Baut das `finanzplaner-age`-Image (age/age-keygen)                                                                                    |

## Warum nur das `public`-Schema?

Das Backup umfasst ausschliesslich die fachlichen Anwendungsdaten
(vollstaendig in `supabase/migrations/0001_schema.sql` definiert,
`--schema=public --data-only`). Supabases eigene interne Schemata (`auth`,
`storage`, `realtime`, `vault`, `supabase_functions`/`_migrations`) sind
Supabases eigener Betriebsbereich - fuer self-hosted Supabase gilt dafuer
Supabases eigene, unabhaengige Backup-Strategie. Ein Restore-Ziel muss daher
**vor** dem Einspielen bereits das aktuelle Schema haben (z. B. per
`supabase db reset` bzw. Anwenden von `0001_schema.sql` gegen eine leere
Datenbank) - das gilt fuer `restore.sh` (manuell) genauso wie fuer
`restore_test.sh` (baut das Schema automatisch inkl. `auth`-Stub selbst auf).

## Schluesselverwaltung (age)

`age` ist asymmetrisch: `backup.sh` braucht nur den **oeffentlichen**
Recipient-Schluessel (`AGE_RECIPIENT`, Format `age1...`). Die private Identity
wird nur fuer `restore.sh`/`restore_test.sh` gebraucht und sollte offline /
getrennt vom Backup-Host aufbewahrt werden.

```bash
# Einmalig ein Schluesselpaar erzeugen (ueber das age-Image, kein
# Host-Install noetig):
docker build -t finanzplaner-age -f infra/backup/Dockerfile.age infra/backup
docker run --rm finanzplaner-age age-keygen > age-identity.txt
# Oeffentlichen Schluessel aus der Kommentarzeile "# public key: age1..." entnehmen.
```

`age-identity.txt` **niemals committen** (`.gitignore` schliesst
`age-identity*.txt`, `*.agekey` und `infra/backup/out/` bereits aus).

## Nutzung

```bash
# Backup erstellen:
DATABASE_URL=postgresql+psycopg://postgres:postgres@host.docker.internal:55322/postgres \
AGE_RECIPIENT=age1... \
./infra/backup/backup.sh
# -> infra/backup/out/finanzplaner_<timestamp>.tar.age

# Beweisen, dass es wiederhergestellt werden kann (isolierte Wegwerf-DB,
# ruehrt die Quelldatenbank nicht an):
AGE_IDENTITY_FILE=age-identity.txt ./infra/backup/restore_test.sh
# -> "RESTORE TEST PASSED" oder ein Tabellenbericht + Exitcode != 0

# Echten Disaster-Recovery-Restore einspielen (Zieldatenbank muss bereits
# das aktuelle Schema haben):
./infra/backup/restore.sh infra/backup/out/finanzplaner_<timestamp>.tar.age \
  --identity age-identity.txt \
  --target-url postgresql+psycopg://postgres:postgres@<ziel>:5432/postgres \
  --yes
```

Weitere Optionen: `backup.sh --out-dir DIR --retention N` (Standard: die
neuesten 14 Backups behalten), `restore_test.sh [<backup.tar.age>]` (ohne
Dateiargument: neuestes Backup in `BACKUP_OUT_DIR`). Fuer das
`local-db`-Compose-Profil (Postgres per Containername statt
`host.docker.internal` erreichbar) zusaetzlich `BACKUP_DOCKER_NETWORK=<compose-netzwerk>` setzen.

## Was der Restoretest tatsaechlich prueft

1. Entschluesselt das Backup in ein temporaeres Verzeichnis.
2. Startet eine **voellig isolierte** Wegwerf-Postgres-Instanz (zufaelliger
   Containername/Port).
3. Wendet `restoretest/auth_stub.sql` (die vier `auth.*`-Objekte, die
   `0001_schema.sql` tatsaechlich referenziert) und danach das **echte,
   aktuelle** `supabase/migrations/0001_schema.sql` an.
4. Spielt die Backup-Daten ein (`pg_restore --data-only --disable-triggers`).
5. Erzeugt ein Manifest der wiederhergestellten Datenbank (Zeilenzahl +
   ordnungsunabhaengige Pruefsumme je Tabelle) und vergleicht es Tabelle fuer
   Tabelle gegen das im Backup mitgelieferte Manifest.
6. Raeumt die Wegwerf-Instanz **immer** auf (auch bei Fehlern/Abbruch, per
   `trap`).

Eine fehlende/zusaetzliche Tabelle oder eine abweichende Zeilenzahl/Pruefsumme
lassen den Test mit einem klaren Tabellenbericht und Exitcode != 0
fehlschlagen - das ist kein Existenz-Check der Backup-Datei, sondern ein
echter Wiederherstellungsbeweis. Waehrend der Implementierung real gegen eine
isolierte Testdatenbank verifiziert: ein sauberer Durchlauf mit
uebereinstimmenden Daten meldet `RESTORE TEST PASSED`; ein absichtlich
inkonsistenter Zustand (bei der Verifikation live durch eine gleichzeitige
Aenderung der Quelldaten provoziert) wurde korrekt mit einem
Tabelle-fuer-Tabelle-Fehlerbericht und Exitcode 1 abgelehnt - der Vergleich
ist kein Rubberstamp.

Wird `0001_schema.sql` um eine neue `auth.*`-Abhaengigkeit erweitert (z. B.
`auth.jwt()`), schlaegt der Restoretest beim Anwenden des Schemas mit einer
klaren Postgres-Fehlermeldung fehl, bis `restoretest/auth_stub.sql`
entsprechend erweitert wird - das ist beabsichtigt, kein Bug.

## Planung (Scheduling)

Wird bewusst **nicht** ins Repo eingebaut (das ist Aufgabe des Betreibers) -
Beispiele:

**Privat/Home-Server (cron):**

```cron
0 3 * * * cd /opt/finanzplaner && AGE_RECIPIENT=age1... ./infra/backup/backup.sh >> /var/log/finanzplaner-backup.log 2>&1
0 4 * * 0 cd /opt/finanzplaner && AGE_IDENTITY_FILE=/etc/finanzplaner/age-identity.txt ./infra/backup/restore_test.sh >> /var/log/finanzplaner-restoretest.log 2>&1
```

(Restoretest woechentlich statt taeglich - er ist schwerer als das reine
Backup und muss nicht denselben Takt haben.)

**Produktiv/SaaS (systemd):**

```ini
# /etc/systemd/system/finanzplaner-backup.service
[Service]
Type=oneshot
Environment=AGE_RECIPIENT=age1...
WorkingDirectory=/opt/finanzplaner
ExecStart=/opt/finanzplaner/infra/backup/backup.sh
```

```ini
# /etc/systemd/system/finanzplaner-backup.timer
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
[Install]
WantedBy=timers.target
```

stdout/stderr landen automatisch in journald; auf einen Exitcode != 0
alarmieren (z. B. `OnFailure=` auf ein Alerting-Unit).

**Windows (Privat, sekundaer Fallback):** `schtasks /create` ruft das Skript
ueber Git Bash auf; primaeres Zielsystem fuer Produktivbetrieb ist Linux.

## Manuell in der echten Zielumgebung zu verifizieren

- Tatsaechliche Backup-Groesse/-Dauer gegen eine produktionsgrosse Datenbank
  (hier nur gegen eine kleine Testdatenbank verifiziert).
- Erreichbarkeit von `DATABASE_URL` von dem Host aus, der `backup.sh`
  tatsaechlich per Cron/systemd ausfuehrt (Netzwerk-/Firewall-Konfiguration).
- Sichere Aufbewahrung der privaten age-Identity ausserhalb des
  Backup-Hosts (organisatorische Massnahme, nicht Teil dieses Skripts).
