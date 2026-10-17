
import csv
from io import StringIO
from typing import BinaryIO

from pydantic import ValidationError

from .models import Ticket


REQUIRED_COLUMNS = {"ticket_id", "subject", "description"}


class CSVImportError(ValueError):
    """Raised when a CSV file cannot be imported."""


def load_tickets(file: BinaryIO) -> list[Ticket]:
    """Read and validate tickets from a UTF-8 CSV file."""

    # 1. Read and decode file content.
    try:
        raw_data = file.read()
    except (OSError, ValueError) as exc:
        raise CSVImportError(
            f"Cannot read CSV file: {exc}"
        ) from exc

    if not isinstance(raw_data, bytes):
        raise CSVImportError(
            "Expected a binary CSV file."
        )

    try:
        text = raw_data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVImportError(
            "CSV file must use UTF-8 encoding."
        ) from exc

    # 2. Initialize the CSV parser.
    reader = csv.DictReader(
        StringIO(text, newline=""),
        strict=True,
    )

    tickets: list[Ticket] = []
    seen_ids: set[str] = set()

    try:
        # 3. Validate CSV headers.
        headers = reader.fieldnames

        if headers is None:
            raise CSVImportError("CSV file is empty.")

        if len(headers) != len(set(headers)):
            raise CSVImportError(
                "CSV contains duplicate column names."
            )

        missing = REQUIRED_COLUMNS - set(headers)
        unexpected = set(headers) - REQUIRED_COLUMNS

        if missing:
            raise CSVImportError(
                f"Missing required columns: {sorted(missing)}"
            )

        if unexpected:
            raise CSVImportError(
                f"Unexpected columns: {sorted(unexpected)}"
            )

        # 4. Validate each ticket.
        for row in reader:
            line = reader.line_num

            if None in row:
                raise CSVImportError(
                    f"Line {line}: Too many values."
                )

            try:
                ticket = Ticket.model_validate(row)
            except ValidationError as exc:
                errors = "; ".join(
                    f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
                    for err in exc.errors()
                )
                raise CSVImportError(
                    f"Line {line}: {errors}"
                ) from exc

            # 5. Detect duplicate ticket IDs.
            if ticket.ticket_id in seen_ids:
                raise CSVImportError(
                    f"Line {line}: Duplicate ticket ID "
                    f"'{ticket.ticket_id}'."
                )

            seen_ids.add(ticket.ticket_id)
            tickets.append(ticket)

    except csv.Error as exc:
        raise CSVImportError(
            f"Malformed CSV near line {reader.line_num}: {exc}"
        ) from exc

    # 6. Reject files containing no tickets.
    if not tickets:
        raise CSVImportError(
            "CSV file contains no tickets."
        )

    return tickets
