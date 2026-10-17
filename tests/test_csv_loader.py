
from io import BytesIO

import pytest

from support_assistant.csv_loader import (
    CSVImportError,
    load_tickets,
)


def read_csv(text: str):
    return load_tickets(BytesIO(text.encode("utf-8")))


def test_valid_csv():
    content = (
        "ticket_id,subject,description\n"
        'T001,Billing,"I was charged twice, please help."\n'
    )

    tickets = read_csv(content)

    assert len(tickets) == 1
    assert tickets[0].ticket_id == "T001"
    assert tickets[0].description == (
        "I was charged twice, please help."
    )


def test_missing_column():
    content = "ticket_id,subject\nT001,Payment issue\n"

    with pytest.raises(
        CSVImportError, match="Missing required columns"
    ):
        read_csv(content)


def test_empty_ticket_id():
    content = (
        "ticket_id,subject,description\n"
        ",Login issue,Cannot log in\n"
    )

    with pytest.raises(CSVImportError, match="ticket_id"):
        read_csv(content)


def test_duplicate_ticket_id():
    content = (
        "ticket_id,subject,description\n"
        "T001,Issue A,Description A\n"
        "T001,Issue B,Description B\n"
    )

    with pytest.raises(
        CSVImportError, match="Duplicate ticket ID"
    ):
        read_csv(content)


def test_unexpected_column():
    content = (
        "ticket_id,subject,description,customer_age\n"
        "T001,Login,Login problem,30\n"
    )

    with pytest.raises(
        CSVImportError, match="Unexpected columns"
    ):
        read_csv(content)


def test_empty_dataset():
    content = "ticket_id,subject,description\n"

    with pytest.raises(
        CSVImportError, match="contains no tickets"
    ):
        read_csv(content)


def test_malformed_csv():
    content = (
        "ticket_id,subject,description\n"
        'T001,Login,"Unclosed description\n'
    )

    with pytest.raises(
        CSVImportError, match="Malformed CSV"
    ):
        read_csv(content)


def test_invalid_encoding():
    with pytest.raises(
        CSVImportError, match="UTF-8"
    ):
        load_tickets(BytesIO(b"\xff\xfe"))


def test_utf8_bom():
    content = (
        "ticket_id,subject,description\n"
        "T001,Login,Cannot access account\n"
    )
    file = BytesIO(content.encode("utf-8-sig"))

    tickets = load_tickets(file)

    assert len(tickets) == 1
