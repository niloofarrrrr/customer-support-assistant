# AI Customer Support Assistant (SupportDesk)

A small Python application that helps customer-support operators **organize, prioritize, and respond to support tickets** with AI assistance.

It imports tickets from CSV, asks **Gemini or OpenAI** to classify each ticket and draft a reply, validates the result, and saves it in SQLite. An operator can then review the suggestion and correct its category or priority. **The application never sends messages to customers.**


## Features

- Import tickets from a UTF-8 CSV file and validate their contents before saving.
- Detect duplicate ticket IDs without overwriting previously imported tickets.
- Analyze **one ticket at a time** using either Gemini or OpenAI.
- Classify tickets into five categories and three priority levels.
- Generate a suggested customer reply and validate the structured AI output with Pydantic.
- Store tickets, AI results, and operator corrections in a local SQLite database.
- Review tickets and edit classifications in a Streamlit dashboard.
- Access the same workflows through a FastAPI REST API.
- Retry failed AI analyses without reprocessing successful ones.
- Run automated tests without making real LLM API calls.

## Requirements

- **Python 3.12 or newer** (the project specifies Python 3.12 in `.python-version`).
- **[uv](https://docs.astral.sh/uv/)** to install dependencies and run commands.
- An API key for **Google Gemini or OpenAI** to analyze tickets. Importing, browsing, and reviewing already-saved tickets do not require an API call.

The dependencies are defined in `pyproject.toml` and locked in `uv.lock`.

## Quick start

Run the following commands **from the project root** (the directory containing `pyproject.toml`).

### 1. Install dependencies

```bash
uv sync
```

If Python 3.12 is not installed, you can install it with `uv python install 3.12` and run `uv sync` again.

### 2. Set up your API credentials

Copy the provided environment template:

```bash
cp .env.example .env
```

On Windows Command Prompt, use `copy .env.example .env` instead.

Edit `.env` to choose a provider and supply **its** real API key. For example, to use Gemini:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
```

Or to use OpenAI:

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
```

Only the selected provider's key and model are needed. The examples show the model names currently provided in `.env.example`; you may need to use a model available to your account. The application loads `.env` using `python-dotenv`.

**Do not commit `.env` or real API keys to Git.** `.env` is excluded by `.gitignore`.

### 3. Start the Streamlit interface

```bash
uv run streamlit run app.py
```

Open the local address shown in the terminal (usually `http://localhost:8501`). The SQLite database is created automatically on first startup in `data/local/tickets.db`.

### 4. Try the sample workflow

1. In the sidebar, upload **`data/sample_tickets.csv`** and click **Import tickets**. The file contains 12 synthetic example tickets.
2. Select a ticket from the inbox. You can search tickets and filter by status.
3. Click **Analyze ticket** to generate a category, priority, and suggested reply. This step makes a real LLM API request.
4. Review the classification and draft reply. Optionally change the category or priority, then click **Save corrections**.
5. Reopen the ticket or restart the application: the saved analysis and corrections remain available.

If an AI request fails, the ticket becomes **failed** and the interface offers **Retry analysis**. A successfully processed ticket is not sent to the LLM again.

## Optional: FastAPI interface

The project also provides a REST API. Run it from the project root:

```bash
uv run uvicorn api:app --reload
```

Open **http://127.0.0.1:8000/docs** for the interactive API documentation, or use **http://127.0.0.1:8000/health** for a basic health check.

Streamlit and FastAPI use the same service layer and local SQLite database; **Streamlit does not need the API server to be running**.

### Available endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Basic API health check |
| `POST` | `/tickets/import` | Import tickets from an uploaded CSV file (2 MB API limit) |
| `GET` | `/tickets` | List saved tickets and their statuses |
| `GET` | `/tickets/{ticket_id}` | Retrieve a ticket |
| `POST` | `/tickets/{ticket_id}/analyze` | Generate and save an AI analysis |
| `GET` | `/tickets/{ticket_id}/analysis` | Retrieve the saved analysis and overrides |
| `PATCH` | `/tickets/{ticket_id}/classification` | Set or clear an operator override |

### API example

With the API server running, these commands demonstrate the same workflow in Windows PowerShell:

**Import the sample CSV:**

```powershell
curl.exe -X POST http://127.0.0.1:8000/tickets/import -F "file=@data/sample_tickets.csv"
```

**List and analyze tickets:**

```powershell
curl.exe http://127.0.0.1:8000/tickets
curl.exe -X POST http://127.0.0.1:8000/tickets/T001/analyze
```

**Correct a classification:**

```powershell
$body = @{
    category = "technical_support"
    priority = "high"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/tickets/T001/classification" -Method Patch -ContentType "application/json" -Body $body
```

**View the saved result:**

```powershell
curl.exe http://127.0.0.1:8000/tickets/T001/analysis
```

The analysis response includes the original AI values (`analysis`), any manual overrides (`operator_category`, `operator_priority`), and the values to use after applying overrides (`effective_category`, `effective_priority`). In a `PATCH` request, **omitting a field keeps its existing override**; setting it to `null` clears that override and restores the AI value.

You can also try all of these requests directly from `/docs` without using `curl`.

## Ticket format and AI output

A CSV must be UTF-8 encoded and have **exactly** these three column headers:

```csv
ticket_id,subject,description
T001,Duplicate payment,I was charged twice for the same order.
T002,Login issue,I cannot access my account.
```

Required fields cannot be empty. Ticket IDs must contain only letters, numbers, `_`, or `-` (maximum 30 characters). Subjects are limited to 150 characters, and descriptions to 3,000 characters.

The AI produces three required fields:

```json
{
  "category": "billing",
  "priority": "medium",
  "draft_reply": "Thank you for reporting this. Could you share the transaction reference so we can investigate?"
}
```

This is an **illustrative output**, not a guaranteed model response.

| Field | Allowed values |
|---|---|
| `category` | `billing`, `technical_support`, `account_access`, `general_inquiry`, `other` |
| `priority` | `low`, `medium`, `high` |
| `draft_reply` | Non-empty text |

The prompt asks for concise English replies based only on the ticket information, without invented policies or claims that actions have already been completed. AI-generated text still requires human review.

## How it works

Both interfaces call `TicketService`, which connects CSV validation, AI analysis, and SQLite persistence.

```text
Streamlit (app.py)       FastAPI (api.py)
        \                   /
            TicketService
          /     |      \
 CSV loader  AI service  Repository
 (Pydantic)  (LLM +      (SQLite)
             Pydantic)
```

1. The entire CSV is validated before ticket import begins.
2. New tickets are inserted into SQLite with status `pending`. Valid new tickets are inserted in a single transaction.
3. When the operator requests analysis, the service sends the selected ticket to the configured LLM provider.
4. The returned category, priority, and draft are validated using Pydantic before saving.
5. The analysis and its `processed` status are saved in a single SQLite transaction.
6. The operator may save category/priority overrides. The original AI output remains unchanged.

### Persistence and status

The database has two tables:

- **`tickets`** — ticket ID, subject, description, and status (`pending`, `processed`, or `failed`).
- **`ticket_results`** — one saved analysis per ticket, including the AI classification, draft reply, nullable operator overrides, and analysis timestamp.

A stored ticket can have **at most one** saved analysis. Saved operator overrides take precedence when showing the effective classification. The local database survives application restarts.

### Project structure

```text
.
├── app.py                         # Streamlit interface
├── api.py                         # FastAPI interface
├── assets/styles.css              # Streamlit styling
├── .streamlit/config.toml         # Streamlit theme
├── .env.example                   # Configuration template; no secrets
├── pyproject.toml                 # Dependencies and test configuration
├── uv.lock                        # Locked dependencies
├── data/sample_tickets.csv        # Synthetic example data
├── docs/
│   ├── architecture.md            # Design decisions
│   └── requirements.md            # Project requirements
├── src/support_assistant/
│   ├── ai_service.py               # Gemini/OpenAI calls and response validation
│   ├── config.py                   # Environment-based AI configuration
│   ├── csv_loader.py               # CSV parsing and input validation
│   ├── models.py                   # Pydantic models and enums
│   ├── prompts.py                  # Classification and reply instructions
│   ├── repository.py               # SQLite queries and transactions
│   └── ticket_service.py           # Shared business workflows
└── tests/                          # Unit, integration, UI, and API tests
```

`data/local/tickets.db` is generated locally and excluded from Git. See [architecture.md](docs/architecture.md) for more detail on the design and [requirements.md](docs/requirements.md) for the functional requirements.

## Error handling

- **Invalid CSV:** Missing/extra headers, malformed rows, invalid values, and duplicate IDs *within the same file* cause the file to be rejected before any tickets are imported.
- **Existing ticket IDs:** IDs already present in SQLite are skipped and included in the import summary; existing data is not overwritten.
- **Missing configuration or LLM error:** A handled AI error is displayed; the ticket is marked `failed` and can be retried.
- **Invalid AI output:** Pydantic rejects it; incomplete results are not saved.
- **Already processed ticket:** A second analysis is blocked to preserve the existing result and avoid another API charge.
- **Database error:** The application reports a failure instead of success. Saving an analysis and changing its status are atomic.

The API returns appropriate HTTP errors for common failures (including `400` for invalid CSV, `404` for missing data, `409` for conflicting operations, `413` for oversized CSV uploads, `422` for invalid requests, and `502` for handled AI errors).

## Run tests

```bash
uv run pytest -q
```

The tests cover input validation, duplicate handling, SQLite persistence and rollback, successful and failed analyses, retries, operator corrections, Streamlit interactions, and API endpoints. They use temporary databases and mocked AI responses, so **running the tests does not require a paid API call**. To verify the actual provider integration, analyze a sample ticket manually after configuring your API key.

## Assumptions and known limitations

This is a **local demonstration application**, not a production-ready customer-support platform.

- **Single-ticket analysis only:** There is no batch analysis or background job queue.
- **Human review required:** AI classifications and replies can be incorrect; no reply is automatically sent.
- **One analysis per ticket:** Reprocessing a successful ticket is intentionally blocked. Only the operator's category and priority can be corrected; the draft reply is not editable or sent within the application.
- **Local persistence:** SQLite is suitable for this assignment, but the app has no multi-user access control or centralized storage.
- **No API authentication or rate limiting:** Run the FastAPI server locally; do not expose it publicly without additional security controls.
- **External API dependency:** Analysis requires a valid provider key, an available model, network access, and may incur provider charges. LLM quality depends on the ticket and model.
- **CSV import constraints:** Files must follow the three-column format. The FastAPI upload endpoint has a 2 MB size limit. Validation is performed before insertion.
- **Sample data:** The bundled tickets are synthetic. Do not upload private or confidential customer information without appropriate data-handling controls; ticket content is sent to the selected AI provider for analysis.
- **Concurrent analysis:** If the same ticket is analyzed at the same moment from two places (Streamlit and the API), both may call the LLM. Only the first result is saved and the second request returns a conflict error. A production version would mark the ticket as "analyzing" before calling the LLM.



## AI assistant disclosure

I used AI coding assistants (Claude, ChatGPT) during this project for:

- discussing the architecture and design trade-offs
- reviewing my code and finding weaknesses 
- explaining how to fix those issues

I reviewed, tested, and understand the code in this repository. All tests were run locally, and the LLM integrations (Gemini/OpenAI) were tested with real API calls.