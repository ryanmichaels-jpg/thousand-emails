"""Contract test for the RealLLM adapter and the persona-fallback path, pinned against a recorded
Anthropic messages-API response (a non-English title the rules cannot place). No network, no SDK:
the recording is the contract."""
import json
import os

from api.adapters.real import RealLLM
from api.scoring import personas

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDED = os.path.join(HERE, "recorded", "llm_persona_fallback.json")


def _recorded() -> dict:
    with open(RECORDED) as f:
        return json.load(f)


def test_real_llm_parses_recorded_messages_response():
    assert RealLLM.text_from_response(_recorded()) == '{"persona": "TRC"}'


def test_persona_fallback_path_accepts_recorded_response(monkeypatch):
    class Recorded:
        def complete(self, system, user, *, model, max_tokens=800, json_schema=None):
            assert "Responsable" in user
            assert json_schema["properties"]["persona"]["enum"]
            return RealLLM.text_from_response(_recorded())

    monkeypatch.setattr(personas, "get_adapter", lambda name: Recorded())
    rules = personas.load_rules()
    persona, model = personas._llm_persona("Responsable Rémunération et Avantages", rules)
    assert persona == "TRC"
    assert model
