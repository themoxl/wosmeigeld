"""Startet die API nativ (ohne Docker) fuer die lokale Entwicklung.

Warum es dieses Modul gibt: psycopg (async) kann auf Windows nicht mit dem
Default-Event-Loop (ProactorEventLoop) arbeiten - jede DB-Abfrage scheitert
sonst mit "psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'
to run in async mode". Die noetige WindowsSelectorEventLoopPolicy muss
gesetzt sein, BEVOR der erste Event-Loop entsteht. Der Guard in app/main.py
kann das unter "python -m uvicorn app.main:app" nicht leisten: uvicorn
erzeugt seinen Loop (Server.run -> asyncio.run), bevor es die Anwendung
importiert.

Gemessen mit uvicorn 0.34 (Verhalten, auf das man sich nicht verlassen
sollte): mit "--reload" laeuft der CLI-Weg zufaellig richtig, weil uvicorn in
uvicorn/loops/asyncio.py fuer den Subprozess-Modus selbst die
WindowsSelectorEventLoopPolicy setzt. Ohne "--reload" passiert das nicht und
der Systemstatus meldet die Datenbank als "down". Dieses Modul setzt die
Policy deshalb in jedem Fall selbst.

Zweiter Punkt: pydantic-settings sucht die .env relativ zum
Arbeitsverzeichnis (app/core/config.py: env_file=".env"). Die einzige .env
des Projekts liegt im Repo-Root; ein Start aus services/api heraus faende sie
nicht und fiele still auf die Docker-Defaults zurueck (Hostnamen "postgres",
"redis"). Dieses Modul wechselt daher ins Repo-Root und legt services/api auf
den sys.path, damit "app.main:app" trotzdem importierbar bleibt.

Aufruf (siehe services/api/README.md):

    .venv\\Scripts\\python.exe run_dev.py
    .venv\\Scripts\\python.exe run_dev.py --port 8010 --no-reload
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import uvicorn

SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parents[1]

# Der Import von uvicorn oben erzeugt noch keinen Loop - das tut erst
# uvicorn.run() in main(). Diese Zeile laeuft also frueh genug.
# Wichtig auch fuer den Reload-Modus: uvicorn startet den eigentlichen
# Serverprozess per multiprocessing("spawn"), der Kindprozess importiert
# dieses Modul dabei erneut (als "__mp_main__") und setzt die Policy damit
# ebenfalls, bevor dort ein Loop entsteht.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

os.chdir(REPO_ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finanzplaner-API nativ starten (Entwicklung, ohne Docker)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind-Adresse (Default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8882, help="Port (Default: 8882)")
    parser.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        help="Auto-Reload abschalten (Default: eingeschaltet)",
    )
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # Ohne diese Einschraenkung wuerde der Reloader das Arbeitsverzeichnis
        # ueberwachen - das ist hier das Repo-Root samt node_modules und .venv.
        reload_dirs=[str(SERVICE_DIR / "app")],
    )


if __name__ == "__main__":
    main()
