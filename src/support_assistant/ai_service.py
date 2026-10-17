
import os
from typing import Any

from google import genai
from google.genai import errors as gemini_errors, types
from openai import OpenAI, OpenAIError
from pydantic import ConfigDict, ValidationError

from .config import AI_PROVIDER, GEMINI_MODEL, OPENAI_MODEL
from .models import Ticket, TicketAnalysis
from .prompts import SYSTEM_PROMPT


class AIServiceError(Exception):
    """Raised when AI analysis cannot be completed."""


class GeminiTicketAnalysis(TicketAnalysis):
    """Schema compatible with Gemini's API."""

    model_config = ConfigDict(extra="ignore")


def create_client(provider: str | None = None):
    """Create a client for the selected provider."""

    provider = (provider or AI_PROVIDER).lower()

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise AIServiceError(
                "GEMINI_API_KEY is not configured."
            )

        return genai.Client(api_key=api_key)

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise AIServiceError(
                "OPENAI_API_KEY is not configured."
            )

        return OpenAI(
            api_key=api_key,
            timeout=30.0,
            max_retries=1,
        )

    raise AIServiceError(
        f"Unsupported AI provider: {provider}"
    )


def _analyze_with_gemini(
    ticket: Ticket,
    client: Any,
) -> TicketAnalysis:
    """Analyze a ticket using Gemini."""

    if not GEMINI_MODEL:
        raise AIServiceError("GEMINI_MODEL is not configured.")

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ticket.model_dump_json(),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=GeminiTicketAnalysis,
                temperature=0.2,
                max_output_tokens=1024,
            ),
        )

    except gemini_errors.APIError as exc:
        raise AIServiceError(
            f"Gemini API request failed (HTTP {exc.code})."
        ) from exc

    if not response.text:
        raise AIServiceError(
            "Gemini returned an empty response."
        )

    try:
        return TicketAnalysis.model_validate_json(
            response.text
        )
    except ValidationError as exc:
        raise AIServiceError(
            "Gemini returned an invalid analysis."
        ) from exc


def _analyze_with_openai(
    ticket: Ticket,
    client: Any,
) -> TicketAnalysis:
    """Analyze a ticket using OpenAI."""

    if not OPENAI_MODEL:
        raise AIServiceError("OPENAI_MODEL is not configured.")

    try:
        response = client.responses.parse(
            model=OPENAI_MODEL,
            instructions=SYSTEM_PROMPT,
            input=ticket.model_dump_json(),
            text_format=TicketAnalysis,
            max_output_tokens=1024,
            store=False,
        )

    except (OpenAIError, ValidationError) as exc:
        raise AIServiceError(
            "OpenAI analysis request failed."
        ) from exc

    if response.status != "completed":
        raise AIServiceError(
            f"OpenAI response status: {response.status}"
        )

    result = response.output_parsed

    if not isinstance(result, TicketAnalysis):
        raise AIServiceError(
            "OpenAI did not return a valid ticket analysis."
        )

    return result


def analyze_ticket(
    ticket: Ticket,
    client: Any = None,
    *,
    provider: str | None = None,
) -> TicketAnalysis:
    """Analyze a ticket using the configured AI provider."""

    selected_provider = (provider or AI_PROVIDER).lower()

    if selected_provider not in {"gemini", "openai"}:
        raise AIServiceError(
            f"Unsupported AI provider: {selected_provider}"
        )

    if client is None:
        client = create_client(selected_provider)

    if selected_provider == "gemini":
        return _analyze_with_gemini(ticket, client)

    return _analyze_with_openai(ticket, client)
