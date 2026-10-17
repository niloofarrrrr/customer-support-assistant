
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(str, Enum):
    BILLING = "billing"
    TECHNICAL_SUPPORT = "technical_support"
    ACCOUNT_ACCESS = "account_access"
    GENERAL_INQUIRY = "general_inquiry"
    OTHER = "other"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Ticket(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    ticket_id: str = Field(
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    subject: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=3000)


class TicketAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Priority
    draft_reply: str

    @field_validator("draft_reply")
    @classmethod
    def validate_reply(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Draft reply cannot be empty")
        return value
