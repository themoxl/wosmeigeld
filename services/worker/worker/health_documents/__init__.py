"""Sichere Extraktion privater Gesundheitsdokumente.

`processor.py` importiert `app.*` auf Modulebene. services/api kommt dafuer
ueber PYTHONPATH (services/worker/Dockerfile, pyproject.toml fuer pytest,
scripts/dev.py) - frueher haengte dieser Paket-Init den Pfad selbst an
sys.path.
"""
