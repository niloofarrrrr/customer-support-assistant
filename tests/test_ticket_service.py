"""Service workflow tests: temporary SQLite database and mocked AI only."""

from io import BytesIO
import sqlite3
from unittest.mock import Mock

import pytest

from support_assistant.ai_service import AIServiceError
from support_assistant.csv_loader import CSVImportError
from support_assistant.models import Category, Priority, Ticket, TicketAnalysis, TicketStatus
from support_assistant.repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    TicketNotFoundError,
    TicketRepository,
)
from support_assistant.ticket_service import ImportSummary, TicketService


@pytest.fixture
def repository(tmp_path):
    repo = TicketRepository(tmp_path / "tickets.db")
    repo.initialize_database()
    return repo


@pytest.fixture
def ticket():
    return Ticket(
        ticket_id="T001",
        subject="Duplicate charge",
        description="I was charged twice for one purchase.",
    )


@pytest.fixture
def analysis():
    return TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="Please share the transaction reference.",
    )


def make_csv(*rows: str) -> BytesIO:
    return BytesIO(("ticket_id,subject,description\n" + "\n".join(rows) + "\n").encode())


def test_import_valid_csv_and_list_tickets(repository):
    service = TicketService(repository)
    summary = service.import_tickets(make_csv("T001,Billing,Charged twice", "T002,Login,Locked out"))
    assert summary == ImportSummary(imported=2, skipped=0, skipped_ids=())
    assert [record.ticket.ticket_id for record in service.list_tickets()] == ["T001", "T002"]
    assert all(record.status is TicketStatus.PENDING for record in service.list_tickets())


def test_import_skips_existing_ids_without_overwriting(repository, ticket):
    repository.save_ticket(ticket)
    service = TicketService(repository)
    summary = service.import_tickets(
        make_csv("T001,New subject,New description", "T002,Login,Password reset")
    )
    assert summary == ImportSummary(imported=1, skipped=1, skipped_ids=("T001",))
    assert service.get_ticket("T001").ticket == ticket


def test_reimport_same_csv_does_not_duplicate_tickets(repository):
    service = TicketService(repository)
    data = lambda: make_csv("T001,Billing,Charged twice", "T002,Login,Locked out")
    service.import_tickets(data())
    assert service.import_tickets(data()).skipped_ids == ("T001", "T002")
    assert len(service.list_tickets()) == 2


def test_invalid_csv_leaves_database_unchanged(repository):
    service = TicketService(repository)
    with pytest.raises(CSVImportError):
        service.import_tickets(make_csv("T001,Billing,Valid", "T002,,Invalid"))
    assert service.list_tickets() == []


def test_duplicate_id_inside_csv_rejects_entire_file(repository):
    service = TicketService(repository)
    with pytest.raises(CSVImportError, match="Duplicate ticket ID"):
        service.import_tickets(make_csv("T001,First,Valid", "T001,Second,Valid"))
    assert service.list_tickets() == []


def test_analyze_ticket_saves_analysis_and_updates_status(repository, ticket, analysis):
    repository.save_ticket(ticket)
    analyzer = Mock(return_value=analysis)
    service = TicketService(repository, analyzer=analyzer)
    saved = service.analyze_ticket("T001")
    analyzer.assert_called_once_with(ticket)
    assert saved.analysis == analysis
    assert saved.effective_category is Category.BILLING
    assert service.get_analysis("T001") == saved
    assert service.get_ticket("T001").status is TicketStatus.PROCESSED


def test_missing_ticket_does_not_call_ai(repository):
    analyzer = Mock()
    service = TicketService(repository, analyzer=analyzer)
    with pytest.raises(TicketNotFoundError):
        service.analyze_ticket("missing")
    analyzer.assert_not_called()


def test_already_processed_ticket_does_not_call_ai_again(repository, ticket, analysis):
    repository.save_ticket(ticket)
    repository.save_analysis("T001", analysis)
    analyzer = Mock()
    service = TicketService(repository, analyzer=analyzer)
    with pytest.raises(AnalysisAlreadyExistsError):
        service.analyze_ticket("T001")
    analyzer.assert_not_called()
    assert service.get_analysis("T001").analysis == analysis


