---
name: "expensify-communicator"
description: "Handles low-level Expensify API operations: query, create reports/expenses, download data. Pure execution tool with NO subjective judgment. Invoke for any Expensify data I/O."
---

# Expensify Communicator

This skill provides the technical specifications for interacting with the Expensify Integration Server API. It is designed for pure execution of tasks without subjective analysis.

**Endpoint:** `https://integrations.expensify.com/Integration-Server/ExpensifyIntegrations`
**Method:** POST
**Parameter:** `requestJobDescription` (JSON)

## 1. Authentication
All requests require credentials.
```json
"credentials": {
    "partnerUserID": "<USER_ID>",
    "partnerUserSecret": "<USER_SECRET>"
}
```

## 2. Download/Export Reports (Query)
Use this to fetch expense data. Requires a Freemarker template.

**Job Type:** `file`
**Input Settings Type:** `combinedReportData`

**Payload:**
```json
{
    "type": "file",
    "credentials": { ... },
    "onReceive": {
        "immediateResponse": ["returnRandomFileName"]
    },
    "inputSettings": {
        "type": "combinedReportData",
        "filters": {
            "reportIDList": "ID1,ID2", 
            "startDate": "YYYY-MM-DD",
            "endDate": "YYYY-MM-DD",
            "markedAsExported": "Expensify Export" // Optional filter
        }
    },
    "outputSettings": {
        "fileExtension": "csv"
    }
}
```
**Template:** Must be passed as `template` parameter (URL encoded).
*Example Template:*
```freemarker
<#if addHeader == true>Merchant,Amount,Category<#lt></#if>
<#list reports as report>
    <#list report.transactionList as expense>
        ${expense.merchant},${expense.amount},${expense.category}<#lt>
    </#list>
</#list>
```

## 3. Create Report
Creates a new report container.

**Job Type:** `create`
**Input Settings Type:** `report`

**Payload:**
```json
{
    "type": "create",
    "credentials": { ... },
    "inputSettings": {
        "type": "report",
        "report": {
            "title": "Report Title",
            "policyID": "<POLICY_ID>"
        }
    }
}
```

## 4. Create Expense
Creates a transaction (expense). Note: Requires specific domain permissions.

**Job Type:** `create`
**Input Settings Type:** `expenses`

**Payload:**
```json
{
    "type": "create",
    "credentials": { ... },
    "inputSettings": {
        "type": "expenses",
        "employeeEmail": "employee@domain.com",
        "transactionList": [
            {
                "merchant": "Merchant Name",
                "amount": 1000, // In cents (e.g. $10.00)
                "currency": "USD",
                "created": "YYYY-MM-DD",
                "category": "Travel"
            }
        ]
    }
}
```

## 5. Execution Strategy
- Use `curl` for execution.
- Always validate `partnerUserID` and `partnerUserSecret` availability before running.
- For "download", handle the template file creation (temp file) or inline URL encoding.
- **NO JUDGMENT**: Execute exactly what is requested. If data is missing, report error, do not guess.
