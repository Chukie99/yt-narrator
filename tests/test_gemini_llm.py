"""Gemini provider resilience: retries, model fallback, JSON parsing.

The free-tier Flash models return 503 under load, so a single failed call must
not take down a whole job. These tests fake the SDK client.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.providers import gemini_llm
from backend.providers.gemini_llm import GeminiLLM, _first_json_object


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    async def generate_content(self, model=None, contents=None, config=None):
        self.calls.append((model, contents))
        outcome = self.behaviour(model, len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, behaviour):
        inner = FakeModels(behaviour)
        self.aio = type("Aio", (), {"models": inner})()
        self.models = inner


def install_fake(monkeypatch, behaviour):
    client = FakeClient(behaviour)
    fake_genai = type("Genai", (), {"Client": staticmethod(lambda api_key=None: client)})
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google", type("Google", (), {"genai": fake_genai}))
    return client


# --- JSON parsing ---------------------------------------------------------

def test_parses_plain_json():
    assert _first_json_object('{"a": 1}') == {"a": 1}


def test_parses_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert _first_json_object(text) == {"a": 1}


def test_parses_json_wrapped_in_prose():
    text = 'Berikut hasilnya:\n{"a": 1}\n hope this helps!'
    assert _first_json_object(text) == {"a": 1}


def test_parses_json_containing_nested_braces():
    text = '{"style": {"inner": {"deep": 1}}, "n": 2}'
    assert _first_json_object(text) == {"style": {"inner": {"deep": 1}}, "n": 2}


def test_rejects_reply_with_no_json():
    with pytest.raises(ValueError, match="no JSON object"):
        _first_json_object("Maaf, saya tidak bisa membantu.")


# --- retry and fallback ---------------------------------------------------

@pytest.mark.asyncio
async def test_retries_then_succeeds_on_same_model(monkeypatch):
    """A transient 503 must be retried, not surfaced to the job."""
    def behaviour(model, n):
        if n < 3:
            return RuntimeError("503 UNAVAILABLE: high demand")
        return '{"full_narasi": "ok", "scenes": [{"narration_text":"a"}]}'

    client = install_fake(monkeypatch, behaviour)
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=3)
    monkeypatch.setattr(gemini_llm.asyncio, "sleep", _no_sleep)

    out = await llm.generate_narasi("topic")
    assert out["full_narasi"] == "ok"
    assert [c[0] for c in client.models.calls] == ["m1", "m1", "m1"]


@pytest.mark.asyncio
async def test_falls_back_to_another_model(monkeypatch):
    """When one model is down for good, another must still be tried."""
    def behaviour(model, n):
        if model == "m1":
            return RuntimeError("503 UNAVAILABLE")
        return json.dumps({"full_narasi": "from " + model,
                           "scenes": [{"narration_text": "a"}]})

    client = install_fake(monkeypatch, behaviour)
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=2)
    monkeypatch.setattr(gemini_llm.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(gemini_llm, "FALLBACK_MODELS", ["m1", "m2"])

    out = await llm.generate_narasi("topic")
    assert out["full_narasi"] == "from m2"
    assert "m2" in [c[0] for c in client.models.calls]


@pytest.mark.asyncio
async def test_empty_response_is_treated_as_failure(monkeypatch):
    def behaviour(model, n):
        return "" if n < 2 else '{"full_narasi": "ok", "scenes": [{"narration_text":"a"}]}'

    client = install_fake(monkeypatch, behaviour)
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=3)
    monkeypatch.setattr(gemini_llm.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(gemini_llm, "FALLBACK_MODELS", ["m1"])

    out = await llm.generate_narasi("topic")
    assert out["full_narasi"] == "ok"


@pytest.mark.asyncio
async def test_model_is_not_tried_twice(monkeypatch):
    """The configured model also appears in the fallback list.

    Regression: the chain was built with a list comprehension that kept both
    copies, spending retry budget on an identical request.
    """
    seen = []

    def behaviour(model, n):
        seen.append(model)
        return json.dumps({"full_narasi": "ok", "scenes": [{"narration_text": "a"}]})

    install_fake(monkeypatch, behaviour)
    monkeypatch.setattr(gemini_llm, "FALLBACK_MODELS", ["m1", "m2", "m1"])
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=1)

    await llm.generate_narasi("topic")
    assert seen == ["m1"], f"model tried more than once: {seen}"


@pytest.mark.asyncio
async def test_all_models_failing_raises_with_cause(monkeypatch):
    def behaviour(model, n):
        return RuntimeError("503 UNAVAILABLE")

    install_fake(monkeypatch, behaviour)
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=2)
    monkeypatch.setattr(gemini_llm.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(gemini_llm, "FALLBACK_MODELS", ["m1", "m2"])

    with pytest.raises(RuntimeError, match="all Gemini models failed"):
        await llm.generate_narasi("topic")


@pytest.mark.asyncio
async def test_missing_scenes_is_an_error(monkeypatch):
    """Empty scenes would build a zero-length video, so it must fail loudly."""
    def behaviour(model, n):
        return '{"full_narasi": "text", "scenes": []}'

    install_fake(monkeypatch, behaviour)
    llm = GeminiLLM(api_key="fake", model="m1", max_attempts=1)
    monkeypatch.setattr(gemini_llm, "FALLBACK_MODELS", ["m1"])

    with pytest.raises(ValueError, match="no scenes"):
        await llm.generate_narasi("topic")


async def _no_sleep(_seconds):
    """Skip backoff so retry tests stay fast."""
    return None