#!/usr/bin/env python3
"""Check extraction results from file_processing_steps table."""

from app.db.session import get_db
from app.db.models.file_processing_steps import FileProcessingSteps
from uuid import UUID
import json

db_gen = get_db()
db = next(db_gen)

run_id = UUID('9ec9ef4b-0599-4229-8fc8-efea181f84db')

# Get extraction steps
extraction_steps = db.query(FileProcessingSteps).filter_by(
    run_id=run_id,
    stage='extraction'
).order_by(FileProcessingSteps.page_index).all()

print(f'Found {len(extraction_steps)} extraction steps\n')

total_items = 0
total_line_items = 0

for step in extraction_steps:
    print(f'Page {step.page_index}: status={step.status}')

    # Parse JSON from the step
    raw_json = step.output_text or '{}'

    # Clean JSON (same logic as merge_page_results)
    if raw_json.strip().startswith('```'):
        lines = raw_json.strip().split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        raw_json = '\n'.join(lines)

    raw_json = raw_json.replace('<|begin_of_box|>', '').replace('<|end_of_box|>', '')

    try:
        data = json.loads(raw_json.strip())
        items_count = len(data.get('items', []))
        line_items_count = len(data.get('line_items', []))
        total_items += items_count
        total_line_items += line_items_count
        print(f'  Items: {items_count}, Line items: {line_items_count}')

        # Show first item if available
        if data.get('items'):
            item = data['items'][0]
            print(f'    Sample: {item}')
    except json.JSONDecodeError as e:
        print(f'  ERROR parsing JSON: {e}')
        print(f'  First 200 chars: {raw_json[:200]}')
    print()

print(f'\nTOTAL across all pages:')
print(f'  Items: {total_items}')
print(f'  Line items: {total_line_items}')

db.close()
