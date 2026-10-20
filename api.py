"""REST API for SupportDesk. Run: uv run uvicorn api:app --reload.

FastAPI is an additional interface; Streamlit continues to use TicketService
without going through HTTP. Both interfaces share the same SQLite database.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Callable

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from support_assistant.ai_service import AIServiceError  # noqa: E402
from support_assistant.csv_loader import CSVImportError  # noqa: E402
from support_assistant.models import (  # noqa: E402
    Category, Priority, Ticket, TicketAnalysis, TicketStatus,
)
from support_assistant.repository import (  # noqa: E402
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    StoredAnalysis,
    StoredTicket,
    TicketNotFoundError,
    TicketRepository,
    UNSET,
)
from support_assistant.ticket_service import TicketService  # noqa: E402

logger = logging.getLogger(__name__)
DB_PATH = ROOT / "data" / "local" / "tickets.db"
MAX_CSV_BYTES = 2 * 1024 * 1024


class TicketOut(BaseModel):
    ticket_id: str
    subject: str
    description: str
    status: TicketStatus

    @classmethod
    def from_record(cls, record: StoredTicket) -> "TicketOut":
        return cls(**record.ticket.model_dump(), status=record.status)


class AnalysisOut(BaseModel):
    ticket_id: str
    analysis: TicketAnalysis
    operator_category: Category | None
    operator_priority: Priority | None
    effective_category: Category
    effective_priority: Priority
    analyzed_at: datetime

    @classmethod
    def from_record(cls, record: StoredAnalysis) -> "AnalysisOut":
        return cls(
            ticket_id=record.ticket_id,
            analysis=record.analysis,
            operator_category=record.operator_category,
            operator_priority=record.operator_priority,
            effective_category=record.effective_category,
            effective_priority=record.effective_priority,
            analyzed_at=record.analyzed_at,
        )


class ImportSummaryOut(BaseModel):
    imported: int
    skipped: int
    skipped_ids: tuple[str, ...]


class ClassificationPatch(BaseModel):
    """Omitted fields retain their overrides; explicit null clears an override."""

    model_config = ConfigDict(extra="forbid")
    category: Category | None = None
    priority: Priority | None = None


def get_service(request: Request) -> TicketService:
    """Provide the service initialized during application startup."""
    return request.app.state.service


def create_app(
    db_path: str | Path = DB_PATH,
    *,
    analyzer: Callable[[Ticket], TicketAnalysis] | None = None,
) -> FastAPI:
    """Create an API instance; injectable paths and analyzers support isolated tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository = TicketRepository(db_path)
        repository.initialize_database()
        app.state.service = TicketService(repository, analyzer=analyzer)
        yield

    app = FastAPI(
        title="SupportDesk API",
        description="Human-reviewed customer-support ticket processing. No replies are sent.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(CSVImportError)
    async def csv_error(_request: Request, exc: CSVImportError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(TicketNotFoundError)
    async def ticket_not_found(_request: Request, exc: TicketNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(AnalysisNotFoundError)
    async def analysis_not_found(_request: Request, exc: AnalysisNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AnalysisAlreadyExistsError)
    async def analysis_exists(_request: Request, exc: AnalysisAlreadyExistsError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AIServiceError)
    async def ai_error(_request: Request, exc: AIServiceError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.error("Database operation failed", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Database operation failed."})

    @app.get("/health", tags=["System"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/tickets", response_model=list[TicketOut], tags=["Tickets"])
    def list_tickets(service: TicketService = Depends(get_service)) -> list[TicketOut]:
        return [TicketOut.from_record(record) for record in service.list_tickets()]

    @app.post("/tickets/import", response_model=ImportSummaryOut, tags=["Tickets"])
    def import_tickets(
        file: UploadFile = File(description="UTF-8 CSV: ticket_id,subject,description"),
        service: TicketService = Depends(get_service),
    ) -> ImportSummaryOut:
        # Bound memory usage even for unexpectedly large uploaded files.
        raw = file.file.read(MAX_CSV_BYTES + 1)
        if len(raw) > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV file exceeds the 2 MB limit.")
        summary = service.import_tickets(BytesIO(raw))
        return ImportSummaryOut(
            imported=summary.imported,
            skipped=summary.skipped,
            skipped_ids=summary.skipped_ids,
        )

    @app.get("/tickets/{ticket_id}", response_model=TicketOut, tags=["Tickets"])
    def get_ticket(
        ticket_id: str, service: TicketService = Depends(get_service)
    ) -> TicketOut:
        record = service.get_ticket(ticket_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' does not exist.")
        return TicketOut.from_record(record)

    @app.post(
        "/tickets/{ticket_id}/analyze",
        response_model=AnalysisOut,
        status_code=status.HTTP_201_CREATED,
        tags=["Analysis"],
    )
    def analyze_ticket(
        ticket_id: str, service: TicketService = Depends(get_service)
    ) -> AnalysisOut:
        return AnalysisOut.from_record(service.analyze_ticket(ticket_id))

    @app.get("/tickets/{ticket_id}/analysis", response_model=AnalysisOut, tags=["Analysis"])
    def get_analysis(
        ticket_id: str, service: TicketService = Depends(get_service)
    ) -> AnalysisOut:
        record = service.get_analysis(ticket_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No analysis found for ticket '{ticket_id}'.")
        return AnalysisOut.from_record(record)

    @app.patch("/tickets/{ticket_id}/classification", response_model=AnalysisOut, tags=["Analysis"])
    def correct_classification(
        ticket_id: str,
        changes: ClassificationPatch,
        service: TicketService = Depends(get_service),
    ) -> AnalysisOut:
        supplied = changes.model_fields_set
        if not supplied:
            raise HTTPException(status_code=422, detail="Provide category and/or priority.")

        current = service.get_analysis(ticket_id)
        if current is None:
            if service.get_ticket(ticket_id) is None:
                raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' does not exist.")
            raise HTTPException(status_code=409, detail="Analyze this ticket before correcting it.")

        category = changes.category if "category" in supplied else UNSET
        priority = changes.priority if "priority" in supplied else UNSET
        return AnalysisOut.from_record(
            service.correct_classification(ticket_id, category, priority)
        )

    return app


app = create_app()
