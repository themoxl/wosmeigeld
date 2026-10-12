"""Startet den nativen Entwicklungsstapel in EINER Konsole und ueberwacht ihn.

Ersetzt das Hantieren mit mehreren PowerShell-Fenstern:

    python scripts/dev.py                 # alles: API (:8882), Web (:5173), Worker
    python scripts/dev.py --no-worker     # nur API und Web
    python scripts/dev.py --kill-stale    # belegte Ports vorher freiraeumen
    python scripts/dev.py --quiet         # ohne uvicorn-Zugriffslog

Der Worker gehoert zum Default, weil die Anwendung ohne ihn nur scheinbar
laeuft: Importe und KI-Laeufe bleiben in der Warteschlange liegen, das
Importzentrum meldet "Der Hintergrunddienst antwortet nicht" und die
Ursache steht nirgends. Ein halber Stapel ist kein brauchbarer Stapel -
fehlt eine Voraussetzung des Workers, laesst dieses Modul ihn ausdruecklich
und sichtbar weg, statt ihn stillschweigend nie gestartet zu haben.

Warum es dieses Modul gibt - drei Dinge gehen beim Handstart regelmaessig
schief:

1. Ist :5173 noch von einem verwaisten Vite belegt, weicht Vite still auf
   :5174 aus. Die API laesst per API_CORS_ORIGINS aber nur :5173 zu, also
   scheitert schon der CORS-Preflight (OPTIONS /v1/me -> 400) und die
   Oberflaeche meldet nur "Haushaltsdaten konnten nicht geladen werden".
   Supabase kennt in supabase/config.toml ebenfalls nur :5173. Dieses Modul
   prueft die Ports daher VOR dem Start und startet Vite mit --strictPort,
   damit ein belegter Port ein lauter Fehler ist statt einer stillen
   Fehlkonfiguration.
2. Vier Adressen in der .env sind auf den Docker-Betrieb gemuenzt und beim
   nativen Start falsch: REDIS_URL zeigt auf den Compose-Hostnamen "redis",
   DATABASE_URL, SUPABASE_URL und LM_STUDIO_BASE_URL auf
   "host.docker.internal". Dieses Modul biegt alle vier um.

   "redis" loest schlicht nicht auf - die API reiht dann nichts ein und
   KI-Laeufe bleiben auf "pending" stehen. "host.docker.internal" ist der
   unangenehmere Fall, weil er NICHT scheitert, sondern in die Irre fuehrt:
   Docker Desktop traegt ihn mit der aktuellen LAN-Adresse des Rechners in
   die hosts-Datei ein. Im gewohnten Netz stimmt die zufaellig und alles
   laeuft; nach einem Netzwechsel (anderes WLAN, Hotspot) zeigt sie auf ein
   fremdes Geraet oder ins Nichts. Statt eines klaren Fehlers gibt es dann
   pro Verbindungsversuch einen mehrsekuendigen TCP-Timeout, und die
   Symptome sehen nach allem Moeglichen aus, nur nicht nach einer falschen
   Adresse:
     * Anmelden endet in 401 "invalid_token" - nicht weil das Token schlecht
       waere, sondern weil die API den Signaturschluessel nicht laden kann.
       Seit Supabase Auth asymmetrisch signiert (ES256), holt sie ihn zur
       Laufzeit vom JWKS-Endpunkt unter SUPABASE_URL
       (services/api/app/auth/jwt.py). Erneutes Anmelden hilft deshalb nie.
     * Seiten haengen ewig auf "Lade Haushalt..." - die Anfrage kommt an, die
       API wartet aber auf eine Datenbank unter DATABASE_URL, die es dort
       nicht gibt.
     * KI-Aufrufe warten erst in einen Timeout, bevor die Kandidatenliste in
       classifier.py auf 127.0.0.1 ausweicht.
3. Beim Abbruch bleiben unter Windows Kindprozesse zurueck. Popen sieht nur
   den direkten Kindprozess, und das ist hier nie der Dienst selbst: der
   Venv-Starter (.venv/Scripts/python.exe) startet den echten Interpreter als
   eigenen Prozess, uvicorn haengt im Reload-Modus noch seinen Serverprozess
   an, pnpm haengt an cmd.exe/node. Stirbt der Starter zuerst, gilt der Dienst
   faelschlich als beendet und der Rest laeuft weiter - genau daraus entsteht
   der verwaiste Port aus Punkt 1. Dieses Modul steckt jeden Dienst deshalb in
   ein Jobobjekt, das den gesamten Baum mitnimmt, und prueft nach dem Stoppen
   nach, ob die Ports wirklich frei sind.

Das Modul braucht nur die Standardbibliothek; die Dienste laufen weiterhin in
ihren eigenen Venvs (services/api/.venv, services/worker/.venv).
"""

