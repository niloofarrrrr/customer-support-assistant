"""SupportDesk — light-theme Streamlit interface for support ticket review.

Run from the project root: uv run streamlit run app.py
All workflow and persistence operations are delegated to TicketService.
"""

from html import escape
from io import BytesIO
from pathlib import Path
import sqlite3
import sys

import streamlit as st

# Resolve imports when running the app directly from a src-layout project.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from support_assistant.ai_service import AIServiceError  # noqa: E402
from support_assistant.config import AI_PROVIDER  # noqa: E402
from support_assistant.csv_loader import CSVImportError  # noqa: E402
from support_assistant.models import Category, Priority, TicketStatus  # noqa: E402
from support_assistant.repository import (  # noqa: E402
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    TicketNotFoundError,
    TicketRepository,
)
from support_assistant.ticket_service import TicketService  # noqa: E402

DB_PATH = ROOT / "data" / "local" / "tickets.db"

# Keep visual styles separate from the application workflows.
STYLES = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")


def label_for(value: Category | Priority) -> str:
    """Make enum values readable: technical_support -> Technical Support."""
    return value.value.replace("_", " ").title()


def label_for_status(status: TicketStatus) -> str:
    return status.value.title()


def _notify(level: str, message: str) -> None:
    """Persist feedback across a Streamlit rerun."""
    st.session_state["notice"] = (level, message)
    st.rerun()


def _status_html(status: TicketStatus) -> str:
    return f'<span class="sd-pill {status.value}">● {label_for_status(status)}</span>'


