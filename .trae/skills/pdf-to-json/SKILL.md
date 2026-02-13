---
name: "pdf-to-json"
description: "Defines validation criteria and output protocols for PDF-extracted JSON. Invoke when generating JSON fixes or proposing patterns.json/code changes for the extraction pipeline."
---

# PDF-to-JSON Contract (Validation + Fix + Evolver Protocol)

This skill is the single source of truth for:

- What constitutes a “valid and reasonable” extracted JSON for this project
- How to repair an extracted JSON (Phase 1: data healing)
- How to propose safe, controlled extraction-rule changes (Phase 2: logic healing)

Repository context:

- Extraction code lives under `src/invoice_processor/`
- Validation spec is `src/invoice_processor/pdf_to_json_spec_v0_3_1.json`
- Patterns are in `src/invoice_processor/patterns.json`

## 1) Ground Rules (Always)

- Never fabricate facts not supported by `extracted_text`.
- If evidence is missing or ambiguous, keep the original value or leave it empty/null (even if validation may fail again).
- Do not change `document_type`.
- Use evidence-driven edits: when you change a field, you must be able to point to a snippet in `extracted_text` that supports it.
- Output must follow the requested protocol exactly (no Markdown, no explanations, no code blocks) unless the protocol explicitly allows it.

## 2) Document Types

Allowed values (see spec): `VAT_invoice`, `VAT_invalid_invoice`, `bank_receipt`, `tax_certificate`, `unknown`.

If `document_type = unknown`, treat it as human review required.

## 3) Field Expectations (High-Level)

The validator spec is the deterministic source of truth. This skill adds semantic expectations that help avoid common extraction mistakes.

### Common fields (across types)

- `date`: must be `YYYY-MM-DD` (normalize from `YYYY年M月D日`, `YYYY/M/D`, etc.)
- `uid`: stable identifier from the document (invoice number / transaction id / receipt no.)
- `amount` / `total_amount`: should be a decimal string with 2 digits (e.g., `3280.29`)
- `payer`: the paying entity name (not an account number, not a bank name)

### VAT_invoice / VAT_invalid_invoice

- `payer`: should correspond to the buyer/purchaser (“购买方/购方/买方”) name
- `seller`: should correspond to the seller (“销售方/销方/卖方”) name
- `seller_tax_id`: should be a tax id (usually alphanumeric)
- `buyer_tax_id`: optional if not clearly present
- `total_amount`: should be the “价税合计(小写)” amount where available

### bank_receipt

- `payer`: 付款方/付款人/户名（付款侧主体）
- `payee`: 收款方/收款人/对方户名（收款侧主体）
- `uid`: 交易流水号/回单号/凭证号等
- Avoid swapping `payer` and `payee`. If evidence is conflicting, do not guess.

### tax_certificate

- `payer`: 纳税人名称
- `payer_tax_id`: 纳税人识别号（optional but preferred if present)
- `tax_authority`: issuing authority if present
- `uid`: 税票号码/票号等

## 4) Phase 1 Protocol — Data Fix Output

Invoke when:

- You are asked to repair a single extracted JSON that failed validation (`FAIL_LLM`)
- You are given `extracted_text_truncated`, validation errors, and a `current_json`

Output format:

- Output ONLY the repaired JSON object (a single JSON object)
- No extra keys unless they already exist in `current_json` or are required by the spec for this `document_type`
- Preserve `document_type` exactly
- Preserve `extracted_text` exactly if present in the original payload (do not rewrite it)

Repair strategy:

1. Fix formatting issues first (date, amount normalization, trimming whitespace rules).
2. Fill missing required fields only if `extracted_text` contains explicit evidence.
3. If the validator complains about name whitespace policy, remove whitespace outside parentheses only.
4. If multiple candidates exist (e.g., multiple amounts), prefer the one explicitly labeled as total/合计/价税合计/小写 for invoices.

## 5) Phase 2 Protocol — Evolver Proposal Output (Patterns First)

Invoke when:

- You are asked to propose a change to extraction rules to prevent repeated failures
- The safest first attempt is to evolve patterns in `src/invoice_processor/patterns.json`

Critical safety constraint:

- You MUST NOT modify or reorder existing `doc_patterns` or `field_patterns`.
- You may only append new rules under an overrides subtree:
  - `field_patterns_overrides[doc_type][field]` as a list of rule objects

Rule object schema (proposal):

```json
{
  "id": "br_payer_v1_001",
  "priority": 500,
  "regex": "付款方[:：]?\\s*([^\\n\\r\\t\\f\\v]{5,60})",
  "enabled": true,
  "notes": "Short reason; mention evidence labels used."
}
```

Priority rules:

- Higher `priority` runs earlier than lower `priority` within overrides (implementation detail for the executor).
- Do not reuse an existing `id`.
- Use coarse steps (e.g., 100, 200, 300…) to reduce churn.

Proposal output format (single JSON object):

```json
{
  "kind": "patterns_override_proposal",
  "target": "src/invoice_processor/patterns.json",
  "doc_type": "bank_receipt",
  "field": "payer",
  "rule": {
    "id": "br_payer_v1_001",
    "priority": 500,
    "regex": "付款方[:：]?\\s*([^\\n\\r\\t\\f\\v]{5,60})",
    "enabled": true,
    "notes": "..."
  },
  "evidence": [
    {
      "quote": "付款方：XXX有限公司",
      "reason": "Shows label-to-value pattern for payer."
    }
  ],
  "expected_effect": "Fixes payer extraction for bank receipts with explicit label '付款方'.",
  "risk": "Low",
  "tests_to_add_or_update": [
    "tests/regression_samples/sample_001_truth.json"
  ]
}
```

## 6) Phase 2 Protocol — Code Patch Proposal Output (Controlled Scope)

Invoke when:

- Patterns cannot safely solve the issue (layout logic, cross-line parsing, multi-column handling)
- A minimal, localized code change is required

Constraints:

- Patch must be minimal, localized, and only touch whitelisted extraction files under `src/invoice_processor/`.
- Prefer function-level replacement rather than arbitrary diffs.
- The patch must be syntactically valid Python.

Output format (single JSON object):

```json
{
  "kind": "code_patch_proposal",
  "target": "src/invoice_processor/pdf_extractor.py",
  "scope": "function_replace",
  "function_name": "extract_bank_receipt_fields",
  "new_function_source": "def extract_bank_receipt_fields(...):\\n    ...\\n",
  "evidence": [
    {
      "quote": "收款方：...",
      "reason": "Shows mis-parsing due to split columns."
    }
  ],
  "expected_effect": "Improves bank receipt parsing under multi-column layout.",
  "risk": "Medium",
  "requires_human_review": true
}
```

## 7) Evidence Requirements

Evidence is mandatory for:

- Any new rule proposal (`patterns_override_proposal`)
- Any code patch proposal (`code_patch_proposal`)

Evidence must be a direct quote from `extracted_text` (can be truncated) and must show:

- The label (e.g., 付款方/收款方/发票号码/价税合计)
- The value candidate

## 8) Common Failure Patterns (Checklist)

- Names mistakenly extracted as bank names, account numbers, or transaction channels
- `uid` extracted from “No:” or “流水号” with trailing symbols (should be trimmed/normalized)
- Amount extracted from non-total lines (e.g., fee line, tax line, subtotal)
- Buyer/seller swapped in invoices due to column splits
- Multi-column PDF lines merged incorrectly (requires controlled code fix)