def test_ai_failure_sets_failed_status_without_result(repository, ticket):
    repository.save_ticket(ticket)
    analyzer = Mock(side_effect=AIServiceError("Provider unavailable"))
    service = TicketService(repository, analyzer=analyzer)
    with pytest.raises(AIServiceError, match="Provider unavailable"):
        service.analyze_ticket("T001")
    assert service.get_ticket("T001").status is TicketStatus.FAILED
    assert service.get_analysis("T001") is None


def test_retry_failed_ticket_succeeds(repository, ticket, analysis):
    repository.save_ticket(ticket)
    analyzer = Mock(side_effect=[AIServiceError("Temporary outage"), analysis])
    service = TicketService(repository, analyzer=analyzer)
    with pytest.raises(AIServiceError):
        service.analyze_ticket("T001")
    saved = service.analyze_ticket("T001")
    assert saved.analysis == analysis
    assert analyzer.call_count == 2
    assert service.get_ticket("T001").status is TicketStatus.PROCESSED


def test_invalid_ai_response_is_rejected(repository, ticket):
    repository.save_ticket(ticket)
    service = TicketService(
        repository,
        analyzer=lambda t: {"category": "invented", "priority": "low", "draft_reply": "Hi"},
    )
    with pytest.raises(AIServiceError, match="invalid ticket analysis"):
        service.analyze_ticket("T001")
    assert service.get_ticket("T001").status is TicketStatus.FAILED
    assert service.get_analysis("T001") is None


def test_database_save_failure_does_not_report_processed(repository, ticket, analysis, monkeypatch):
    repository.save_ticket(ticket)
    service = TicketService(repository, analyzer=Mock(return_value=analysis))

    def fail_save(*args):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(repository, "save_analysis", fail_save)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        service.analyze_ticket("T001")
    assert service.get_ticket("T001").status is TicketStatus.PENDING
    assert service.get_analysis("T001") is None


def test_correct_classification_preserves_ai_predictions(repository, ticket, analysis):
    repository.save_ticket(ticket)
    repository.save_analysis("T001", analysis)
    service = TicketService(repository)
    corrected = service.correct_classification(
        "T001", Category.TECHNICAL_SUPPORT, Priority.HIGH
    )
    assert corrected.analysis == analysis
    assert corrected.effective_category is Category.TECHNICAL_SUPPORT
    assert corrected.effective_priority is Priority.HIGH
    assert service.get_analysis("T001") == corrected


def test_clearing_corrections_restores_ai_values(repository, ticket, analysis):
    repository.save_ticket(ticket)
    repository.save_analysis("T001", analysis)
    service = TicketService(repository)
    service.correct_classification("T001", Category.OTHER, Priority.LOW)
    restored = service.correct_classification("T001", None, None)
    assert restored.effective_category is Category.BILLING
    assert restored.effective_priority is Priority.MEDIUM


def test_cannot_correct_unanalyzed_ticket(repository, ticket):
    repository.save_ticket(ticket)
    service = TicketService(repository)
    with pytest.raises(AnalysisNotFoundError):
        service.correct_classification("T001", Category.OTHER, Priority.LOW)


def test_invalid_correction_leaves_existing_classification_untouched(
    repository, ticket, analysis
):
    repository.save_ticket(ticket)
    repository.save_analysis("T001", analysis)
    service = TicketService(repository)
    with pytest.raises(ValueError):
        service.correct_classification("T001", "not_a_category", Priority.LOW)
    assert service.get_analysis("T001").operator_category is None
    assert service.get_analysis("T001").operator_priority is None


def test_sample_csv_to_analysis_and_correction_survives_restart(
    repository, analysis
):
    from pathlib import Path

    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample_tickets.csv"
    service = TicketService(repository, analyzer=Mock(return_value=analysis))
    with sample_path.open("rb") as file:
        result = service.import_tickets(file)
    assert result.imported == 12
    assert result.skipped == 0
    assert len(service.list_tickets()) == 12

    saved = service.analyze_ticket("T001")
    assert saved.analysis == analysis
    service.correct_classification("T001", Category.OTHER, Priority.LOW)

    restarted = TicketRepository(repository.db_path)
    restarted.initialize_database()
    new_service = TicketService(restarted)
    assert new_service.get_ticket("T001").status is TicketStatus.PROCESSED
    assert new_service.get_analysis("T001").analysis == analysis
    assert new_service.get_analysis("T001").effective_category is Category.OTHER
