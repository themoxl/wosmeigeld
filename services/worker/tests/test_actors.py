from worker.actors import ping


def test_ping_actor_runs_synchronously() -> None:
    # .fn ruft die reine Funktion direkt auf, ohne Broker/Redis-Verbindung -
    # ausreichend fuer einen Unit-Test des Phase-0-Referenzactors.
    ping.fn("hallo")