def _metric(label: str, number: int, hint: str, symbol: str) -> None:
    st.markdown(
        f'<div class="sd-stat"><div class="sd-stat-head"><span>{escape(label)}</span>'
        f'<span class="sd-stat-icon">{symbol}</span></div>'
        f'<div class="sd-stat-number">{number}</div>'
        f'<div class="sd-stat-hint">{escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def _sidebar(service: TicketService) -> None:
    """Sidebar holds imports and lightweight workspace information."""
    with st.sidebar:
        st.markdown(
            '<div class="sd-brand"><div class="sd-brand-mark">✦</div><div>'
            '<div class="sd-brand-name">SupportDesk</div>'
            '<div class="sd-brand-sub">AI SUPPORT WORKSPACE</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sd-side-heading">IMPORT TICKETS</div>', unsafe_allow_html=True)
        st.caption("Add tickets from a UTF-8 CSV file.")
        uploaded_file = st.file_uploader(
            "Upload a CSV containing ticket_id, subject, and description",
            type=["csv"],
        )
        if st.button("Import tickets", disabled=uploaded_file is None, use_container_width=True):
            try:
                result = service.import_tickets(BytesIO(uploaded_file.getvalue()))
            except (CSVImportError, sqlite3.Error, OSError) as exc:
                st.error(f"Import failed: {exc}")
            else:
                _notify(
                    "success",
                    f"Imported {result.imported} ticket(s); "
                    f"skipped {result.skipped} existing ticket(s).",
                )

        st.divider()
        st.markdown('<div class="sd-side-heading">AI CONFIGURATION</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sd-provider"><span class="sd-indicator"></span>'
            f'<span>Provider: <strong>{escape(AI_PROVIDER.upper())}</strong></span></div>',
            unsafe_allow_html=True,
        )
        st.caption("The AI only suggests a response. No customer messages are sent.")
        st.divider()
        st.markdown('<div class="sd-side-heading">QUICK GUIDE</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sd-side-note">01 &nbsp; Import a CSV file<br>'
            '02 &nbsp; Select a support ticket<br>'
            '03 &nbsp; Generate an AI suggestion<br>'
            '04 &nbsp; Review and save corrections</div>',
            unsafe_allow_html=True,
        )


def _toggle_ticket_selection(ticket_id: str) -> None:
    """Toggle the selected ticket before Streamlit redraws the page.

    Streamlit runs button on_click callbacks before the main script rerun.
    This keeps row styling and the details panel in sync on the same click.
    """
    current_id = st.session_state.get("selected_ticket_id")
    st.session_state["selected_ticket_id"] = (
        None if current_id == ticket_id else ticket_id
    )


def _inbox(service: TicketService, records: list) -> str | None:
    """Render a ticket list where each row is a button, without checkboxes.

    Clicking a ticket opens its details. Clicking the same ticket again closes
    the details panel. Selection uses ticket IDs, not row indexes, so it stays
    correct when tickets are searched or filtered.
    """
    st.markdown('<div class="sd-section">Ticket inbox</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sd-section-desc">Select a ticket to view it. Click it again to close.</div>',
        unsafe_allow_html=True,
    )
    search_col, filter_col = st.columns([1.5, 1], gap="small")
    with search_col:
        query = st.text_input("Search tickets", placeholder="Search tickets...", key="ticket_search")
    with filter_col:
        status_filter = st.selectbox(
            "Filter by status", ["All statuses", "Pending", "Processed", "Failed"],
            key="ticket_filter",
        )

    text = query.strip().casefold()
    filtered = [
        r for r in records
        if (not text or text in r.ticket.ticket_id.casefold()
            or text in r.ticket.subject.casefold()
            or text in r.ticket.description.casefold())
        and (status_filter == "All statuses" or label_for_status(r.status) == status_filter)
    ]
    st.caption(f"{len(filtered)} of {len(records)} tickets")

    # Clear a selection that is no longer shown by the current filters.
    visible_ids = {r.ticket.ticket_id for r in filtered}
    selected_id = st.session_state.get("selected_ticket_id")
    if selected_id not in visible_ids:
        selected_id = None
        st.session_state["selected_ticket_id"] = None

    if not filtered:
        st.info("No tickets match these filters. Try a different search or status.")
        return None

    # Buttons are used instead of st.dataframe(selection_mode="single-row"):
    # the latter adds a checkbox column and does not provide the toggle-off
    # behavior needed by this interface.
    with st.container(height=490, border=False):
        for record in filtered:
            ticket_id = record.ticket.ticket_id
            status = label_for_status(record.status)
            label = f"{ticket_id}   ·   {record.ticket.subject}   —   {status}"
            st.button(
                label,
                key=f"ticket_row_{ticket_id}",
                use_container_width=True,
                type="primary" if ticket_id == selected_id else "secondary",
                help="Click again to close details" if ticket_id == selected_id else "View ticket details",
                on_click=_toggle_ticket_selection,
                args=(ticket_id,),
            )

    if selected_id is not None:
        st.markdown(
            f'<div class="sd-selection-note">● Selected ticket: '
            f'<strong>{escape(selected_id)}</strong> · Click the row again to close</div>',
            unsafe_allow_html=True,
        )
    return selected_id


def _ticket_details(record) -> None:
    """Render customer-provided text safely as HTML-escaped content."""
    st.markdown(
        '<div class="sd-ticket-top"><div class="sd-section">Ticket details</div>'
        f'{_status_html(record.status)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="sd-detail-id">Ticket {escape(record.ticket.ticket_id)}</div>'
        f'<div class="sd-ticket-title">{escape(record.ticket.subject)}</div>'
        '<div class="sd-mini-label">Customer message</div>'
        f'<div class="sd-message">{escape(record.ticket.description)}</div>',
        unsafe_allow_html=True,
    )


def _analysis_panel(service: TicketService, record) -> None:
    """Handle single-ticket analysis and operator corrections."""
    ticket_id = record.ticket.ticket_id
    try:
        saved = service.get_analysis(ticket_id)
    except sqlite3.Error as exc:
        st.error(f"Could not load the analysis: {exc}")
        return

    st.markdown('<div class="sd-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sd-section">AI analysis</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sd-section-desc">Suggestions are reviewed by a human before use.</div>',
        unsafe_allow_html=True,
    )

    if saved is None:
        if record.status is TicketStatus.FAILED:
            st.warning("The previous analysis failed. This ticket can be retried.")
        else:
            st.markdown(
                '<div class="sd-empty"><div class="sd-empty-icon">✦</div>'
                '<div class="sd-empty-title">Ready for AI review</div>'
                '<div class="sd-empty-desc">Generate a category, priority, and draft reply '
                'for this ticket. You stay in control of the final decision.</div></div>',
                unsafe_allow_html=True,
            )

        if st.button(
            "Retry analysis" if record.status is TicketStatus.FAILED else "Analyze ticket",
            type="primary", use_container_width=True,
        ):
            try:
                with st.spinner("Analyzing ticket..."):
                    service.analyze_ticket(ticket_id)
            except AIServiceError as exc:
                _notify("error", f"Analysis failed for {ticket_id}: {exc}")
            except (AnalysisAlreadyExistsError, TicketNotFoundError, sqlite3.Error, OSError) as exc:
                st.error(f"Could not save the analysis: {exc}")
            else:
                _notify("success", f"Analysis saved for {ticket_id}.")
        return

    cat_col, pri_col = st.columns(2, gap="small")
    with cat_col:
        st.markdown(
            '<div class="sd-result"><div class="label">AI category</div>'
            f'<div class="value">{escape(label_for(saved.analysis.category))}</div></div>',
            unsafe_allow_html=True,
        )
    with pri_col:
        st.markdown(
            '<div class="sd-result"><div class="label">AI priority</div>'
            f'<div class="value">{escape(label_for(saved.analysis.priority))}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sd-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sd-section">Operator review</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sd-section-desc">Override either field, or keep the AI suggestion.</div>',
        unsafe_allow_html=True,
    )
   

    categories: list[Category | None] = [None, *Category]
    priorities: list[Priority | None] = [None, *Priority]
    with st.form(key=f"corrections_{ticket_id}"):
        category = st.selectbox(
            "Category", options=categories, index=categories.index(saved.operator_category),
            format_func=lambda value: (
                f"Use AI suggestion ({label_for(saved.analysis.category)})"
                if value is None else label_for(value)
            ),
        )
        priority = st.selectbox(
            "Priority", options=priorities, index=priorities.index(saved.operator_priority),
            format_func=lambda value: (
                f"Use AI suggestion ({label_for(saved.analysis.priority)})"
                if value is None else label_for(value)
            ),
        )
        submitted = st.form_submit_button("Save corrections", type="primary", use_container_width=True)

    if submitted:
        try:
            service.correct_classification(ticket_id, category, priority)
        except (AnalysisNotFoundError, TicketNotFoundError, sqlite3.Error, ValueError) as exc:
            st.error(f"Could not save corrections: {exc}")
        else:
            _notify("success", f"Classification saved for {ticket_id}.")

    # The reply is deliberately last: operators review category/priority before
    # reading or copying the AI-generated customer response.
    st.markdown('<div class="sd-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sd-section">AI suggested reply</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sd-section-desc">Draft for the operator to review · Not sent</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="sd-reply">{escape(saved.analysis.draft_reply)}</div>',
        unsafe_allow_html=True,
    )
    st.caption(f"Analyzed {saved.analyzed_at.strftime('%d %b %Y · %H:%M UTC')}")


def main() -> None:
    st.set_page_config(
        page_title="SupportDesk | AI Ticket Workspace",
        page_icon="✦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(f"<style>{STYLES}</style>", unsafe_allow_html=True)

    repository = TicketRepository(DB_PATH)
    try:
        repository.initialize_database()
    except (sqlite3.Error, OSError) as exc:
        st.error(f"Could not initialize the database: {exc}")
        st.stop()

    service = TicketService(repository)
    _sidebar(service)

    notice = st.session_state.pop("notice", None)
    if notice is not None:
        level, message = notice
        if level == "success":
            st.success(message)
        else:
            st.error(message)

    st.markdown(
        '<div class="sd-hero"><div class="sd-eyebrow">Support operations / Workspace</div>'
        '<h1>Every ticket, one clear next step.</h1>'
        '<p>Prioritize incoming requests, review AI-generated replies, and make confident '
        'decisions with your team in control.</p></div>',
        unsafe_allow_html=True,
    )
    try:
        records = service.list_tickets()
    except sqlite3.Error as exc:
        st.error(f"Could not load tickets: {exc}")
        return

    counts = {s: sum(r.status is s for r in records) for s in TicketStatus}
    stat_cols = st.columns(4, gap="medium")
    with stat_cols[0]:
        _metric("Total tickets", len(records), "Imported into workspace", "▤")
    with stat_cols[1]:
        _metric("Pending review", counts[TicketStatus.PENDING], "Awaiting AI analysis", "◷")
    with stat_cols[2]:
        _metric("Analyzed", counts[TicketStatus.PROCESSED], "Ready for operator review", "✓")
    with stat_cols[3]:
        _metric("Need attention", counts[TicketStatus.FAILED], "Failed · retry available", "!")

    st.write("")
    if not records:
        with st.container(border=True):
            st.markdown(
                '<div class="sd-empty"><div class="sd-empty-icon">↥</div>'
                '<div class="sd-empty-title">Your inbox is empty</div>'
                '<div class="sd-empty-desc">No tickets stored yet. Import a CSV to begin. '
                'Use the uploader in the left sidebar.</div></div>',
                unsafe_allow_html=True,
            )
        st.info("No tickets stored yet. Import a CSV to begin.")
        return

    inbox_col, details_col = st.columns([1.05, 1.25], gap="large")
    with inbox_col:
        with st.container(border=True):
            selected_id = _inbox(service, records)
    with details_col:
        with st.container(border=True):
            if selected_id is None:
                st.info("Select a ticket from the inbox to view its details.")
            else:
                selected = next(r for r in records if r.ticket.ticket_id == selected_id)
                _ticket_details(selected)
                _analysis_panel(service, selected)

    st.caption("SupportDesk · Human-reviewed AI recommendations · Local SQLite storage")


if __name__ == "__main__":
    main()
