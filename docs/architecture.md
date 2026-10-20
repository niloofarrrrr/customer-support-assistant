# Architecture
The application is organized into four main layers:

1. **Presentation Layer**
2. **Service Layer**
3. **AI Layer**
4. **Data Layer**

### Why this architecture?

The main principle is separation of concerns. Each layer has a specific responsibility:

- **Presentation Layer:** Provides the Streamlit interface and FastAPI endpoints for importing, viewing, analyzing, and correcting tickets.
- **Service Layer:** Coordinates application workflows and applies business rules.
- **AI Layer:** Communicates with the selected LLM provider (Gemini or OpenAI) and validates structured responses.
- **Data Layer:** Validates CSV input and handles SQLite persistence for tickets, AI results, and operator corrections.

This separation keeps the application easier to maintain and test without tightly coupling the user interfaces, business logic, AI integration, and database operations.

### Project structure

```text
Customer_Support_Assistant/
├── .streamlit/
│   └── config.toml
├── assets/
│   └── styles.css
├── .env                    # Local only, not committed
├── .env.example
├── .gitignore
├── .python-version
├── app.py
├── api.py
├── pyproject.toml
├── README.md
├── uv.lock
│
├── data/
│   ├── sample_tickets.csv
│   └── local/
│       └── tickets.db      # Generated locally
│
├── docs/
│   ├── architecture.md
│   └── requirements.md
│
├── src/
│   └── support_assistant/
│       ├── __init__.py
│       ├── ai_service.py
│       ├── config.py
│       ├── csv_loader.py
│       ├── models.py
│       ├── prompts.py
│       ├── repository.py
│       └── ticket_service.py
│
└── tests/
    ├── test_ai_service.py
    ├── test_api.py
    ├── test_app.py
    ├── test_csv_loader.py
    ├── test_repository.py
    └── test_ticket_service.py
```

### Responsibilities of the main files

| File | Responsibility |
|---|---|
| `app.py` | Provide the Streamlit interface for importing, viewing, analyzing, and correcting tickets. |
| `api.py` | Provide FastAPI REST endpoints for ticket operations using the shared service layer. |
| `models.py` | Define and validate ticket data, AI responses, categories, priorities, and statuses using Pydantic. |
| `csv_loader.py` | Read and validate CSV files before importing tickets. |
| `ai_service.py` | Communicate with Gemini or OpenAI and validate structured LLM responses. |
| `prompts.py` | Store classification rules and reply-generation instructions. |
| `repository.py` | Handle SQLite persistence for tickets, AI results, and operator corrections. |
| `ticket_service.py` | Coordinate ticket imports, single-ticket analysis, and operator corrections. |
| `config.py` | Load the selected AI provider and model configuration from environment variables. |


## LLM Requests

Two approaches were considered:

| Approach | Advantages | Disadvantages |
|---|---|---|
| One API request per ticket | Simple validation, independent error handling, easier retry | More API calls when processing multiple tickets |
| One API request for multiple tickets | Potentially fewer requests | More complex output validation, context limits, and error recovery |

**Final decision:** One API request per ticket.

The current implementation processes tickets individually. Each ticket is sent to the selected LLM provider (Gemini or OpenAI), and the structured response is validated before being saved to SQLite.

This approach keeps the implementation simple, makes errors easier to handle, and allows failed tickets to be retried independently. Previously processed tickets are not analyzed again, avoiding unnecessary API calls.

## Design the Data Model

The application distinguishes three types of information:

1. Original customer tickets
2. AI-generated results
3. Operator corrections

Two SQLite tables are used to store this information.

### `tickets`

Stores the original customer requests and their processing status.

- `ticket_id` (TEXT, primary key)
- `subject` (TEXT, required)
- `description` (TEXT, required)
- `status` (TEXT): `pending`, `processed`, or `failed`

### `ticket_results`

Stores the AI analysis and any operator corrections.

- `ticket_id` (TEXT, primary key and foreign key)
- `ai_category` (TEXT, required)
- `ai_priority` (TEXT, required)
- `draft_reply` (TEXT, required)
- `operator_category` (TEXT, nullable)
- `operator_priority` (TEXT, nullable)
- `analyzed_at` (TEXT, required)

The tables have a one-to-one relationship through `ticket_id`. Each ticket can have at most one saved AI analysis.

### Why separate AI predictions and operator corrections?

Keeping AI predictions and operator corrections in separate fields preserves the original AI output while allowing the operator to override the category or priority.

When an operator correction exists, it takes precedence over the AI prediction. Clearing a correction restores the original AI value without requiring another API request.

### Processing and data consistency

- Tickets are initially stored with a `pending` status.
- After successful AI analysis, the result is saved and the ticket status changes to `processed`.
- If AI analysis fails with a handled error, the ticket is marked as `failed` and remains available for retry.
- Already processed tickets cannot be analyzed again, avoiding unnecessary API calls and preserving existing results.
- Saving an AI result and updating the ticket status happen within a single database transaction to prevent inconsistent records.
- CSV import saves all new tickets in a single transaction. If a database error occurs, none of the tickets from that file are saved.
- Operator corrections update only the fields that were sent, so two partial updates cannot overwrite each other.



## Error Handling

The application handles validation, API, and database errors to prevent invalid or incomplete results from being saved.

| Failure | Implemented behavior |
|---|---|
| Invalid CSV | Display a validation error and reject the file before importing any tickets. |
| Duplicate ticket ID | Skip IDs already stored in SQLite and report them. Reject the CSV if it contains duplicate IDs within the file. |
| Missing API key | Raise an `AIServiceError` and display a configuration error. |
| LLM API failure | Mark the ticket as `failed` and allow the operator to retry the analysis. |
| Invalid LLM output | Reject the response, do not save the analysis, and mark the ticket as `failed`. |
| Database error | Display an error without reporting a successful operation. |
| Already processed ticket | Prevent repeated analysis to preserve existing results and avoid unnecessary API calls. |

### CSV Validation

The CSV loader validates the entire file before any records are inserted.

- Reject files with missing, unexpected, or duplicate column names.
- Reject rows with missing or invalid required values.
- Reject malformed CSV files and unsupported encodings.
- Reject the entire CSV if duplicate ticket IDs appear within the file.
- Skip ticket IDs that already exist in SQLite.
- Report the number of successfully imported and skipped tickets.

### AI Analysis and Retry

Each ticket is analyzed individually. If a handled LLM request or response-validation error occurs, the ticket is marked as `failed` without saving an incomplete analysis.

Failed tickets remain available for retry. Successfully processed tickets retain their saved analysis and cannot be analyzed again through the current workflow.

### Database Consistency

SQLite transactions are used to keep data consistent. Saving an AI analysis and updating its ticket status occur within a single transaction. If a database operation fails, the transaction is rolled back, and the application does not report a successful analysis.


## Application Workflow

1. **Import:** The operator uploads a CSV file through Streamlit or submits tickets through the API.
2. **Validation:** Pydantic validates the ticket data before it is stored in SQLite.
3. **AI Analysis:** The service layer sends a selected ticket to Gemini or OpenAI for classification and draft reply generation.
4. **Persistence:** The validated AI result is saved in SQLite, and the ticket status is updated.
5. **Operator Review:** The operator reviews the AI suggestions and can save category or priority corrections without overwriting the original AI output.

Both Streamlit and FastAPI use the same service layer, keeping business logic independent of the interface.