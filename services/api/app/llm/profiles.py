"""Herstellernahe Sampling-Profile fuer gaengige lokale Sprachmodelle.

Die Profile sind bewusst Daten und keine versteckten Heuristiken: Sie werden
ueber den Modellkatalog an die Oberflaeche geliefert und nur verwendet, wenn
der Haushalt den Sampling-Modus ``model`` gewaehlt hat. Bei unbekannten
Modellen wird kein Sampling-Parameter gesendet; dann gilt unveraendert die
Konfiguration des Modells bzw. Servers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

ReasoningMode = Literal["thinking", "non-thinking", "any"]


@dataclass(frozen=True)
class SamplingProfile:
    id: str
    label: str
    model_examples: tuple[str, ...]
    name_markers: tuple[str, ...]
    reasoning_mode: ReasoningMode
    temperature: float | None
    top_p: float | None
    top_k: int | None
    repeat_penalty: float | None
    note: str
    source_url: str
    min_p: float | None = None
    presence_penalty: float | None = None

    def parameters(self) -> dict[str, float | int]:
        return {
            name: value
            for name, value in (
                ("temperature", self.temperature),
                ("top_p", self.top_p),
                ("top_k", self.top_k),
                ("repeat_penalty", self.repeat_penalty),
                ("min_p", self.min_p),
                ("presence_penalty", self.presence_penalty),
            )
            if value is not None
        }


# Reihenfolge ist relevant: spezifische Varianten muessen vor ihrer Familie
# stehen. Werte ohne Herstellerangabe bleiben None und werden nicht erfunden.
SAMPLING_PROFILES: Final[tuple[SamplingProfile, ...]] = (
    SamplingProfile(
        id="gemma-4",
        label="Gemma 4 (4B/12B/26B)",
        model_examples=("Gemma 4 12B", "Gemma 4 26B A4B"),
        name_markers=("gemma-4", "gemma4"),
        reasoning_mode="any",
        temperature=1.0,
        top_p=0.95,
        top_k=64,
        repeat_penalty=None,
        note=(
            "Google veroeffentlicht diese Sampling-Werte fuer Gemma 4. Eine sehr "
            "niedrige Temperatur wie 0,1 ist nicht das Modellprofil und kann bei "
            "Reasoning monotone Wiederholungen beguenstigen."
        ),
        source_url="https://huggingface.co/google/gemma-4-26B-A4B/blob/main/generation_config.json",
    ),
    SamplingProfile(
        id="qwen3.6-thinking",
        label="Qwen3.6 35B A3B – Thinking",
        model_examples=("Qwen3.6-35B-A3B",),
        name_markers=("qwen3.6", "qwen3_6", "qwen3-6"),
        reasoning_mode="thinking",
        temperature=1.0,
        top_p=0.95,
        top_k=20,
        repeat_penalty=1.0,
        note="Qwens Empfehlung fuer Thinking-Aufgaben; Wiederholungsstrafe bleibt neutral.",
        source_url="https://huggingface.co/Qwen/Qwen3.6-35B-A3B",
        min_p=0.0,
        presence_penalty=1.5,
    ),
    SamplingProfile(
        id="qwen3.6-non-thinking",
        label="Qwen3.6 35B A3B – ohne Thinking",
        model_examples=("Qwen3.6-35B-A3B",),
        name_markers=("qwen3.6", "qwen3_6", "qwen3-6"),
        reasoning_mode="non-thinking",
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repeat_penalty=1.0,
        note="Qwens Empfehlung, wenn Thinking ausgeschaltet ist.",
        source_url="https://huggingface.co/Qwen/Qwen3.6-35B-A3B",
        min_p=0.0,
        presence_penalty=1.5,
    ),
    SamplingProfile(
        id="qwen3-instruct-2507",
        label="Qwen3 30B A3B Instruct 2507",
        model_examples=("Qwen3-30B-A3B-Instruct-2507",),
        name_markers=("qwen3-30b-a3b-instruct-2507",),
        reasoning_mode="any",
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repeat_penalty=None,
        note="Nicht-denkende Instruct-Variante mit den veroeffentlichten Qwen-Werten.",
        source_url="https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
        min_p=0.0,
    ),
    SamplingProfile(
        id="qwen3-thinking",
        label="Qwen3 (8B/14B/30B/32B) – Thinking",
        model_examples=("Qwen3 8B", "Qwen3 30B A3B", "Qwen3 32B"),
        name_markers=("qwen3",),
        reasoning_mode="thinking",
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        repeat_penalty=None,
        note="Qwens Empfehlung fuer den Thinking-Modus der Qwen3-Familie.",
        source_url="https://huggingface.co/Qwen/Qwen3-30B-A3B",
        min_p=0.0,
    ),
    SamplingProfile(
        id="qwen3-non-thinking",
        label="Qwen3 (8B/14B/30B/32B) – ohne Thinking",
        model_examples=("Qwen3 8B", "Qwen3 30B A3B", "Qwen3 32B"),
        name_markers=("qwen3",),
        reasoning_mode="non-thinking",
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repeat_penalty=None,
        note="Qwens Empfehlung fuer den ausgeschalteten Thinking-Modus.",
        source_url="https://huggingface.co/Qwen/Qwen3-30B-A3B",
        min_p=0.0,
    ),
    SamplingProfile(
        id="deepseek-r1-distill-qwen-32b",
        label="DeepSeek R1 Distill Qwen 32B",
        model_examples=("DeepSeek-R1-Distill-Qwen-32B",),
        name_markers=("deepseek-r1-distill-qwen-32b",),
        reasoning_mode="any",
        temperature=0.6,
        top_p=0.95,
        top_k=None,
        repeat_penalty=None,
        note="DeepSeek empfiehlt Temperatur 0,5 bis 0,7; verwendet wird der Mittelwert 0,6.",
        source_url="https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
    ),
    SamplingProfile(
        id="mistral-small-3.2",
        label="Mistral Small 3.2 24B",
        model_examples=("Mistral-Small-3.2-24B-Instruct-2506",),
        name_markers=("mistral-small-3.2", "mistral-small-3_2"),
        reasoning_mode="any",
        temperature=0.15,
        top_p=None,
        top_k=None,
        repeat_penalty=None,
        note="Mistral empfiehlt 0,15 fuer diese Instruct-Version, auch gegen Wiederholungen.",
        source_url="https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    ),
    SamplingProfile(
        id="phi-4-reasoning",
        label="Phi-4 Reasoning 14B/15B",
        model_examples=("Phi-4-reasoning", "Phi-4-reasoning-vision-15B"),
        name_markers=("phi-4-reasoning", "phi4-reasoning"),
        reasoning_mode="any",
        temperature=0.8,
        top_p=0.95,
        top_k=None,
        repeat_penalty=None,
        note="Werte aus Microsofts veroeffentlichter Generation-Konfiguration.",
        source_url="https://huggingface.co/microsoft/Phi-4-reasoning-vision-15B/blob/main/generation_config.json",
    ),
    SamplingProfile(
        id="gpt-oss-20b",
        label="OpenAI gpt-oss-20b",
        model_examples=("gpt-oss-20b",),
        name_markers=("gpt-oss-20b",),
        reasoning_mode="any",
        temperature=None,
        top_p=None,
        top_k=None,
        repeat_penalty=None,
        note=(
            "Die offizielle Generation-Konfiguration aktiviert Sampling, nennt aber "
            "keine Zahlen. Deshalb bleiben die Werte ehrlich beim Modell-/Serverstandard."
        ),
        source_url="https://huggingface.co/openai/gpt-oss-20b/blob/main/generation_config.json",
    ),
    SamplingProfile(
        id="granite-4-h",
        label="IBM Granite 4 H Small/Tiny",
        model_examples=("Granite-4.0-H-Small", "Granite-4.0-H-Tiny"),
        name_markers=("granite-4.0-h", "granite-4-h"),
        reasoning_mode="any",
        temperature=None,
        top_p=None,
        top_k=None,
        repeat_penalty=None,
        note=(
            "IBM veroeffentlicht fuer diese Modelle keine festen Sampling-Zahlen; "
            "der Serverstandard bleibt unangetastet."
        ),
        source_url="https://huggingface.co/ibm-granite/granite-4.0-h-small",
    ),
)


def matching_profile(model_id: str, reasoning: str | None) -> SamplingProfile | None:
    """Passendes Profil fuer Modellname und gewaehlten Reasoning-Modus."""
    lowered = model_id.lower()
    wants_thinking = reasoning != "off"
    for profile in SAMPLING_PROFILES:
        if not any(marker in lowered for marker in profile.name_markers):
            continue
        if profile.reasoning_mode == "thinking" and not wants_thinking:
            continue
        if profile.reasoning_mode == "non-thinking" and wants_thinking:
            continue
        return profile
    return None
