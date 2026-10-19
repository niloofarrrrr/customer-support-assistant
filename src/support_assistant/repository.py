"""SQLite persistence for tickets, AI analyses, and operator corrections."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from .models import Category, Priority, Ticket, TicketAnalysis, TicketStatus


class DuplicateTicketError(ValueError):
    """A ticket with the same ID is already stored."""


class TicketNotFoundError(LookupError):
    """The requested ticket does not exist."""


class AnalysisAlreadyExistsError(ValueError):
    """An analysis for this ticket is already stored."""


class AnalysisNotFoundError(LookupError):
    """No analysis exists to correct."""


@dataclass(frozen=True)
class StoredTicket:
    ticket: Ticket
    status: TicketStatus


@dataclass(frozen=True)
class StoredAnalysis:
    ticket_id: str
    analysis: TicketAnalysis
    operator_category: Category | None
    operator_priority: Priority | None
    analyzed_at: datetime

    @property
    def effective_category(self) -> Category:
        return self.operator_category or self.analysis.category

    @property
    def effective_priority(self) -> Priority:
        return self.operator_priority or self.analysis.priority


class TicketRepository:
    """Store application data in a file-backed SQLite database.

    Call initialize_database() once during application startup. Each operation
    opens and closes its own connection, so records survive application restarts.
    """

    def __init__(self, db_path: str | Path = "data/local/tickets.db") -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:  # Commit on success; roll back on an exception.
                yield connection
        finally:
            connection.close()

    def initialize_database(self) -> None:
        """Create the two tables, preserving any existing records."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'processed', 'failed'))
                );

                CREATE TABLE IF NOT EXISTS ticket_results (
                    ticket_id TEXT PRIMARY KEY
                        REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                    ai_category TEXT NOT NULL CHECK (
                        ai_category IN (
                            'billing', 'technical_support', 'account_access',
                            'general_inquiry', 'other'
                        )
                    ),
                    ai_priority TEXT NOT NULL
                        CHECK (ai_priority IN ('low', 'medium', 'high')),
                    draft_reply TEXT NOT NULL,
                    operator_category TEXT CHECK (
                        operator_category IN (
                            'billing', 'technical_support', 'account_access',
                            'general_inquiry', 'other'
                        )
                    ),
                    operator_priority TEXT
                        CHECK (operator_priority IN ('low', 'medium', 'high')),
                    analyzed_at TEXT NOT NULL
                );
                """
            )

    def save_ticket(self, ticket: Ticket) -> None:
        """Insert a ticket as pending; reject an existing ticket ID."""
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO tickets (ticket_id, subject, description, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        ticket.ticket_id,
                        ticket.subject,
                        ticket.description,
                        TicketStatus.PENDING.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise DuplicateTicketError(
                        f"Ticket '{ticket.ticket_id}' already exists."
                    ) from exc
                raise

    def get_ticket(self, ticket_id: str) -> StoredTicket | None:
        """Return a stored ticket, or None if its ID is unknown."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT ticket_id, subject, description, status "
                "FROM tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        return self._to_stored_ticket(row) if row is not None else None

    def get_all_tickets(self) -> list[StoredTicket]:
        """Return all tickets in stable ticket-ID order."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT ticket_id, subject, description, status "
                "FROM tickets ORDER BY ticket_id"
            ).fetchall()
        return [self._to_stored_ticket(row) for row in rows]

    @staticmethod
    def _to_stored_ticket(row: sqlite3.Row) -> StoredTicket:
        return StoredTicket(
            ticket=Ticket(
                ticket_id=row["ticket_id"],
                subject=row["subject"],
                description=row["description"],
            ),
            status=TicketStatus(row["status"]),
        )

    def save_analysis(self, ticket_id: str, analysis: TicketAnalysis) -> None:
        """Save a first analysis and mark the ticket processed in one transaction.

        Failed tickets can be retried. Existing analyses are never overwritten.
        """
        with self._connection() as connection:
            ticket_row = connection.execute(
                "SELECT status FROM tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if ticket_row is None:
                raise TicketNotFoundError(f"Ticket '{ticket_id}' does not exist.")
            if ticket_row["status"] == TicketStatus.PROCESSED.value:
                raise AnalysisAlreadyExistsError(
                    f"Ticket '{ticket_id}' was already processed."
                )

            try:
                connection.execute(
                    """
                    INSERT INTO ticket_results (
                        ticket_id, ai_category, ai_priority, draft_reply, analyzed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        analysis.category.value,
                        analysis.priority.value,
                        analysis.draft_reply,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise AnalysisAlreadyExistsError(
                        f"Ticket '{ticket_id}' already has an analysis."
                    ) from exc
                raise

            connection.execute(
                "UPDATE tickets SET status = ? WHERE ticket_id = ?",
                (TicketStatus.PROCESSED.value, ticket_id),
            )

    def get_analysis(self, ticket_id: str) -> StoredAnalysis | None:
        """Return an analysis and its corrections, or None when absent."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT ticket_id, ai_category, ai_priority, draft_reply,
                       operator_category, operator_priority, analyzed_at
                FROM ticket_results
                WHERE ticket_id = ?
                """,
                (ticket_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredAnalysis(
            ticket_id=row["ticket_id"],
            analysis=TicketAnalysis(
                category=row["ai_category"],
                priority=row["ai_priority"],
                draft_reply=row["draft_reply"],
            ),
            operator_category=(
                Category(row["operator_category"])
                if row["operator_category"] is not None else None
            ),
            operator_priority=(
                Priority(row["operator_priority"])
                if row["operator_priority"] is not None else None
            ),
            analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
        )

    def update_classification(
        self,
        ticket_id: str,
        category: Category | None,
        priority: Priority | None,
    ) -> None:
        """Set human overrides. None clears the override for that field."""
        # Validate any string inputs before opening a database transaction.
        corrected_category = Category(category) if category is not None else None
        corrected_priority = Priority(priority) if priority is not None else None
        with self._connection() as connection:
            result = connection.execute(
                """
                UPDATE ticket_results
                SET operator_category = ?, operator_priority = ?
                WHERE ticket_id = ?
                """,
                (
                    corrected_category.value if corrected_category else None,
                    corrected_priority.value if corrected_priority else None,
                    ticket_id,
                ),
            )
            if result.rowcount == 0:
                raise AnalysisNotFoundError(
                    f"Ticket '{ticket_id}' has no analysis to correct."
                )

    def mark_ticket_failed(self, ticket_id: str) -> None:
        """Mark a pending/failed ticket as failed without creating a result."""
        with self._connection() as connection:
            result = connection.execute(
                """
                UPDATE tickets SET status = ?
                WHERE ticket_id = ? AND status <> ?
                """,
                (TicketStatus.FAILED.value, ticket_id, TicketStatus.PROCESSED.value),
            )
            if result.rowcount == 0:
                row = connection.execute(
                    "SELECT 1 FROM tickets WHERE ticket_id = ?", (ticket_id,)
                ).fetchone()
                if row is None:
                    raise TicketNotFoundError(f"Ticket '{ticket_id}' does not exist.")
                raise ValueError(f"Cannot mark processed ticket '{ticket_id}' as failed.")
