# UPI Conversational Insight Prototype

This is a full working prototype that:
- loads `upi_transactions_2024.csv`
- accepts natural-language business questions
- computes answers from the dataset
- returns explainable text + chart + table
- supports follow-up context in a chat session

## Run

1. Ensure Python 3.9+ is installed.
2. From project folder:

```bash
python app.py
```

3. Open:

`http://127.0.0.1:8000`

## Supported Query Types

- Descriptive: `What is the average transaction amount for bill payment?`
- Comparative: `How do failure rates compare between Android and iOS users?`
- Temporal: `What are peak transaction hours for Grocery on weekends?`
- Segmentation: `Which age group uses P2P transfers most frequently?`
- Correlation style: `Is there a relationship between network type and transaction success?`
- Risk analysis: `What percentage of high-value transactions are flagged for review?`

## Architecture

- `app.py`
  - Data loading and normalization
  - Rule-based NL query understanding (intent + entities + filters)
  - Aggregation/statistics engine
  - Chat context memory for follow-up questions
  - HTTP API endpoints (`/ask`, `/health`)
- `web/index.html`
  - Chat interface
- `web/app.js`
  - Sends question to backend
  - Renders answer text, chart, and table
- `web/styles.css`
  - Responsive UI styling

## API

- `GET /health` -> basic dataset health
- `POST /ask`

Request:

```json
{ "question": "Which transaction type has the highest failure rate?" }
```

Response includes:
- `answer_text`
- `chart` (Chart.js config-like payload)
- `table` (top rows + metrics)
- `explanation`

## Notes

- Uses only the provided CSV as data source.
- No hardcoded static answers; all responses are computed from data.
- If your CSV schema changes, update aliases/filters in `QueryEngine`.

