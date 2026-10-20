"""Application workflows connecting CSV import, AI analysis, and persistence.

The Streamlit UI should call this service rather than accessing SQLite or an
LLM provider directly. Only single-ticket AI processing is supported.
"""

from dataclasses import dataclass
from typing import BinaryIO, Callable

from pydantic import ValidationError

from .ai_service import AIServiceError, analyze_ticket
from .csv_loader import load_tickets
from .models import Category, Priority, Ticket, TicketAnalysis, TicketStatus

from .repository import (
    AnalysisAlreadyExistsError,
    StoredAnalysis,
    StoredTicket,
    TicketNotFoundError,
    TicketRepository,
)
from .repository import UNSET 

@dataclass(frozen=True)
class ImportSummary:
    imported: int
    skipped: int
    skipped_ids: tuple[str, ...]


class TicketService:
    """Coordinate the operator workflows without SQL or provider-specific code."""

    def __init__(
        self,
        repository: TicketRepository,
        analyzer: Callable[[Ticket], TicketAnalysis] | None = None,
    ) -> None:
        self.repository = repository
        self._analyzer = analyzer if analyzer is not None else analyze_ticket

    def import_tickets(self, file: BinaryIO) -> ImportSummary:
        """Validate the whole CSV, then save all new tickets in one transaction.

        Invalid CSV content is rejected before any write. If the database
        fails during the import, nothing from this file is saved.
        """
        tickets = load_tickets(file)  # Validate *all* rows first.
        skipped_ids = self.repository.save_tickets(tickets)
        return ImportSummary(
            imported=len(tickets) - len(skipped_ids),
            skipped=len(skipped_ids),
            skipped_ids=tuple(skipped_ids),
        )

    def list_tickets(self) -> list[StoredTicket]:
        return self.repository.get_all_tickets()

    def get_ticket(self, ticket_id: str) -> StoredTicket | None:
        return self.repository.get_ticket(ticket_id)

    def get_analysis(self, ticket_id: str) -> StoredAnalysis | None:
        return self.repository.get_analysis(ticket_id)

    def analyze_ticket(self, ticket_id: str) -> StoredAnalysis:
        """Analyze a pending/failed ticket and save the result.

        Known AI failures mark the ticket failed; a database write error is
        propagated without incorrectly marking the ticket processed.
        """
        stored = self.repository.get_ticket(ticket_id)
        if stored is None:
            raise TicketNotFoundError(f"Ticket '{ticket_id}' does not exist.")
        if (
            stored.status is TicketStatus.PROCESSED
            or self.repository.get_analysis(ticket_id) is not None
        ):
            raise AnalysisAlreadyExistsError(
                f"Ticket '{ticket_id}' already has an analysis."
            )

        try:
            raw_analysis = self._analyzer(stored.ticket)
            # The provider already validates results, but this also protects
            # the service if an alternative analyzer returns malformed data.
            candidate = (
                raw_analysis.model_dump()
                if isinstance(raw_analysis, TicketAnalysis)
                else raw_analysis
            )
            analysis = TicketAnalysis.model_validate(candidate)
        except AIServiceError:
            self.repository.mark_ticket_failed(ticket_id)
            raise
        except ValidationError as exc:
            self.repository.mark_ticket_failed(ticket_id)
            raise AIServiceError("AI returned an invalid ticket analysis.") from exc

        self.repository.save_analysis(ticket_id, analysis)
        saved = self.repository.get_analysis(ticket_id)
        if saved is None:
            raise RuntimeError(f"Saved analysis for '{ticket_id}' could not be retrieved.")
        return saved

    def correct_classification(
        self,
        ticket_id: str,
        category: Category | None | object = UNSET,
        priority: Priority | None | object = UNSET,
    ) -> StoredAnalysis:
        """Set/clear human overrides; never modify the AI's original output."""
        self.repository.update_classification(ticket_id, category, priority)
        saved = self.repository.get_analysis(ticket_id)
        if saved is None:
            raise RuntimeError(f"Corrected analysis for '{ticket_id}' could not be retrieved.")
        return saved
