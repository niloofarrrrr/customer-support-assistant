"""HTTP-level FastAPI tests using temporary SQLite and a fake AI analyzer."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import MAX_CSV_BYTES, create_app
from support_assistant.ai_service import AIServiceError
from support_assistant.models import Category, Priority, TicketAnalysis


CSV = b"ticket_id,subject,description\nT001,Payment,Charged twice\nT002,Login,Cannot log in\n"


@pytest.fixture
def analyzer():
    return Mock(return_value=TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="Please share your transaction reference.",
    ))


@pytest.fixture
def client(tmp_path, analyzer):
    with TestClient(create_app(tmp_path / "tickets.db", analyzer=analyzer)) as client:
        yield client


def import_csv(client, data=CSV):
    return client.post("/tickets/import", files={"file": ("tickets.csv", data, "text/csv")})


def test_health_and_docs(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_initially_empty_and_missing_ticket(client):
    assert client.get("/tickets").json() == []
    assert client.get("/tickets/unknown").status_code == 404
    assert client.get("/tickets/unknown/analysis").status_code == 404


def test_import_list_and_reimport(client, analyzer):
    response = import_csv(client)
    assert response.status_code == 200
    assert response.json() == {"imported": 2, "skipped": 0, "skipped_ids": []}
    assert client.get("/tickets/T001").json() == {
        "ticket_id": "T001", "subject": "Payment", "description": "Charged twice", "status": "pending"
    }
    assert [r["ticket_id"] for r in client.get("/tickets").json()] == ["T001", "T002"]
    assert import_csv(client).json() == {
        "imported": 0, "skipped": 2, "skipped_ids": ["T001", "T002"]
    }
    analyzer.assert_not_called()


def test_invalid_csv_does_not_partially_import(client):
    invalid = b"ticket_id,subject,description\nT001,Ok,Valid\nT002,,Invalid\n"
    response = import_csv(client, invalid)
    assert response.status_code == 400
    assert "Line 3:" in response.json()["detail"]
    assert client.get("/tickets").json() == []


def test_duplicate_within_csv_rejected(client):
    invalid = b"ticket_id,subject,description\nT001,One,Valid\nT001,Two,Valid\n"
    response = import_csv(client, invalid)
    assert response.status_code == 400
    assert "Duplicate ticket ID" in response.json()["detail"]
    assert client.get("/tickets").json() == []


def test_import_missing_file_and_size_limit(client):
    assert client.post("/tickets/import").status_code == 422
    response = import_csv(client, b"x" * (MAX_CSV_BYTES + 1))
    assert response.status_code == 413


def test_analyze_and_prevent_duplicate_api_call(client, analyzer):
    import_csv(client)
    response = client.post("/tickets/T001/analyze")
    assert response.status_code == 201
    saved = response.json()
    assert saved["ticket_id"] == "T001"
    assert saved["analysis"]["category"] == "billing"
    assert saved["analysis"]["priority"] == "medium"
    assert saved["effective_category"] == "billing"
    assert saved["operator_category"] is None
    assert saved["analyzed_at"]
    assert client.get("/tickets/T001/analysis").json() == saved
    assert client.get("/tickets/T001").json()["status"] == "processed"
    assert client.post("/tickets/T001/analyze").status_code == 409
    analyzer.assert_called_once()
    assert client.post("/tickets/missing/analyze").status_code == 404


def test_patch_is_partial_and_explicit_null_clears_override(client):
    import_csv(client)
    client.post("/tickets/T001/analyze")
    first = client.patch("/tickets/T001/classification", json={"category": "technical_support"})
    assert first.status_code == 200
    assert first.json()["operator_category"] == "technical_support"
    assert first.json()["operator_priority"] is None

    second = client.patch("/tickets/T001/classification", json={"priority": "high"})
    assert second.status_code == 200
    assert second.json()["operator_category"] == "technical_support"  # omitted field kept
    assert second.json()["effective_priority"] == "high"
    assert second.json()["analysis"]["priority"] == "medium"  # AI value unchanged

    restored = client.patch("/tickets/T001/classification", json={"category": None})
    assert restored.status_code == 200
    assert restored.json()["operator_category"] is None
    assert restored.json()["effective_category"] == "billing"
    assert restored.json()["effective_priority"] == "high"


def test_correction_request_validation_and_conflicts(client):
    assert client.patch("/tickets/missing/classification", json={"category": "billing"}).status_code == 404
    import_csv(client)
    assert client.patch("/tickets/T001/classification", json={"category": "billing"}).status_code == 409
    client.post("/tickets/T001/analyze")
    for payload in ({}, {"category": "invalid"}, {"category": "billing", "surprise": 1}):
        assert client.patch("/tickets/T001/classification", json=payload).status_code == 422


def test_ai_failure_marks_failed_and_retry_succeeds(client, analyzer):
    import_csv(client)
    analyzer.side_effect = [AIServiceError("Provider unavailable"), analyzer.return_value]
    failed = client.post("/tickets/T001/analyze")
    assert failed.status_code == 502
    assert client.get("/tickets/T001").json()["status"] == "failed"
    assert client.get("/tickets/T001/analysis").status_code == 404
    assert client.post("/tickets/T001/analyze").status_code == 201
    assert client.get("/tickets/T001").json()["status"] == "processed"


def test_restart_keeps_analysis_and_corrections(tmp_path, analyzer):
    path = tmp_path / "shared.db"
    with TestClient(create_app(path, analyzer=analyzer)) as first:
        import_csv(first)
        assert first.post("/tickets/T001/analyze").status_code == 201
        first.patch("/tickets/T001/classification", json={"category": "other"})
    with TestClient(create_app(path, analyzer=analyzer)) as second:
        saved = second.get("/tickets/T001/analysis")
        assert saved.status_code == 200
        assert saved.json()["effective_category"] == "other"
        assert second.get("/tickets/T001").json()["status"] == "processed"
