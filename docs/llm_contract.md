# OCR / Parser LLM Contract

This project uses LLMs in the file-ingestion pipeline only.

## Two-stage extraction
1. Vision OCR stage (`ocr_page_to_markdown`)
   - input: image bytes/base64
   - output: markdown text
2. Parser stage (`parse_invoice` or `parse_price_list`)
   - input: markdown text
   - output: normalized JSON payload

## Parser outputs
### Invoice
Top-level fields include:
- `supplier`
- `supplier_contact_name`
- `supplier_phone`
- `supplier_email`
- `invoice_date`
- `invoice_number`
- `currency`
- `line_items[]` with `name`, `qty`, `unit`, `unit_price`, `amount`

### Price list
Top-level fields include:
- `supplier`
- `supplier_contact_name`
- `supplier_phone`
- `supplier_email`
- `lead_time`
- `effective_date`
- `currency`
- `line_items[]` with `name`, `unit`, `unit_price`

## Validation behavior
- Missing/invalid numeric fields are normalized to `None` instead of crashing.
- Empty item names are dropped.
- Invoice items with missing `qty`/`unit_price` are retained for user review.
- JSON parsing failures raise `ParseError`.

## Telemetry callback contract
LLM calls may emit callback payloads:
- `purpose` (e.g. `ocr_page_to_markdown`, `parse_invoice`)
- `model`
- `upstream_id` (if available)
- `usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`)
- `error` (for failed calls)

Worker tasks persist these into `llm_calls` when session context is available.
