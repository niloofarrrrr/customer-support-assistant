# Requirements

## Project Objective

The AI Customer Support Assistant is a Python application that helps customer-support operators manage incoming tickets. It imports tickets from CSV files, uses an LLM to classify each ticket by category and priority, generates a suggested reply, and stores the results.

Operators can review and correct AI-generated classifications before using the information.

## Core Principles

- The AI assists the operator rather than replacing them.
- Generated replies are suggestions and are never sent automatically.

## Functional Requirements

| ID | Requirement | Expected Behavior |
|---|---|---|
| FR-01 | CSV Import | Import customer-support tickets from a CSV file. |
| FR-02 | Input Validation | Validate required fields and handle duplicate ticket IDs. |
| FR-03 | AI Classification | Use Gemini or OpenAI to classify a ticket by category and priority. |
| FR-04 | Reply Generation | Generate a suggested customer response using the selected LLM. |
| FR-05 | Output Validation | Validate AI-generated results using a Pydantic schema. |
| FR-06 | Data Persistence | Store tickets, AI analyses, and operator corrections in SQLite. |
| FR-07 | Operator Review | Display tickets, classifications, priorities, and suggested replies. |
| FR-08 | Manual Correction | Allow operators to update category or priority without overwriting AI predictions. |
| FR-09 | Error Handling | Handle CSV validation, LLM, and database errors and allow failed analyses to be retried. |
| FR-10 | Single-Ticket Analysis | Allow the operator to select and analyze one ticket at a time. |
| FR-11 | Interfaces | Provide a Streamlit interface and FastAPI endpoints for ticket operations. |

## Input and Output Data

### Input: Customer-Support Ticket

Required CSV columns:

- `ticket_id` (str)
- `subject` (str)
- `description` (str)

Each ticket has an internal status: `pending`, `processed`, or `failed`.

### Output: AI Analysis

- `category` (Enum)
- `priority` (Enum): `low`, `medium`, `high`
- `draft_reply` (str)

Each analysis is associated with its original `ticket_id`. Operator corrections are stored separately from the AI predictions.

## Classification Rules

The application uses predefined categories and priorities to ensure consistent classification and structured output validation.

| Category | Meaning |
|---|---|
| **Billing** | Payments, invoices, refunds, subscriptions |
| **Technical Support** | Bugs, errors, application failures |
| **Account Access** | Login, password, account access problems |
| **General Inquiry** | Product or service information |
| **Other** | Requests that do not match the defined categories |

| Priority | Definition |
|---|---|
| **High** | Critical outages, severe access problems, security incidents, or major operational impact |
| **Medium** | Significant but non-critical problems |
| **Low** | General inquiries or minor issues |

Generated replies should use only information provided in the ticket, avoid inventing company policies or guarantees, and never claim that actions have already been completed.

## Application Workflow

1. **Import Tickets:** The operator imports tickets from a CSV file.
2. **Validate Input:** Ticket data is validated before being stored in SQLite.
3. **Select Ticket:** The operator selects a ticket for analysis.
4. **AI Analysis:** Gemini or OpenAI generates a category, priority, and suggested reply.
5. **Validate and Save:** Pydantic validates the AI response before the result is saved.
6. **Operator Review:** The operator reviews the analysis and can save classification corrections.

## Acceptance Criteria

| Test | Expected Result |
|---|---|
| Import a valid CSV | Valid tickets are stored in SQLite. |
| Import a CSV with a missing required column | A validation error is displayed, and no tickets from the file are imported. |
| Import a ticket with an ID already in the database | The existing ticket is skipped without being overwritten. |
| Import a CSV containing duplicate ticket IDs | The entire CSV is rejected. |
| Analyze a billing complaint | The LLM returns a valid category, priority, and suggested reply. |
| Receive an invalid LLM category | Validation rejects the result without saving an incomplete analysis. |
| Correct a classification | The correction is saved while preserving the original AI prediction. |
| Restart the application | Previously saved tickets, analyses, and corrections remain available. |
| Encounter an LLM API error | The ticket is marked as failed and can be retried. |
| Analyze an already processed ticket | Reanalysis is prevented, preserving the saved result. |