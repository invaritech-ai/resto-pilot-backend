#!/usr/bin/env python3
"""
Score OCR benchmark results against gold standard.

Analyzes OCR accuracy for each model by comparing extracted data
against the ground truth in gold_standard.json.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any

GOLD_STANDARD_FILE = Path("gold_standard.json")
RESULTS_DIR = Path("ocr_benchmark_results")

def load_gold_standard() -> Dict[str, Any]:
    """Load the gold standard data"""
    with open(GOLD_STANDARD_FILE) as f:
        return json.load(f)

def extract_data_from_markdown(md_content: str) -> Dict[str, Any]:
    """Extract structured data from OCR markdown output"""
    data = {
        "supplier_name": None,
        "invoice_number": None,
        "invoice_date": None,
        "total": None,
        "line_items": []
    }

    # Extract supplier (usually in header or H1/H2)
    supplier_patterns = [
        r'#\s*([A-Za-z\s]+蘇蝦菜檔)',
        r'SoHaVegetables',
        r'SoHa\s*Vegetables',
    ]
    for pattern in supplier_patterns:
        match = re.search(pattern, md_content, re.IGNORECASE)
        if match:
            data["supplier_name"] = "SoHaVegetables"
            break

    # Extract invoice number
    invoice_patterns = [
        r'To:\s*[(\[]?(\d+)[)\]]?',
        r'Invoice.*?#?\s*:?\s*(\d+)',
        r'#\s*(\d+)',
    ]
    for pattern in invoice_patterns:
        match = re.search(pattern, md_content, re.IGNORECASE)
        if match:
            data["invoice_number"] = match.group(1)
            break

    # Extract date
    date_patterns = [
        r'Date:\s*(\d+/\d+)',
        r'日期.*?(\d+/\d+)',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, md_content, re.IGNORECASE)
        if match:
            data["invoice_date"] = match.group(1)
            break

    # Extract total
    total_patterns = [
        r'Total.*?[:\$\s]+(\d+)',
        r'合計.*?(\d+)',
        r'\*\*(\d+)\*\*\s*$',
    ]
    for pattern in total_patterns:
        match = re.search(pattern, md_content, re.IGNORECASE | re.MULTILINE)
        if match:
            data["total"] = int(match.group(1))
            break

    # Extract line items from table
    # Look for markdown tables or line-by-line items
    table_pattern = r'\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|'
    for match in re.finditer(table_pattern, md_content):
        desc, qty, price, amount = [m.strip() for m in match.groups()]

        # Skip header rows
        if 'DESCRIPTION' in desc or 'description' in desc.lower():
            continue
        if '---' in desc or 'Vegetables' in desc:
            continue

        # Parse quantity and unit
        qty_match = re.match(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?', qty)
        if qty_match:
            quantity = float(qty_match.group(1))
            unit = qty_match.group(2) if qty_match.group(2) else None
        else:
            continue

        # Parse unit price
        price_match = re.search(r'(\d+(?:\.\d+)?)', price)
        unit_price = float(price_match.group(1)) if price_match else None

        # Parse line total
        amount_match = re.search(r'(\d+(?:\.\d+)?)', amount)
        line_total = float(amount_match.group(1)) if amount_match else None

        data["line_items"].append({
            "description": desc.strip('- ').lower(),
            "quantity": quantity,
            "unit": unit,
            "unit_price": unit_price,
            "line_total": line_total
        })

    return data

def score_field(extracted, gold, field_name: str) -> tuple[bool, str]:
    """Score a single field"""
    if extracted is None:
        return False, f"{field_name}: Missing"

    if field_name in ["supplier_name", "invoice_date"]:
        match = str(extracted).lower() == str(gold).lower()
        return match, f"{field_name}: {'✓' if match else f'✗ ({extracted} vs {gold})'}"

    if field_name == "invoice_number":
        # Allow partial match for invoice number (81 vs 181)
        extracted_str = str(extracted)
        gold_str = str(gold)
        match = extracted_str in gold_str or gold_str in extracted_str
        return match, f"{field_name}: {'✓' if match else f'✗ ({extracted} vs {gold})'}"

    if field_name == "total":
        match = abs(float(extracted) - float(gold)) < 1
        return match, f"{field_name}: {'✓' if match else f'✗ ({extracted} vs {gold})'}"

    return False, f"{field_name}: Unknown"

def score_line_items(extracted_items: List[Dict], gold_items: List[Dict]) -> Dict[str, Any]:
    """Score line items extraction"""
    scores = {
        "count_match": len(extracted_items) == len(gold_items),
        "count": f"{len(extracted_items)}/{len(gold_items)}",
        "quantity_correct": 0,
        "unit_correct": 0,
        "price_correct": 0,
        "total_correct": 0,
        "description_matches": 0
    }

    # Match extracted items to gold items by description
    for gold_item in gold_items:
        gold_desc = gold_item["description"].lower()

        # Find best matching extracted item
        best_match = None
        best_score = 0

        for ext_item in extracted_items:
            ext_desc = ext_item["description"].lower()
            # Simple word overlap scoring
            gold_words = set(gold_desc.split())
            ext_words = set(ext_desc.split())
            overlap = len(gold_words & ext_words)
            if overlap > best_score:
                best_score = overlap
                best_match = ext_item

        if not best_match or best_score == 0:
            continue

        scores["description_matches"] += 1

        # Check quantity
        if best_match.get("quantity") and abs(best_match["quantity"] - gold_item["quantity"]) < 0.1:
            scores["quantity_correct"] += 1

        # Check unit
        if best_match.get("unit"):
            ext_unit = best_match["unit"].lower()
            gold_unit = gold_item["unit"].lower()
            if ext_unit == gold_unit or ext_unit in gold_unit or gold_unit in ext_unit:
                scores["unit_correct"] += 1

        # Check unit price
        if best_match.get("unit_price") and gold_item["unit_price"]:
            if abs(best_match["unit_price"] - gold_item["unit_price"]) < 1:
                scores["price_correct"] += 1

        # Check line total
        if best_match.get("line_total"):
            if abs(best_match["line_total"] - gold_item["line_total"]) < 1:
                scores["total_correct"] += 1

    total_items = len(gold_items)
    scores["accuracy"] = {
        "descriptions": f"{scores['description_matches']}/{total_items} ({100*scores['description_matches']/total_items:.0f}%)",
        "quantities": f"{scores['quantity_correct']}/{total_items} ({100*scores['quantity_correct']/total_items:.0f}%)",
        "units": f"{scores['unit_correct']}/{total_items} ({100*scores['unit_correct']/total_items:.0f}%)",
        "prices": f"{scores['price_correct']}/{total_items} ({100*scores['price_correct']/total_items:.0f}%)",
        "totals": f"{scores['total_correct']}/{total_items} ({100*scores['total_correct']/total_items:.0f}%)",
    }

    return scores

def score_model(md_file: Path, gold: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single model's output"""
    md_content = md_file.read_text()
    extracted = extract_data_from_markdown(md_content)

    scores = {
        "model": md_file.stem.replace('_20260217_005302', '').replace('_', ' '),
        "supplier": score_field(extracted["supplier_name"], gold["supplier_name"], "supplier")[0],
        "invoice_number": score_field(extracted["invoice_number"], gold["invoice_number"], "invoice_number")[0],
        "date": score_field(extracted["invoice_date"], gold["invoice_date"], "date")[0],
        "total": score_field(extracted["total"], gold["total"], "total")[0],
    }

    # Score line items
    line_item_scores = score_line_items(extracted["line_items"], gold["line_items"])
    scores.update({
        "line_item_count": line_item_scores["count"],
        "descriptions_matched": line_item_scores["description_matches"],
        "quantities_correct": line_item_scores["quantity_correct"],
        "units_correct": line_item_scores["unit_correct"],
        "prices_correct": line_item_scores["price_correct"],
        "totals_correct": line_item_scores["total_correct"],
    })

    # Overall accuracy score
    total_checks = 4  # supplier, invoice#, date, total
    correct = sum([scores["supplier"], scores["invoice_number"], scores["date"], scores["total"]])

    # Add line item accuracy
    total_items = len(gold["line_items"])
    total_checks += total_items * 4  # qty, unit, price, total per item
    correct += (
        line_item_scores["quantity_correct"] +
        line_item_scores["unit_correct"] +
        line_item_scores["price_correct"] +
        line_item_scores["total_correct"]
    )

    scores["overall_accuracy"] = f"{100*correct/total_checks:.1f}%"

    return scores

