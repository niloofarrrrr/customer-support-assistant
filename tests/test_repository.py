"""Tests for the SQLite repository; no real database or LLM API is used."""

import sqlite3

import pytest

from support_assistant.models import Category, Priority, Ticket, TicketAnalysis, TicketStatus
from support_assistant.repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    DuplicateTicketError,
    TicketNotFoundError,
    TicketRepository,
)


@pytest.fixture
def repository(tmp_path):
    repo = TicketRepository(tmp_path / "tickets.db")
    repo.initialize_database()
    return repo


@pytest.fixture
def sample_ticket():
    return Ticket(
        ticket_id="T001",
        subject="Duplicate charge",
        description="I was charged twice for the same order.",
    )


@pytest.fixture
def sample_analysis():
    return TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="Please share the transaction reference.",
    )


def test_initialize_database_creates_tables_and_is_idempotent(repository):
    repository.initialize_database()
    with sqlite3.connect(repository.db_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"tickets", "ticket_results"}.issubset(names)


def test_save_and_get_ticket(repository, sample_ticket):
    repository.save_ticket(sample_ticket)
    stored = repository.get_ticket("T001")
    assert stored.ticket == sample_ticket
    assert stored.status == TicketStatus.PENDING


def test_unknown_ticket_returns_none(repository):
    assert repository.get_ticket("missing") is None
    assert repository.get_analysis("missing") is None


def test_duplicate_ticket_is_rejected_without_overwriting(repository, sample_ticket):
    repository.save_ticket(sample_ticket)
    with pytest.raises(DuplicateTicketError):
        repository.save_ticket(
            sample_ticket.model_copy(update={"subject": "Changed subject"})
        )
    assert repository.get_ticket("T001").ticket.subject == "Duplicate charge"


def test_get_all_tickets_is_ordered(repository, sample_ticket):
    repository.save_ticket(sample_ticket.model_copy(update={"ticket_id": "T002"}))
    repository.save_ticket(sample_ticket)
    assert [r.ticket.ticket_id for r in repository.get_all_tickets()] == ["T001", "T002"]


def test_save_analysis_updates_status(repository, sample_ticket, sample_analysis):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    stored = repository.get_analysis("T001")
    assert stored.analysis == sample_analysis
    assert stored.analyzed_at.tzinfo is not None
    assert stored.operator_category is None
    assert stored.operator_priority is None
    assert stored.effective_category == Category.BILLING
    assert repository.get_ticket("T001").status == TicketStatus.PROCESSED


def test_cannot_analyze_unknown_ticket(repository, sample_analysis):
    with pytest.raises(TicketNotFoundError):
        repository.save_analysis("missing", sample_analysis)
    assert repository.get_analysis("missing") is None


def test_cannot_overwrite_existing_analysis(repository, sample_ticket, sample_analysis):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    with pytest.raises(AnalysisAlreadyExistsError):
        repository.save_analysis(
            "T001", sample_analysis.model_copy(update={"draft_reply": "New reply"})
        )
    assert repository.get_analysis("T001").analysis == sample_analysis


def test_operator_corrections_preserve_original_ai_results(
    repository, sample_ticket, sample_analysis
):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    repository.update_classification("T001", Category.TECHNICAL_SUPPORT, Priority.HIGH)
    stored = repository.get_analysis("T001")
    assert stored.analysis == sample_analysis
    assert stored.operator_category == Category.TECHNICAL_SUPPORT
    assert stored.operator_priority == Priority.HIGH
    assert stored.effective_category == Category.TECHNICAL_SUPPORT
    assert stored.effective_priority == Priority.HIGH


def test_clearing_corrections_restores_ai_classification(
    repository, sample_ticket, sample_analysis
):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    repository.update_classification("T001", Category.OTHER, Priority.LOW)
    repository.update_classification("T001", None, None)
    stored = repository.get_analysis("T001")
    assert stored.effective_category == Category.BILLING
    assert stored.effective_priority == Priority.MEDIUM


def test_invalid_operator_category_is_rejected(repository, sample_ticket, sample_analysis):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    with pytest.raises(ValueError):
        repository.update_classification("T001", "not_a_category", Priority.LOW)
    assert repository.get_analysis("T001").operator_priority is None


def test_cannot_correct_unanalyzed_ticket(repository, sample_ticket):
    repository.save_ticket(sample_ticket)
    with pytest.raises(AnalysisNotFoundError):
        repository.update_classification("T001", Category.OTHER, Priority.LOW)


def test_failed_ticket_can_be_retried(repository, sample_ticket, sample_analysis):
    repository.save_ticket(sample_ticket)
    repository.mark_ticket_failed("T001")
    assert repository.get_ticket("T001").status == TicketStatus.FAILED
    assert repository.get_analysis("T001") is None
    repository.save_analysis("T001", sample_analysis)
    assert repository.get_ticket("T001").status == TicketStatus.PROCESSED


def test_cannot_mark_processed_or_unknown_ticket_failed(
    repository, sample_ticket, sample_analysis
):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    with pytest.raises(ValueError, match="processed"):
        repository.mark_ticket_failed("T001")
    with pytest.raises(TicketNotFoundError):
        repository.mark_ticket_failed("missing")
    assert repository.get_ticket("T001").status == TicketStatus.PROCESSED


def test_data_survives_new_repository_instance(
    repository, sample_ticket, sample_analysis
):
    repository.save_ticket(sample_ticket)
    repository.save_analysis("T001", sample_analysis)
    repository.update_classification("T001", Category.OTHER, None)

    restarted = TicketRepository(repository.db_path)
    restarted.initialize_database()
    assert restarted.get_ticket("T001").ticket == sample_ticket
    assert restarted.get_analysis("T001").effective_category == Category.OTHER


def test_analysis_and_status_changes_are_atomic(
    repository, sample_ticket, sample_analysis
):
    repository.save_ticket(sample_ticket)
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER prevent_status_update
            BEFORE UPDATE OF status ON tickets
            BEGIN
                SELECT RAISE(ABORT, 'forced database failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced database failure"):
        repository.save_analysis("T001", sample_analysis)

    assert repository.get_analysis("T001") is None
    assert repository.get_ticket("T001").status == TicketStatus.PENDING


def test_foreign_keys_are_enabled(repository):
    with repository._connection() as connection:
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO ticket_results (
                    ticket_id, ai_category, ai_priority, draft_reply, analyzed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("missing", "billing", "low", "Draft", "2026-10-18T00:00:00+00:00"),
            )
