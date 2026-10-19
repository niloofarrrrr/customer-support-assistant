"""Tests for the Streamlit UI's service-layer wiring.

A minimal Streamlit fake keeps these tests deterministic and makes sure button
clicks call the right application workflows. It does not replace a browser test.
"""

from contextlib import nullcontext
from importlib import util
from io import BytesIO
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from support_assistant.ai_service import AIServiceError
from support_assistant.models import Category, Priority, Ticket, TicketAnalysis, TicketStatus
from support_assistant.repository import TicketRepository
from support_assistant.ticket_service import TicketService


class Rerun(Exception):
    """Simulates Streamlit stopping execution to rerun the script."""


class FakeStreamlit(ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.pressed = set()
        self.uploaded = None
        self.rendered_ticket_rows = []
        self.row_callbacks = {}
        self.button_callbacks = {}
        self.category_choice = "default"
        self.priority_choice = "default"
        self.messages = []
        self.tables = []
        self.markdowns = []
        self.search_query = ""
        self.filter_choice = "All statuses"
        self.sidebar = nullcontext()

    def set_page_config(self, **kwargs):
        pass

    def columns(self, spec, **kwargs):
        size = len(spec) if isinstance(spec, (list, tuple)) else spec
        return [nullcontext() for _ in range(size)]

    def container(self, **kwargs):
        return nullcontext()

    def divider(self):
        pass

    def title(self, value):
        pass

    def subheader(self, value):
        pass

    def caption(self, value):
        pass

    def markdown(self, value, **kwargs):
        self.markdowns.append(value)

    def write(self, value):
        pass

    def success(self, value):
        self.messages.append(("success", value))

    def error(self, value):
        self.messages.append(("error", value))

    def info(self, value):
        self.messages.append(("info", value))

    def warning(self, value):
        self.messages.append(("warning", value))

    def dataframe(self, value, **kwargs):
        raise AssertionError("The inbox must not render a checkbox dataframe")

    def text_input(self, label, **kwargs):
        if label == "Search tickets":
            return self.search_query
        return kwargs.get("value", "")

    def text_area(self, *args, **kwargs):
        return kwargs.get("value")

    def file_uploader(self, *args, **kwargs):
        return self.uploaded

    def button(self, label, **kwargs):
        key = kwargs.get("key", "")
        if key.startswith("ticket_row_"):
            self.rendered_ticket_rows.append((key, label, kwargs.get("type")))
            self.row_callbacks[key] = (
                kwargs["on_click"], kwargs.get("args", ()),
            )
            # In actual Streamlit, callbacks execute before the next render.
            return False
        if kwargs.get("on_click") is not None:
            self.button_callbacks[label] = (kwargs["on_click"], kwargs.get("args", ()))
            return False
        return not kwargs.get("disabled", False) and label in self.pressed

    def click_ticket(self, ticket_id):
        """Simulate a native button callback BEFORE the following script rerun."""
        callback, args = self.row_callbacks[f"ticket_row_{ticket_id}"]
        callback(*args)

    def click_button(self, label):
        """Run the registered callback before simulating the page rerun."""
        callback, args = self.button_callbacks[label]
        callback(*args)

    def selectbox(self, label, options, *, index=0, **kwargs):
        if label == "Filter by status":
            return self.filter_choice
        if label == "Category" and self.category_choice != "default":
            return self.category_choice
        if label == "Priority" and self.priority_choice != "default":
            return self.priority_choice
        return options[index]

    def form(self, *args, **kwargs):
        return nullcontext()

    def form_submit_button(self, label, **kwargs):
        return label in self.pressed

    def spinner(self, *args, **kwargs):
        return nullcontext()

    def rerun(self):
        raise Rerun()

    def stop(self):
        raise Rerun()


@pytest.fixture
def ui(monkeypatch, tmp_path):
    fake = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    spec = util.spec_from_file_location("customer_support_app_test_module", app_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DB_PATH", tmp_path / "tickets.db")
    return module, fake


def seed_ticket(db_path):
    repository = TicketRepository(db_path)
    repository.initialize_database()
    ticket = Ticket(ticket_id="T001", subject="Duplicate charge", description="Charged twice")
    repository.save_ticket(ticket)
    return repository, ticket


def sample_analysis():
    return TicketAnalysis(
        category=Category.BILLING,
        priority=Priority.MEDIUM,
        draft_reply="Please send the payment reference.",
    )


def test_empty_database_shows_import_instruction(ui):
    app, st = ui
    app.main()
    assert any("No tickets stored" in text for _, text in st.messages)


def test_import_button_persists_tickets_and_shows_summary(ui):
    app, st = ui
    st.uploaded = Mock()
    st.uploaded.getvalue.return_value = (
        b"ticket_id,subject,description\nT001,Payment,Charged twice\nT002,Login,Locked out\n"
    )
    st.pressed.add("Import tickets")
    with pytest.raises(Rerun):
        app.main()
    repo = TicketRepository(app.DB_PATH)
    assert len(repo.get_all_tickets()) == 2
    st.pressed.clear()
    app.main()
    assert ("success", "Imported 2 ticket(s); skipped 0 existing ticket(s).") in st.messages


def test_invalid_csv_does_not_insert_rows(ui):
    app, st = ui
    st.uploaded = Mock()
    st.uploaded.getvalue.return_value = b"ticket_id,subject,description\nT001,,Invalid\n"
    st.pressed.add("Import tickets")
    app.main()
    assert TicketRepository(app.DB_PATH).get_all_tickets() == []
    assert any("Import failed" in text for level, text in st.messages if level == "error")


def test_idle_page_never_calls_llm(ui, monkeypatch):
    app, st = ui
    seed_ticket(app.DB_PATH)
    analyzer = Mock()
    monkeypatch.setattr(app, "TicketService", lambda repo: TicketService(repo, analyzer=analyzer))
    app.main()
    analyzer.assert_not_called()
    assert st.rendered_ticket_rows == [("ticket_row_T001", "T001   ·   Duplicate charge   —   Pending", "secondary")]


def test_analyze_button_saves_result(ui, monkeypatch):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T001"
    _, ticket = seed_ticket(app.DB_PATH)
    analyzer = Mock(return_value=sample_analysis())
    monkeypatch.setattr(app, "TicketService", lambda repo: TicketService(repo, analyzer=analyzer))
    # Rendering only registers the callback; the AI call runs before rerender.
    app.main()
    analyzer.assert_not_called()
    st.click_button("Analyze ticket")
    analyzer.assert_called_once_with(ticket)
    repo = TicketRepository(app.DB_PATH)
    assert repo.get_ticket("T001").status is TicketStatus.PROCESSED
    assert repo.get_analysis("T001").analysis == sample_analysis()
    app.main()
    assert ("success", "Analysis saved for T001.") in st.messages


def test_failed_analysis_exposes_retry_and_does_not_save_result(ui, monkeypatch):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T001"
    seed_ticket(app.DB_PATH)
    analyzer = Mock(side_effect=AIServiceError("Provider unavailable"))
    monkeypatch.setattr(app, "TicketService", lambda repo: TicketService(repo, analyzer=analyzer))
    app.main()
    analyzer.assert_not_called()
    st.click_button("Analyze ticket")
    repo = TicketRepository(app.DB_PATH)
    assert repo.get_ticket("T001").status is TicketStatus.FAILED
    assert repo.get_analysis("T001") is None
    app.main()
    assert "Retry analysis" in st.button_callbacks
    assert any("can be retried" in text for level, text in st.messages if level == "warning")
    assert any("Analysis failed" in text for level, text in st.messages if level == "error")


def test_retry_uses_callback_without_rerendering_inbox_during_analysis(ui, monkeypatch):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T001"
    seed_ticket(app.DB_PATH)
    analyzer = Mock(side_effect=[AIServiceError("Temporary outage"), sample_analysis()])
    monkeypatch.setattr(app, "TicketService", lambda repo: TicketService(repo, analyzer=analyzer))

    app.main()
    assert analyzer.call_count == 0
    st.click_button("Analyze ticket")
    assert analyzer.call_count == 1

    # The failed rerender registers the retry callback, but does no AI work.
    app.main()
    before = len(st.rendered_ticket_rows)
    assert "Retry analysis" in st.button_callbacks
    st.click_button("Retry analysis")
    assert analyzer.call_count == 2
    assert len(st.rendered_ticket_rows) == before

    # Streamlit rerenders once after the API request finishes.
    app.main()
    assert TicketRepository(app.DB_PATH).get_ticket("T001").status is TicketStatus.PROCESSED
    assert ("success", "Analysis saved for T001.") in st.messages


def test_operator_changes_category_and_priority_without_overwriting_ai(ui):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T001"
    repo, _ = seed_ticket(app.DB_PATH)
    repo.save_analysis("T001", sample_analysis())
    st.category_choice = Category.TECHNICAL_SUPPORT
    st.priority_choice = Priority.HIGH
    st.pressed.add("Save corrections")
    with pytest.raises(Rerun):
        app.main()
    result = repo.get_analysis("T001")
    assert result.analysis == sample_analysis()
    assert result.effective_category is Category.TECHNICAL_SUPPORT
    assert result.effective_priority is Priority.HIGH


def test_operator_can_restore_ai_suggestions(ui):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T001"
    repo, _ = seed_ticket(app.DB_PATH)
    repo.save_analysis("T001", sample_analysis())
    repo.update_classification("T001", Category.OTHER, Priority.HIGH)
    st.category_choice = None
    st.priority_choice = None
    st.pressed.add("Save corrections")
    with pytest.raises(Rerun):
        app.main()
    result = repo.get_analysis("T001")
    assert result.operator_category is None
    assert result.operator_priority is None
    assert result.effective_category is Category.BILLING
    assert result.effective_priority is Priority.MEDIUM


def test_search_filters_inbox_by_subject(ui):
    app, st = ui
    repo, ticket = seed_ticket(app.DB_PATH)
    repo.save_ticket(ticket.model_copy(update={"ticket_id": "T002", "subject": "Login problem"}))
    st.search_query = "login"
    app.main()
    assert [key for key, _, _ in st.rendered_ticket_rows] == ["ticket_row_T002"]


def test_status_filter_shows_only_processed(ui):
    app, st = ui
    repo, ticket = seed_ticket(app.DB_PATH)
    repo.save_ticket(ticket.model_copy(update={"ticket_id": "T002"}))
    repo.save_analysis("T001", sample_analysis())
    st.filter_choice = "Processed"
    app.main()
    assert [key for key, _, _ in st.rendered_ticket_rows] == ["ticket_row_T001"]


def test_customer_text_is_escaped_before_rendering_html(ui):
    app, st = ui
    st.session_state["selected_ticket_id"] = "T003"
    repository = TicketRepository(app.DB_PATH)
    repository.initialize_database()
    repository.save_ticket(Ticket(
        ticket_id="T003", subject="<script>alert(1)</script>",
        description="<img src=x onerror=alert(1)>",
    ))
    app.main()
    markup = " ".join(st.markdowns)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in markup
    assert "&lt;img src=x onerror=alert(1)&gt;" in markup
    assert '<script>alert(1)</script>' not in markup


def test_clicking_ticket_row_opens_its_details(ui):
    app, st = ui
    repo, ticket = seed_ticket(app.DB_PATH)
    repo.save_ticket(ticket.model_copy(update={
        "ticket_id": "T002", "subject": "Login problem", "description": "Locked out",
    }))

    # Render once so Streamlit can register the row callbacks.
    app.main()
    assert not any("Ticket details" in markup for markup in st.markdowns)
    assert all(row_type == "secondary" for _, _, row_type in st.rendered_ticket_rows)

    # The callback runs BEFORE the next render, so row styling and details agree.
    st.click_ticket("T002")
    st.markdowns.clear()
    st.rendered_ticket_rows.clear()
    app.main()
    details = " ".join(st.markdowns)
    assert st.session_state["selected_ticket_id"] == "T002"
    assert ("ticket_row_T002", "T002   ·   Login problem   —   Pending", "primary") in st.rendered_ticket_rows
    assert ("ticket_row_T001", "T001   ·   Duplicate charge   —   Pending", "secondary") in st.rendered_ticket_rows
    assert "Ticket T002" in details
    assert "Login problem" in details
    assert "Locked out" in details
    assert "Ticket to review" not in details


def test_clicking_selected_ticket_again_hides_details(ui):
    app, st = ui
    seed_ticket(app.DB_PATH)

    app.main()
    st.click_ticket("T001")
    st.markdowns.clear()
    st.rendered_ticket_rows.clear()
    app.main()
    assert st.session_state["selected_ticket_id"] == "T001"
    assert st.rendered_ticket_rows[0][2] == "primary"
    assert any("Ticket details" in markup for markup in st.markdowns)

    # Second click deselects before render, removing both highlight and details.
    st.click_ticket("T001")
    st.markdowns.clear()
    st.rendered_ticket_rows.clear()
    app.main()
    assert st.session_state["selected_ticket_id"] is None
    assert st.rendered_ticket_rows[0][2] == "secondary"
    assert not any("Ticket details" in markup for markup in st.markdowns)
    assert not any('<div class="sd-section">AI analysis</div>' in markup for markup in st.markdowns)


def test_clicking_another_ticket_switches_details(ui):
    app, st = ui
    repo, ticket = seed_ticket(app.DB_PATH)
    repo.save_ticket(ticket.model_copy(update={"ticket_id": "T002", "subject": "Login problem"}))
    st.session_state["selected_ticket_id"] = "T001"
    app.main()
    st.click_ticket("T002")
    st.markdowns.clear()
    st.rendered_ticket_rows.clear()
    app.main()
    assert st.session_state["selected_ticket_id"] == "T002"
    assert [(key, row_type) for key, _, row_type in st.rendered_ticket_rows] == [
        ("ticket_row_T001", "secondary"), ("ticket_row_T002", "primary")
    ]
    assert any("Ticket T002" in markup for markup in st.markdowns)
    assert not any("Ticket T001</div>" in markup for markup in st.markdowns)


def test_row_click_after_search_uses_filtered_ticket_id(ui):
    app, st = ui
    repo, ticket = seed_ticket(app.DB_PATH)
    repo.save_ticket(ticket.model_copy(update={
        "ticket_id": "T002", "subject": "Login problem",
    }))
    st.search_query = "login"
    app.main()
    st.click_ticket("T002")
    st.markdowns.clear()
    st.rendered_ticket_rows.clear()
    app.main()
    assert st.session_state["selected_ticket_id"] == "T002"
    assert any("Ticket T002" in markup for markup in st.markdowns)
    assert [(key, row_type) for key, _, row_type in st.rendered_ticket_rows] == [
        ("ticket_row_T002", "primary")
    ]


def test_filter_clears_previously_selected_ticket(ui):
    app, st = ui
    repo, _ = seed_ticket(app.DB_PATH)
    repo.save_analysis("T001", sample_analysis())
    st.session_state["selected_ticket_id"] = "T001"
    app.main()
    assert any("Ticket details" in markup for markup in st.markdowns)

    st.filter_choice = "Pending"
    st.markdowns.clear()
    app.main()
    assert st.session_state["selected_ticket_id"] is None
    assert not any("Ticket details" in markup for markup in st.markdowns)


def test_operator_review_comes_before_suggested_reply(ui):
    app, st = ui
    repo, _ = seed_ticket(app.DB_PATH)
    repo.save_analysis("T001", sample_analysis())
    st.session_state["selected_ticket_id"] = "T001"
    app.main()

    def position(text):
        return next(i for i, markup in enumerate(st.markdowns) if text in markup)

    assert (position('>AI analysis</div>')
            < position('>AI category</div>')
            < position('>Operator review</div>')
            < position('>AI suggested reply</div>')
            < position("Please send the payment reference."))