from __future__ import annotations

import argparse
import ctypes
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"

# Muss zu API_CORS_ORIGINS (services/api/app/core/config.py) und zu
# supabase/config.toml passen - siehe Punkt 1 im Modul-Docstring.
WEB_PORT = 5173
API_PORT = 8882
REDIS_PORT = 6379
SUPABASE_DB_PORT = 55322
# Kong (Supabase-API-Gateway) - unter dieser Adresse haengen Auth und der
# JWKS-Endpunkt, den die API zur Token-Pruefung braucht.
SUPABASE_API_PORT = 55321

# Der Docker-Hostname "redis" aus der .env loest auf dem Host nicht auf.
HOST_REDIS_URL = "redis://localhost:6379/0"

# "host.docker.internal" ist der zweite, deutlich tueckischere Fall - siehe
# Punkt 2 im Modul-Docstring. Der Name loest auf dem Host durchaus auf, aber
# auf die LAN-Adresse, die Docker Desktop beim letzten Netzwechsel in die
# hosts-Datei geschrieben hat (z. B. 192.168.1.23). Im eigenen WLAN ist das
# zufaellig richtig und alles laeuft; in einem fremden Netz gehoert die
# Adresse einem anderen Geraet oder niemandem. Dann gibt es keinen sauberen
# Fehler, sondern einen mehrsekuendigen TCP-Timeout pro Verbindungsversuch:
# Anmelden schlaegt mit 401 fehl (JWKS unerreichbar), Seiten haengen ewig im
# Ladezustand (Datenbank unerreichbar), KI-Aufrufe warten erst ins Leere.
# Ersetzt wird deshalb hier und nicht in der .env - fuer den Docker-Weg ist
# der Name ja richtig.
#
# Bewusst 127.0.0.1 statt "localhost": loest der Name zuerst nach ::1 auf,
# scheitert die Verbindung erneut, weil die Container-Ports nur auf IPv4
# gebunden sind.
DOCKER_HOST_ALIAS = "host.docker.internal"
LOOPBACK = "127.0.0.1"

# Genau die .env-Werte, die diesen Hostnamen tragen koennen. Bewusst eine
# feste Liste statt "alles, was danach aussieht": ein stiller Umbau fremder
# Variablen waere schwerer zu durchschauen als das Problem, das er loest.
DOCKER_HOST_ENV_KEYS = ("DATABASE_URL", "SUPABASE_URL", "LM_STUDIO_BASE_URL")


