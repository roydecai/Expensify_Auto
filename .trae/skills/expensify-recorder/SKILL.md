---
name: "expensify-recorder"
description: "Records a single expense into Expensify via API. Invoke when all expense fields are already known and need to be written; do not infer or OCR."
---

# Expensify Recorder

## Scope
- Only writes expenses into Expensify.
- Invoke only when the caller already has final values for: employeeEmail, merchant, amount, currency, created date, category, description.
- Does not infer Category, does not parse receipts/OCR, does not guess missing data.

## Required Inputs
- employeeEmail: string (employee in the policy)
- merchant: string
- amount: number (major units, e.g. 10.50 USD)
- currency: string (ISO, e.g. USD/CNY/HKD/SGD)
- created: string (YYYY-MM-DD)
- category: string (final category name)
- description: string (final description text)

## Optional Inputs
- report:
  - createNew: boolean
  - title: string
  - policyID: string
- reportID: string (if you want to attach expense to an existing report; only if supported by your integration settings)

## Validation Rules
- Reject if any required field is empty.
- Reject if amount is not a finite number or is 0.
- Reject if created is not YYYY-MM-DD.
- Reject if currency is not 3 letters.
- Reject if category is missing.
- Convert amount to cents: `amount_cents = round(amount * 100)`.

## Execution
### 1) (Optional) Create Report
If `report.createNew=true`, call expensify-communicator Create Report:

```json
{
  "type": "create",
  "credentials": { "partnerUserID": "<USER_ID>", "partnerUserSecret": "<USER_SECRET>" },
  "inputSettings": {
    "type": "report",
    "report": { "title": "<TITLE>", "policyID": "<POLICY_ID>" }
  }
}
```

### 2) Create Expense
Call expensify-communicator Create Expense:

```json
{
  "type": "create",
  "credentials": { "partnerUserID": "<USER_ID>", "partnerUserSecret": "<USER_SECRET>" },
  "inputSettings": {
    "type": "expenses",
    "employeeEmail": "<EMPLOYEE_EMAIL>",
    "transactionList": [
      {
        "merchant": "<MERCHANT>",
        "amount": 1050,
        "currency": "USD",
        "created": "2026-01-31",
        "category": "<CATEGORY>"
      }
    ]
  }
}
```

## Error Handling
- Missing credentials: stop and return a single actionable error message; do not attempt any request.
- Validation errors: return which field failed and the accepted format; do not guess corrections.
- API errors:
  - Auth/permission errors: return the API error and stop (no retry).
  - Network/transient errors: retry up to 2 times with backoff (1s, 3s); if still failing, return the last error.
  - Partial success: if the API response indicates some transactions failed, return per-transaction failures and do not re-submit failed ones automatically.

## Limitations
- Receipt upload is not specified in expensify-communicator; if receipt attachment is required, return an explicit “unsupported” error and ask for the expected API method/fields.
