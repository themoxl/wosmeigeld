"""Herstellernahe Sampling-Profile - liegen seit dem KI-Kern in app/llm/profiles.py.

Dieses Modul bleibt als stabiler Importpfad fuer die API (Modellkatalog,
Tests); API und Worker lesen dieselbe Tabelle aus dem Kern.
"""

from app.llm.profiles import SAMPLING_PROFILES, ReasoningMode, SamplingProfile, matching_profile

__all__ = ["SAMPLING_PROFILES", "ReasoningMode", "SamplingProfile", "matching_profile"]
