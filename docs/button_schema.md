# Button Callback Schema

Telegram callback payload format is colon-delimited with strict 64-byte constraints.

## Wire format
`action:param1:param2:...`

UUIDs are encoded as 32-char hex without dashes to stay compact.

## Active callback actions
- `doc_type:{staging_hex}:{invoice|price_list}`
- `conf_u:{staging_hex}`
- `del_u:{staging_hex}`
- `set_sup:{staging_hex}:{supplier_ref}`
  - `supplier_ref` may be UUID hex (legacy) or ranked index (compact)
- `new_sup:{staging_hex}`
- `use_match:{staging_hex}:{idx}:{match_ref}`
  - `match_ref` may be UUID hex (legacy) or ranked index (compact)
- `mk_item:{staging_hex}:{idx}`
- `skip_item:{staging_hex}:{idx}`
- `rev_p:{staging_hex}:{page}`
- `list_p:{list_type}:{page}`
- `ed_row:{staging_hex}:{idx}`
- `ed_fld:{staging_hex}:{idx}:{field}`
- `open_u:{staging_hex}`
- `pick_cur:{staging_hex}`
- `set_cur:{staging_hex}:{currency}`

## Behavioral guardrails
1. Button handlers are deterministic and do not invoke LLMs directly.
2. Unknown/expired references return callback alerts instead of hard failures.
3. For paginated command lists, only the latest list message is considered active.
4. Compact callback payloads are preferred where UUID-rich payloads risk Telegram size limits.
