#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def extract_json_blocks(text):
    blocks = []
    collecting = False
    buf = []
    brace_depth = 0

    for line in text.splitlines():
        if line.strip() == "[JSON]":
            collecting = True
            buf = []
            brace_depth = 0
            continue
        if not collecting:
            continue
        buf.append(line)
        brace_depth += line.count("{") - line.count("}")
        if brace_depth == 0 and buf:
            json_text = "\n".join(buf).strip()
            if json_text:
                try:
                    blocks.append(json.loads(json_text))
                except json.JSONDecodeError:
                    pass
            collecting = False
            buf = []

    return blocks


def build_markdown_tables(blocks, page=None):
    out = []
    for idx, obj in enumerate(blocks, start=1):
        if page is not None and idx != page:
            continue
        product_info = obj.get("product_info") or []
        if not product_info:
            continue
        columns = list(product_info[0].keys())
        df = pd.DataFrame(product_info)
        df = df.reindex(columns=columns)
        out.append(f"## Page {idx}")
        out.append(df.to_markdown(index=False))
        out.append("")
    return "\n".join(out).strip()


def main():
    parser = argparse.ArgumentParser(
        description="Render product_info JSON blocks as markdown tables."
    )
    parser.add_argument(
        "--log",
        default="/tmp/6page_flow.log",
        help="Path to flow log containing [JSON] blocks.",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Optional 1-based page number to render.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output path; otherwise print to stdout.",
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        raise SystemExit(f"Log not found: {log_path}")

    text = log_path.read_text()
    blocks = extract_json_blocks(text)
    if not blocks:
        raise SystemExit("No [JSON] blocks found in log.")

    try:
        markdown = build_markdown_tables(blocks, page=args.page)
    except ImportError as exc:
        raise SystemExit(
            "Missing optional dependency for to_markdown(). "
            "Install with: uv pip install tabulate"
        ) from exc

    if args.out:
        Path(args.out).write_text(markdown)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