def main():
    print("="*80)
    print("OCR ACCURACY SCORING")
    print("="*80)

    # Load gold standard
    gold = load_gold_standard()
    print(f"\n📋 Gold Standard:")
    print(f"   Supplier: {gold['supplier_name']}")
    print(f"   Invoice #: {gold['invoice_number']}")
    print(f"   Date: {gold['invoice_date']}")
    print(f"   Line Items: {len(gold['line_items'])}")
    print(f"   Total: {gold['total']}")

    # Find all markdown files
    md_files = sorted(RESULTS_DIR.glob("*.md"))

    if not md_files:
        print(f"\n❌ No markdown files found in {RESULTS_DIR}")
        return

    print(f"\n🧪 Scoring {len(md_files)} models...\n")

    # Score each model
    results = []
    for md_file in md_files:
        scores = score_model(md_file, gold)
        results.append(scores)

        # Print summary
        model = scores["model"][:30]
        acc = scores["overall_accuracy"]
        print(f"{model:<32} {acc:>6} - Items: {scores['line_item_count']}")

    # Print detailed comparison
    print("\n" + "="*80)
    print("DETAILED ACCURACY BREAKDOWN")
    print("="*80)
    print(f"{'Model':<20} {'Sup':<5} {'Inv#':<5} {'Date':<5} {'Tot':<5} {'Items':<8} {'Qty':<5} {'Unit':<5} {'Price':<5} {'Accuracy':<10}")
    print("-"*80)

    for r in sorted(results, key=lambda x: float(x['overall_accuracy'].rstrip('%')), reverse=True):
        model = r['model'][:19]
        sup = '✓' if r['supplier'] else '✗'
        inv = '✓' if r['invoice_number'] else '✗'
        date = '✓' if r['date'] else '✗'
        tot = '✓' if r['total'] else '✗'
        items = r['line_item_count']
        qty = f"{r['quantities_correct']}/14"
        unit = f"{r['units_correct']}/14"
        price = f"{r['prices_correct']}/14"
        acc = r['overall_accuracy']

        print(f"{model:<20} {sup:<5} {inv:<5} {date:<5} {tot:<5} {items:<8} {qty:<5} {unit:<5} {price:<5} {acc:<10}")

    print("\n✨ Done!")

if __name__ == "__main__":
    main()
