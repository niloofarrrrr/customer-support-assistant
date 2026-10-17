# The Architecture

The application will have four main components:
1. **Presentation Layer**
2. **Service Layer** 
3. **AI Layer**
4. **Data Layer**


Why this architecture?
The key principle is separation of concerns.
Each component has a specific responsibility.
- Presentation layer: Displays information and receives operator actions.
- Service layer: Coordinates operations and applies business rules.
- AI layer: Communicates with the LLM and validates its output.
- Data layer: Imports, retrieves, and stores tickets and results.



```text
Customer _Support_Assistant/
├── .env
├── .env.example
├── .gitignore
├── .python-version
├── app.py
├── pyproject.toml
├── README.md
├── uv.lock
│
├── data/
│   ├── sample_tickets.csv
│   └── local/
│       └── tickets.db
│
├── docs/
│   ├── architecture.md
│   └── requirements.md
│
├── src/
│   └── support_assistant/
│       ├── ai_service.py
│       ├── config.py
│       ├── csv_loader.py
│       ├── models.py
│       ├── prompts.py
│       ├── repository.py
│       ├── ticket_service.py
│       └── __init__.py
│
└── tests/
    ├── test_ai_service.py
    ├── test_repository.py
    ├── test_ticket_service.py
    └── test_validation.py
```

### Responsibilities of the main files

| File | Responsibility |
|---|---|
| `app.py`  | Display tickets, handle selection, show AI results, and collect corrections  |
| `models.py`  | Define and validate ticket data and AI responses  |
| `csv_loader.py`  | Read and validate CSV files  |
| `ai_service.py`  | Send tickets to OpenAI and validate responses  |
| `prompts.py`  | Store classification rules and reply-generation instructions  |
| `repository.py`  | Handle SQLite queries and updates  |
| `ticket_service.py`  | Coordinate single-ticket and multiple-ticket processing  |
| `config.py`  | Load API configuration and database settings  |


### single-ticket and batch processing
Reusable ticket-processing function, used by both workflows.

## LLM Requests
We have two choices:

| Approach                         | Advantages                                  | Disadvantages                                     |
|----------------------------------|---------------------------------------------|---------------------------------------------------|
| One API request per ticket       | Simple validation, independent errors, easier retry | More API calls                          |
| One API request for entire batch | Potentially fewer requests                  | Complicated outputs, context limits, harder error recovery |

Final decision: One request per ticket, processed sequentially.
For this small project, sequential processing is easier to test and debug. We can display a progress indicator while processing multiple tickets.
If the fifth ticket fails, the first four results remain saved and the sixth ticket can still be processed.

## Design the data model

We need to distinguish three types of information:
1. Original customer ticket
2. AI-generated results
3. Operator corrections

We use two SQLite tables:

`tickets`: Stores the original customer request.
* ticket_id (TEXT, primary key)
* subject
* description
* status: pending, completed, failed

`ticket_results`: Stores the AI analysis and any human corrections.
The ticket_id connects the two tables.
* ticket_id (TEXT, Primary/Foreign Key)
* ai_category
* ai_priority
* draft_reply
* operator_category (TEXT, nullable)
* operator_priority (TEXT, nullable)
* analyzed_at

The ticket_id connects the two tables.

#### Why separate AI predictions and human corrections?
This makes it possible to distinguish AI output from human decisions and identify classification errors. For display, the operator's value takes precedence when a correction exists.

For the initial version, we skip already-processed tickets during batch processing to avoid duplicate API charges. Failed tickets should remain available for retry.

## Error handling

We should decide what happens when something goes wrong before implementing the application.

| Failure | Expected behavior |
|---|---|
| Invalid CSV | Show validation errors; don't import invalid data |
| Duplicate ticket ID | Skip existing records and report them |
| Missing API key | Display a configuration error |
| OpenAI API failure | Mark the ticket as failed and allow retry |
| Invalid LLM output | Reject the response; don't save incomplete analysis |
| Database error | Display an error without reporting false success |
| One ticket fails during batch processing | Continue processing the remaining tickets |

CSV validation behavior:
* Reject files missing required columns.
* Reject rows with missing required values, with a clear error message.
* Skip duplicate ticket IDs, including duplicates already stored in SQLite.
* Report how many records were imported, skipped, or rejected.