def dotenv_value(key: str) -> str | None:
    """Rohwert einer Variablen aus der .env im Repo-Root.

    Bewusst ein Dreizeiler statt python-dotenv: dieses Modul kommt mit der
    Standardbibliothek aus (siehe Modul-Docstring), und gebraucht wird nur
    genau das - der unveraenderte Wert, ohne Interpolation."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    return None


def docker_host_overrides() -> dict[str, str]:
    """Alle betroffenen .env-Werte mit dem Loopback statt des Docker-Alias.

    Leitet die Werte ab, statt sie festzuschreiben: Passwort, Port oder
    Datenbankname stehen in der .env und sollen dort auch bleiben - eine Kopie
    hier waere genau die Art von zweiter Wahrheit, die spaeter auseinanderlaeuft."""
    overrides: dict[str, str] = {}
    for key in DOCKER_HOST_ENV_KEYS:
        value = dotenv_value(key)
        if value and DOCKER_HOST_ALIAS in value:
            overrides[key] = value.replace(DOCKER_HOST_ALIAS, LOOPBACK)
    return overrides

# Beide Compose-Dateien, nicht nur die Basis: in docker-compose.yml hat redis
# BEWUSST kein Portmapping (im Docker-Weg reden die Dienste ueber das
# Compose-Netz miteinander). Erst das Overlay docker-compose.dev.yml legt
# "6379:6379" auf den Host - und nur darueber erreicht der nativ gestartete
# Worker den Broker. Ein blosses "docker compose up -d redis" startet einen
# gesunden, aber vom Host aus unerreichbaren Container; der Worker scheitert
# dann mit "Connection refused", obwohl `docker ps` alles als healthy meldet.
REDIS_START_HINT = "Starten: docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d redis"

RESET = "\033[0m"

# Win32-Konstanten fuer das Jobobjekt (siehe Punkt 3 im Modul-Docstring).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


@dataclass(frozen=True)
class Service:
    """Ein zu startender Dienst samt allem, was sein Start braucht."""

    name: str
    color: str
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    port: int | None


@dataclass
class Running:
    """Ein gestarteter Dienst - Prozess plus die Leser seiner Ausgabe."""

    service: Service
    proc: subprocess.Popen[bytes]
    readers: list[threading.Thread]


def venv_python(service_dir: Path) -> Path:
    return service_dir / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")


def build_services(args: argparse.Namespace) -> list[Service]:
    services: list[Service] = []
    # Einmal lesen, an beide Dienste geben: API und Worker sprechen dieselbe
    # Datenbank und dasselbe Supabase an.
    host_env = docker_host_overrides()

    if "api" in args.services:
        api_dir = REPO_ROOT / "services" / "api"
        argv = [
            str(venv_python(api_dir)),
            "run_dev.py",
            "--port",
            str(args.api_port),
            "--host",
            args.api_host,
        ]
        if not args.reload:
            argv.append("--no-reload")
        services.append(
            Service(
                name="api",
                color="\033[36m",  # cyan
                argv=argv,
                # run_dev.py wechselt selbst ins Repo-Root (wegen .env), will
                # aber aus services/api heraus gestartet werden.
                cwd=api_dir,
                env={"REDIS_URL": HOST_REDIS_URL, **host_env},
                port=args.api_port,
            )
        )

    if "web" in args.services:
        pnpm = shutil.which("pnpm") or "pnpm"
        services.append(
            Service(
                name="web",
                color="\033[35m",  # magenta
                # "exec vite" statt "run dev", damit --port/--strictPort
                # eindeutig bei Vite landen (so macht es playwright.config.ts).
                argv=[
                    pnpm,
                    "--filter",
                    "@finanzplaner/web",
                    "exec",
                    "vite",
                    "--port",
                    str(args.web_port),
                    "--strictPort",
                ],
                cwd=REPO_ROOT,
                env={},
                port=args.web_port,
            )
        )

    if "worker" in args.services:
        worker_dir = REPO_ROOT / "services" / "worker"
        # worker/config.py sucht die .env relativ zum Arbeitsverzeichnis, die
        # einzige .env liegt im Repo-Root. Der Importpfad kommt deshalb ueber
        # PYTHONPATH; services/api ist noetig, weil der Worker den KI-Kern
        # (app/llm) und weitere Motoren (Vertragsabgleich, Fristen, Vorschau)
        # aus services/api auf Modulebene importiert - wie im Container
        # (services/worker/Dockerfile) und unter pytest (pyproject.toml).
        pythonpath = os.pathsep.join(
            [str(worker_dir), str(REPO_ROOT / "services" / "api"), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        services.append(
            Service(
                name="worker",
                color="\033[33m",  # gelb
                argv=[
                    str(venv_python(worker_dir)),
                    "-m",
                    "dramatiq",
                    "worker.actors",
                    "--processes",
                    "1",
                    "--threads",
                    "4",
                ],
                cwd=REPO_ROOT,
                env={"REDIS_URL": HOST_REDIS_URL, **host_env, "PYTHONPATH": pythonpath},
                port=None,
            )
        )

    return services


def port_is_free(port: int) -> bool:
    """Frei heisst: weder ueber IPv4 noch ueber IPv6 antwortet jemand.

    Beide Familien pruefen ist hier nicht uebertrieben: Vite bindet an
    "localhost" und landet unter Windows auf ::1 - ein reiner
    127.0.0.1-Test haelt einen belegten Port faelschlich fuer frei und der
    Preflight winkt einen Start durch, den Vite danach abbricht.
    """
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.4)
                if sock.connect_ex((host, port)) == 0:
                    return False
        except OSError:
            continue
    return True


def pid_on_port(port: int) -> int | None:
    """PID des Prozesses, der auf dem Port lauscht (None, wenn keiner)."""
    try:
        if IS_WINDOWS:
            # Ohne "-p TCP": dieser Filter blendet die TCPv6-Eintraege aus,
            # und genau dort steht Vite ([::1]:5173).
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                # Nicht am Statuswort festmachen: das ist lokalisiert (auf
                # deutschem Windows "ABHOEREN" statt "LISTENING"). Die
                # Remoteadresse eines lauschenden Sockets ist es nicht.
                if (
                    len(parts) >= 5
                    and parts[0] == "TCP"
                    and parts[1].endswith(f":{port}")
                    and parts[2] in ("0.0.0.0:0", "[::]:0")
                ):
                    return int(parts[4])
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
            if out.strip():
                return int(out.split()[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def process_name(pid: int) -> str:
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
            ).stdout.strip()
            if out.startswith('"'):
                return out.split('","')[0].strip('"')
        else:
            return subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=15
            ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unbekannt"


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in ("Read", "Write", "Other", "ReadTx", "WriteTx", "OtherTx")
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def create_kill_on_close_job() -> int | None:
    """Jobobjekt, das alle zugewiesenen Prozesse mitnimmt, sobald es zugeht.

    Das ist die einzige Zusage unter Windows, die auch dann haelt, wenn dieser
    Prozess nichts mehr aufraeumen kann: bei "taskkill /F", beim Schliessen des
    Fensters oder wenn ein Dienst laenger braucht als das Stoppfenster. Das
    Handle bleibt bis zum Prozessende offen, das Betriebssystem schliesst es
    dann - und beendet damit den Rest des Baums.

    None bedeutet: kein Job verfuegbar (Nicht-Windows oder Win32-Aufruf
    gescheitert). Der Aufraeumpfad in stop_all traegt dann allein.
    """
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            ctypes.c_void_p(job),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            kernel32.CloseHandle(ctypes.c_void_p(job))
            return None
        return int(job)
    except (OSError, AttributeError):
        return None


def assign_to_job(job: int | None, pid: int) -> None:
    """Haengt einen Prozess samt kuenftiger Nachkommen in den Job.

    Kindprozesse erben die Jobzugehoerigkeit, deshalb genuegt der direkte
    Kindprozess - der Venv-Starter zieht seinen Interpreter mit hinein, uvicorn
    seinen Serverprozess, pnpm seine node-Prozesse.
    """
    if job is None:
        return
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            return
        kernel32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(handle))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except (OSError, AttributeError):
        pass


def process_command(pid: int) -> str:
    """Kommandozeile eines Prozesses - leer, wenn nicht ermittelbar.

    Der Name allein hilft im Fehlerfall nicht weiter: alles an diesem Stapel
    heisst "python.exe" oder "node.exe". Erst die Kommandozeile zeigt, ob da
    ein vergessener Entwicklungsstapel laeuft oder etwas voellig anderes.
    """
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=20,
            ).stdout
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def kill_tree(pid: int, *, force: bool) -> None:
    """Beendet einen Prozess samt Kindern (uvicorn-Reloader, pnpm -> node)."""
    try:
        if IS_WINDOWS:
            if force:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30
                )
            else:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def preflight(services: list[Service], *, kill_stale: bool) -> list[Service] | None:
    """Prueft Ports und Voraussetzungen.

    Rueckgabe sind die tatsaechlich startbaren Dienste, None heisst Abbruch.
    Der Worker wird dabei nur weggelassen, nie zum Abbruchgrund: ohne Redis
    oder ohne sein Venv wuerde er sofort sterben und nach der Regel unten den
    ganzen Stapel mitnehmen - dann liesse sich nicht einmal mehr an der
    Oberflaeche arbeiten. Fuer API und Web gilt das Gegenteil, sie sind der
    Stapel.
    """
    ok = True
    startbar = list(services)

    def ohne_worker(grund: str, hinweis: str) -> None:
        nonlocal startbar
        if not any(s.name == "worker" for s in startbar):
            return
        startbar = [s for s in startbar if s.name != "worker"]
        print(f"  WARNUNG Worker wird NICHT gestartet: {grund}")
        print(f"          {hinweis}")
        print("          Importe und KI-Laeufe bleiben so lange in der Warteschlange.")

    for service in services:
        fehlendes_venv = service.argv[0].endswith(("python.exe", "python")) and not Path(
            service.argv[0]
        ).exists()
        if fehlendes_venv and service.name == "worker":
            ohne_worker(
                f"Venv fehlt ({service.argv[0]}).",
                "Anlegen: cd services/worker && python -m venv .venv && "
                "pip install -r requirements.txt",
            )
            continue
        if fehlendes_venv:
            print(f"  FEHLER  {service.name}: Venv fehlt -> {service.argv[0]}")
            print("          Anlegen: python -m venv .venv && pip install -r requirements-dev.txt")
            ok = False
        if service.name == "web" and shutil.which("pnpm") is None:
            print("  FEHLER  web: pnpm nicht im PATH gefunden.")
            ok = False

    # Ohne Redis kein Broker: der Worker wuerde beim Start abbrechen.
    if port_is_free(REDIS_PORT):
        ohne_worker(
            f"Redis auf :{REDIS_PORT} nicht erreichbar.",
            REDIS_START_HINT,
        )

    for service in startbar:
        if service.port is None or port_is_free(service.port):
            continue
        pid = pid_on_port(service.port)
        wer = f"PID {pid} ({process_name(pid)})" if pid else "unbekannter Prozess"
        if kill_stale and pid:
            print(f"  Port {service.port} belegt von {wer} - wird beendet (--kill-stale).")
            kill_tree(pid, force=True)
            for _ in range(20):
                if port_is_free(service.port):
                    break
                time.sleep(0.25)
            if not port_is_free(service.port):
                print(f"  FEHLER  Port {service.port} ist weiterhin belegt.")
                ok = False
        else:
            print(f"  FEHLER  Port {service.port} ({service.name}) belegt von {wer}.")
            befehl = process_command(pid) if pid else ""
            if befehl:
                # In der Mitte kuerzen: vorn steht der Interpreterpfad, hinten
                # die Argumente - und erst die verraten, welcher Stapel das ist.
                if len(befehl) > 110:
                    befehl = f"{befehl[:55]} ... {befehl[-50:]}"
                print(f"          {befehl}")
            print("          Mit --kill-stale automatisch freiraeumen.")
            if service.name == "web":
                print("          Ein Ausweichport waere hier keine Loesung: API-CORS und")
                print("          Supabase-Redirects kennen ausschliesslich :5173.")
            ok = False

    # Weiche Abhaengigkeiten: fehlen sie, laeuft der Stapel an, aber Teile
    # davon bleiben stumm. Deshalb warnen statt abbrechen. Redis steht hier
    # nur fuer den Fall ohne Worker - mit Worker ist es oben schon Thema.
    if port_is_free(REDIS_PORT) and not any(s.name == "worker" for s in services):
        print(f"  WARNUNG Redis auf :{REDIS_PORT} nicht erreichbar - Einreihen schlaegt fehl.")
        print(f"          {REDIS_START_HINT}")
    if port_is_free(SUPABASE_DB_PORT):
        print(f"  WARNUNG Supabase auf :{SUPABASE_DB_PORT} nicht erreichbar - 'supabase start'?")

    return startbar if ok else None


def start(
    service: Service, sink: queue.Queue[tuple[Service, str | None]], job: int | None = None
) -> Running:
    env = {**os.environ, **service.env, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"}
    # Eigene Prozessgruppe: nur so laesst sich der Dienst spaeter gezielt
    # signalisieren, ohne die eigene Konsole mitzunehmen.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    proc = subprocess.Popen(
        service.argv,
        cwd=str(service.cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        bufsize=0,
        creationflags=flags,
        start_new_session=not IS_WINDOWS,
    )
    # So frueh wie moeglich: subprocess gibt uns den Prozess erst nach dem
    # Start heraus (kein CREATE_SUSPENDED), zwischen Start und Zuweisung liegt
    # also ein schmales Fenster. Deshalb bleibt die Nachkontrolle in stop_all
    # das zweite Netz.
    assign_to_job(job, proc.pid)

    def pump(stream: object) -> None:
        for raw in iter(stream.readline, b""):  # type: ignore[attr-defined]
            sink.put((service, raw.decode("utf-8", errors="replace").rstrip("\r\n")))
        sink.put((service, None))

    reader = threading.Thread(target=pump, args=(proc.stdout,), daemon=True)
    reader.start()
    return Running(service=service, proc=proc, readers=[reader])


def wait_ready(running: list[Running], timeout: float = 45.0) -> None:
    """Wartet, bis die Ports der Dienste antworten, und meldet den Zustand."""
    pending = {r.service.name: r.service.port for r in running if r.service.port}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for name, port in list(pending.items()):
            if not port_is_free(port):
                del pending[name]
        if pending:
            time.sleep(0.4)


def stop_all(running: list[Running]) -> None:
    """Sanft beenden, dann hart - inklusive aller Kindprozesse."""
    alive = [r for r in running if r.proc.poll() is None]
    for r in alive:
        kill_tree(r.proc.pid, force=False)
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and any(r.proc.poll() is None for r in alive):
        time.sleep(0.2)
    for r in alive:
        if r.proc.poll() is None:
            kill_tree(r.proc.pid, force=True)
    for r in alive:
        try:
            r.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f"  WARNUNG {r.service.name} (PID {r.proc.pid}) reagiert nicht.")

    # Nachkontrolle am Port statt am Prozesshandle: der beendete Kindprozess
    # ist unter Windows nur der Venv-Starter, der eigentliche Dienst haengt
    # eine Ebene tiefer (Modul-Docstring, Punkt 3). "Beendet" darf deshalb erst
    # heissen, was der Port bestaetigt - sonst meldet dieses Modul Vollzug und
    # der naechste Start scheitert an einem verwaisten uvicorn.
    for r in running:
        port = r.service.port
        if port is None or port_is_free(port):
            continue
        pid = pid_on_port(port)
        if pid is None:
            print(f"  WARNUNG {r.service.name}: Port {port} bleibt belegt (Prozess unbekannt).")
            continue
        print(f"  {r.service.name}: Port {port} noch belegt (PID {pid}) - wird hart beendet.")
        kill_tree(pid, force=True)
        for _ in range(20):
            if port_is_free(port):
                break
            time.sleep(0.25)
        if not port_is_free(port):
            print(f"  WARNUNG Port {port} bleibt belegt (PID {pid}) - bitte manuell pruefen.")


def install_stop_handler() -> None:
    """Auch SIGTERM/SIGBREAK in den Aufraeumpfad leiten.

    Strg+C liefert von sich aus einen KeyboardInterrupt. Ein "taskkill" ohne
    /F oder ein Strg+Untbr wuerde diesen Prozess dagegen ohne Handler sofort
    beenden - die Kindprozesse ueberlebten und blockierten ihre Ports weiter.
    """

    def _stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _stop)
        except (OSError, ValueError):
            pass


def enable_ansi() -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except OSError:
        pass


def is_access_log(line: str) -> bool:
    """uvicorn-Zugriffslog erkennen (eine Zeile je Anfrage, sehr laut)."""
    return line.startswith("INFO:") and ' - "' in line and "HTTP/1.1" in line


def main() -> int:
    # Vite schreibt "➜" in seinen Startbanner. Haengt die Ausgabe nicht an
    # einer UTF-8-faehigen Konsole, sondern an einer Pipe oder Datei, faellt
    # Python unter Windows auf cp1252 zurueck - und der UnicodeEncodeError in
    # der Log-Weitergabe (siehe unten) reisst den gesamten Dienststapel mit.
    # Ein unlesbares Zeichen darf niemals API, Web und Worker beenden.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Startet API, Web und Worker nativ in einer Konsole."
    )
    parser.add_argument(
        "--services",
        default="api,web,worker",
        help="Kommaliste aus api,web,worker (Default: alle drei)",
    )
    # Beide auf denselben Schalter, Default None: nur so bleibt "gar nichts
    # angegeben" von "ausdruecklich abgewaehlt" unterscheidbar und --services
    # behaelt das letzte Wort, wenn es allein gesetzt wird.
    parser.add_argument(
        "--no-worker",
        dest="worker",
        action="store_false",
        default=None,
        help="Ohne Dramatiq-Worker starten (Importe/KI-Laeufe bleiben dann liegen)",
    )
    parser.add_argument(
        "--worker",
        dest="worker",
        action="store_true",
        default=None,
        help=argparse.SUPPRESS,  # laeuft seit dem Default mit; nur noch Gewohnheit
    )
    parser.add_argument(
        "--kill-stale",
        action="store_true",
        help="Belegte Ports vor dem Start freiraeumen (beendet den Prozessbaum)",
    )
    parser.add_argument("--api-port", type=int, default=API_PORT)
    parser.add_argument("--web-port", type=int, default=WEB_PORT)
    parser.add_argument(
        "--api-host",
        default="127.0.0.1",
        help=(
            "Bind-Adresse der API (Default: 127.0.0.1, nur vom selben Rechner erreichbar). "
            "Fuer Tests auf einem echten Geraet im selben WLAN: 0.0.0.0 - sonst kommen "
            "Anfragen vom Handy an EXPO_PUBLIC_API_BASE_URL nie an, obwohl Supabase-Login "
            "(separater Dienst) bereits funktioniert."
        ),
    )
    parser.add_argument(
        "--no-reload", dest="reload", action="store_false", help="API ohne Auto-Reload"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="uvicorn-Zugriffslog ausblenden (eine Zeile je Anfrage)"
    )
    args = parser.parse_args()

    args.services = [s.strip() for s in args.services.split(",") if s.strip()]
    if args.worker is False:
        args.services = [s for s in args.services if s != "worker"]
    elif args.worker and "worker" not in args.services:
        args.services.append("worker")
    unbekannt = [s for s in args.services if s not in ("api", "web", "worker")]
    if unbekannt:
        parser.error(f"Unbekannte Dienste: {', '.join(unbekannt)}")

    enable_ansi()
    install_stop_handler()
    services = build_services(args)
    if not services:
        parser.error("Keine Dienste ausgewaehlt.")

    print(f"Finanzplaner-Entwicklungsstapel: {', '.join(s.name for s in services)}")
    startbar = preflight(services, kill_stale=args.kill_stale)
    if startbar is None:
        print("\nAbbruch - siehe Meldungen oben.")
        return 1
    services = startbar

    sink: queue.Queue[tuple[Service, str | None]] = queue.Queue()
    running: list[Running] = []
    breite = max(len(s.name) for s in services)
    exit_code = 0

    job = create_kill_on_close_job()

    try:
        for service in services:
            running.append(start(service, sink, job))
            print(f"  gestartet  {service.name} (PID {running[-1].proc.pid})")

        banner = threading.Thread(target=wait_ready, args=(running,), daemon=True)
        banner.start()
        namen = {s.name for s in services}
        if "web" in namen:
            print(f"\n  Web  http://localhost:{args.web_port}")
        if "api" in namen:
            print(f"  API  http://127.0.0.1:{args.api_port}/docs")
        print("  Strg+C beendet alle Dienste.\n")

        while True:
            try:
                # Mit Timeout statt blockierend: sonst kann der Interpreter
                # unter Windows ein eingehendes Signal erst verarbeiten, wenn
                # zufaellig die naechste Logzeile eintrifft.
                service, line = sink.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                r = next(r for r in running if r.service is service)
                code = r.proc.wait()
                farbe = "\033[31m" if code else ""
                print(f"{farbe}  {service.name} beendet (Exit {code}){RESET}")
                if code:
                    exit_code = 1
                # Der Stapel ist nur gemeinsam brauchbar: ein Web ohne API
                # zeigt bloss Ladefehler, und eine ueberlebende Haelfte haelt
                # ihren Port besetzt - daraus entsteht beim naechsten Start
                # der stille Ausweichport. Also alles zusammen beenden.
                if any(other.proc.poll() is None for other in running):
                    print("  Uebrige Dienste werden mitbeendet.")
                break
            if args.quiet and service.name == "api" and is_access_log(line):
                continue
            print(f"{service.color}{service.name:>{breite}}{RESET} | {line}", flush=True)
    except KeyboardInterrupt:
        print("\n  Beende Dienste ...")
    finally:
        stop_all(running)
        print("  Alle Dienste beendet.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
