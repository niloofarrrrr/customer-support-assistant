from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import support_assistant.ai_service as ai_module
from support_assistant.ai_service import (
    AIServiceError,
    GeminiTicketAnalysis,
    analyze_ticket,
    create_client,
)
from support_assistant.models import Category, Priority, Ticket, TicketAnalysis


@pytest.fixture(autouse=True)
def configured_test_models(monkeypatch):
    """Tests must not depend on a developer's .env model settings."""
    monkeypatch.setattr(ai_module, "GEMINI_MODEL", "test-gemini-model")
    monkeypatch.setattr(ai_module, "OPENAI_MODEL", "test-openai-model")


@pytest.fixture
def sample_ticket():
    return Ticket(
        ticket_id="T001",
        subject="Duplicate payment",
        description="I was charged twice.",
    )


def test_successful_analysis(sample_ticket):
    expected = TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="We can investigate the charge.",
    )

    client = Mock()
    client.models.generate_content.return_value = (
        SimpleNamespace(text=expected.model_dump_json())
    )

    result = analyze_ticket(
    sample_ticket,
    client=client,
    provider="gemini",
    )

    assert result == expected

    client.models.generate_content.assert_called_once()

    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["config"].response_schema is GeminiTicketAnalysis


def test_invalid_category(sample_ticket):
    client = Mock()
    client.models.generate_content.return_value = (
        SimpleNamespace(
            text='{"category":"unknown","priority":"low",'
                 '"draft_reply":"Hello."}'
        )
    )

    with pytest.raises(AIServiceError):
        analyze_ticket(
            sample_ticket,
            client=client,
            provider="gemini",
        )


def test_empty_response(sample_ticket):
    client = Mock()
    client.models.generate_content.return_value = (
        SimpleNamespace(text=None)
    )

    with pytest.raises(
        AIServiceError, match="empty response"
    ):
        analyze_ticket(
            sample_ticket,
            client=client,
            provider="gemini",
        )


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(
        AIServiceError, match="GEMINI_API_KEY"
    ):
        create_client(provider="gemini")

def test_gemini_schema_compatibility():


    gemini_schema = GeminiTicketAnalysis.model_json_schema()
    application_schema = TicketAnalysis.model_json_schema()

    assert "additionalProperties" not in gemini_schema
    assert application_schema["additionalProperties"] is False



def test_openai_analysis(sample_ticket):
    expected = TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="We can investigate the charge.",
    )

    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        status="completed",
        output_parsed=expected,
    )

    result = analyze_ticket(
        sample_ticket,
        client=client,
        provider="openai",
    )

    assert result == expected

    kwargs = client.responses.parse.call_args.kwargs

    assert kwargs["text_format"] is TicketAnalysis
    assert kwargs["model"] is not None

    client.responses.parse.assert_called_once()


def test_invalid_provider(sample_ticket):
    with pytest.raises(
        AIServiceError,
        match="Unsupported AI provider",
    ):
        analyze_ticket(
            sample_ticket,
            provider="unknown_provider",
        )
