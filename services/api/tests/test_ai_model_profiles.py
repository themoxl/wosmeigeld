"""Herstellerprofile und ihre Auswahl ohne Netzwerk oder Datenbank."""

from app.services.ai_model_profiles import SAMPLING_PROFILES, matching_profile


def test_gemma_4_uses_published_sampling_values() -> None:
    profile = matching_profile("google/gemma-4-26b-a4b-qat", "on")

    assert profile is not None
    assert profile.id == "gemma-4"
    assert profile.parameters() == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


def test_qwen_profile_changes_with_reasoning_choice() -> None:
    thinking = matching_profile("Qwen/Qwen3-30B-A3B", "on")
    direct = matching_profile("Qwen/Qwen3-30B-A3B", "off")

    assert thinking is not None and direct is not None
    assert thinking.id == "qwen3-thinking"
    assert thinking.temperature == 0.6
    assert thinking.min_p == 0.0
    assert direct.id == "qwen3-non-thinking"
    assert direct.temperature == 0.7


def test_qwen36_profile_includes_anti_loop_presence_penalty() -> None:
    profile = matching_profile("qwen/qwen3.6-35b-a3b", "on")

    assert profile is not None
    assert profile.parameters()["presence_penalty"] == 1.5


def test_unknown_model_keeps_server_defaults() -> None:
    assert matching_profile("local/unknown-22b", None) is None


def test_public_guide_covers_several_popular_local_families() -> None:
    labels = {profile.label for profile in SAMPLING_PROFILES}

    assert len(labels) >= 8
    assert any("Gemma 4" in label for label in labels)
    assert any("Qwen3" in label for label in labels)
    assert any("DeepSeek" in label for label in labels)
    assert any("Mistral" in label for label in labels)
    assert any("Phi-4" in label for label in labels)
    assert any("gpt-oss" in label for label in labels)
