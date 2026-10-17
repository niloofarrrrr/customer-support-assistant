## The Project's Objective
The AI Customer Support Assistant is a Python application that helps customer-support operators manage incoming support tickets. It imports tickets from CSV files, uses an LLM to classify each ticket by category and priority, generates a suggested response, and stores the results. Operators can review and correct AI-generated classifications before using the information.

## Two Important Principles
- The AI assists the operator rather than replacing them.
- The application generates draft responses but does not automatically send messages.

## Functional Requirements
Functional requirements describe what the application must be able to do.

| ID | Requirement | Expected Behavior |
| :--- | :--- | :--- |
| FR-01 | CSV Import | Import customer-support tickets from a CSV file. |
| FR-02 | Input Validation | Detect missing required fields and duplicate ticket IDs. |
| FR-03 | AI Classification | Use an LLM to assign a category and priority to a selected ticket. |
| FR-04 | Reply Generation | Use the LLM to generate a suggested customer response. |
| FR-05 | Output Validation | Validate AI-generated data against a predefined Pydantic schema. |
| FR-06 | Data Persistence | Save tickets and AI-generated results in SQLite. |
| FR-07 | Operator Review | Display tickets, classifications, priorities, and suggested responses. |
| FR-08 | Manual Correction | Allow the operator to change a ticket's category or priority and save the changes. |
| FR-09 | Error Handling | Display understandable errors when imports, API calls, or validation fail. |
| FR-10 | Single Ticket | The operator can select a single ticket and request AI analysis. |
| FR-11 | Multiple Tickets | The operator can select multiple tickets or all tickets and request AI analysis in one action. |

## Input and Output Data
### Input: Customer-support ticket

Three required CSV columns:
- ticket_id	(str)
- subject (str)
- description (str)

### Output: AI analysis
- category (Enum)
- priority (Enum): low, medium, high
- draft_reply (str)

Each result must remain associated with its original ticket_id.

We will also need an internal status to distinguish tickets that have not been analyzed from those that have been analyzed or encountered an error.


## Classification rules

We use a fixed set of categories and priorities rather than allowing the LLM to generate arbitrary labels. This makes the results more consistent and easier to validate, filter, and correct.


| Category | Meaning |
| ----- | ----- |
| **Billing** | Payments, invoices, refunds, subscriptions |
| **Technical Support** | Bugs, errors, application failures |
| **Account Access** | Login, password, account activation |
| **General Inquiry** | Product or service information |
| **Other** | Tickets that don't match the defined categories |


| Priority | Definition |
|---|---|
| High | Critical functionality unavailable, widespread outage, or urgent security-related issue |
| Medium | A customer is experiencing a meaningful problem, but it is not a critical incident |
| Low | General inquiries or minor issues |


We should also require the generated reply to avoid inventing company policies, promising refunds, or claiming that actions have already been completed.

## End-to-end application workflow

1. **Upload CSV**
   Operator imports sample tickets

2. **Validate input**
   Check required fields and duplicate IDs

3. **Store and display tickets**
   Operator selects a ticket for analysis

4. **Analyze using OpenAI API**
   Generate category, priority, and draft reply

5. **Validate and save AI results**
   Pydantic validation followed by SQLite persistence

6. **Human review and correction**
   Operator reviews results and saves corrections


## Acceptance criteria
Acceptance criteria describe the conditions that must be met before a feature can be considered complete.

Examples:

| Test | Expected result |
|---|---|
| Upload a valid CSV with 10 tickets | All 10 tickets are imported successfully |
| Upload a CSV with a missing required column  | A clear validation error appears |
| Analyze a billing complaint  | The AI returns a valid category, priority, and draft reply |
| AI returns a category outside the allowed list  | Validation rejects the result |
| Correct a category from Billing to Technical Support  | The corrected value is saved in SQLite |
| Restart the application  | Previously saved tickets and corrections remain available |
| OpenAI API returns an error  | The app displays an error and permits retry without losing the ticket |