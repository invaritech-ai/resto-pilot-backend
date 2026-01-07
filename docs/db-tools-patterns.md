# Database Tools Architecture & Patterns

## Overview

The database tools system provides intent-based operations for the AI agent. It follows a clear separation:

- **Tools = Capabilities**: What operations can be performed (create restaurant, list staff, etc.)
- **Policies = Filtering**: Who can perform those operations (owners vs staff, restaurant-scoped access, etc.)

## Architecture

### Two-Layer Permission System

1. **Direct Checks** (Simple, fast):
   - `is_restaurant_owner()` - Checks if user is actually an owner in the database
   - `has_restaurant_access()` - Checks if user has any access to a restaurant
   - Used for simple, straightforward permission checks

2. **Policy Checks** (Centralized, flexible):
   - `check_policy_permission()` - Checks against the policy allowlist (`db_allowlist.py`)
   - Used for complex tables where you want centralized policy management
   - Cross-references with the policy system for consistency

### When to Use Which

**Use Direct Checks** (`is_restaurant_owner`, `has_restaurant_access`):
- Simple operations with clear ownership rules
- When the permission logic is straightforward (owner can do X, staff can do Y)
- Examples: `update_restaurant`, `revoke_staff_access`, `create_invite_code`

**Use Policy Checks** (`check_policy_permission`):
- Complex tables with multiple permission levels
- When you want centralized policy management in `db_allowlist.py`
- When permissions might change frequently or have complex scoping
- Examples: Future `inventory_items`, `suppliers`, `pricing`, `costs`, `wastage`

## Current Tool Structure

```
app/ai/db_tools/
├── __init__.py          # Exports create_db_tools
├── base.py              # Shared utilities + policy checker
├── profile.py           # Profile tools (uses direct checks)
├── restaurants.py       # Restaurant tools (uses direct checks)
├── staff.py             # Staff management (uses direct checks)
└── invites.py           # Invite codes (uses direct checks)
```

## Pattern: Simple Tool (Direct Checks)

For simple operations, use direct permission checks:

```python
# app/ai/db_tools/restaurants.py
from .base import is_restaurant_owner

def update_restaurant(args: dict[str, Any]) -> str:
    restaurant_id_str = args.get("restaurant_id", "").strip()
    # ... validation ...
    
    # Direct check: Is user actually an owner?
    if not is_restaurant_owner(db, user_id, restaurant_id):
        return "Error: Only restaurant owners can update restaurant details."
    
    # Proceed with update...
```

**When to use**: Simple ownership checks, clear permission rules.

## Pattern: Complex Tool (Policy Checks)

For complex tables, use policy checks for centralized management:

```python
# app/ai/db_tools/inventory.py
from .base import check_policy_permission

def update_inventory_item(args: dict[str, Any]) -> str:
    restaurant_id_str = args.get("restaurant_id", "").strip()
    # ... validation ...
    
    # Policy check: Is this role allowed to update inventory?
    allowed, error = check_policy_permission(
        table="inventory_items",
        crud="update",
        actor_role=actor_role,
        restaurant_id=restaurant_id_str,
        restaurant_roles=restaurant_roles,
    )
    if not allowed:
        return f"Error: {error}"
    
    # Proceed with update...
```

**When to use**: Complex permissions, multiple roles, centralized policy management.

## Adding New Tools

### Step 1: Create Module File

Create `app/ai/db_tools/inventory.py`:

```python
"""
Inventory management tools.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai.tools import Tool
from .base import check_policy_permission, format_date

logger = logging.getLogger(__name__)


def create_inventory_tools(
    *,
    db: Session,
    user_id: Any,
    actor_role: str | None = None,
    restaurant_roles: dict[str, str] | None = None,
) -> dict[str, Tool]:
    """Create inventory management tools."""

    def list_inventory_items(args: dict[str, Any]) -> str:
        """List inventory items for a restaurant."""
        restaurant_id_str = args.get("restaurant_id", "").strip()
        if not restaurant_id_str:
            return "Error: restaurant_id is required."
        
        # Check policy
        allowed, error = check_policy_permission(
            table="inventory_items",
            crud="read",
            actor_role=actor_role or "staff",
            restaurant_id=restaurant_id_str,
            restaurant_roles=restaurant_roles or {},
        )
        if not allowed:
            return f"Error: {error}"
        
        # ... implementation ...
        return json.dumps([...], indent=2)

    return {
        "list_inventory_items": Tool(
            name="list_inventory_items",
            description="List inventory items for a restaurant.",
            parameters={...},
            handler=list_inventory_items,
        ),
    }
```

### Step 2: Add to Policy Allowlist

Update `app/policies/db_allowlist.py`:

```python
ROLE_OWNER: {
    # ... existing tables ...
    "inventory_items": {
        "read": {
            "columns": ["id", "name", "quantity", "unit", "restaurant_id"],
            "scope": SCOPE_RESTAURANT_OWNER,
        },
        "update": {
            "columns": ["name", "quantity", "unit"],
            "scope": SCOPE_RESTAURANT_OWNER,
        },
    },
},
ROLE_STAFF: {
    # ... existing tables ...
    "inventory_items": {
        "read": {
            "columns": ["id", "name", "quantity", "unit"],
            "scope": SCOPE_RESTAURANT_MEMBER,
        },
    },
},
```

### Step 3: Register in Base

Update `app/ai/db_tools/base.py`:

```python
from . import invites, inventory, profile, restaurants, staff

def create_db_tools(...):
    # ... existing tools ...
    inventory_tools = inventory.create_inventory_tools(
        db=db, user_id=user_id, actor_role=actor_role, restaurant_roles=restaurant_roles
    )
    
    all_tools.update(inventory_tools)
    return all_tools
```

## Permission Patterns

### Pattern 1: Owner-Only Operations

**Direct Check**:
```python
if not is_restaurant_owner(db, user_id, restaurant_id):
    return "Error: Only restaurant owners can perform this action."
```

**Policy Check**:
```python
allowed, error = check_policy_permission(
    table="restaurants",
    crud="update",
    actor_role=actor_role,
    restaurant_id=restaurant_id_str,
    restaurant_roles=restaurant_roles,
)
```

### Pattern 2: Staff Can Read, Owner Can Write

**Direct Check**:
```python
# For read: any member can access
if not has_restaurant_access(db, user_id, restaurant_id):
    return "Error: You don't have access to this restaurant."

# For write: only owners
if not is_restaurant_owner(db, user_id, restaurant_id):
    return "Error: Only restaurant owners can update this."
```

**Policy Check**:
```python
# Policy automatically handles role-based access
allowed, error = check_policy_permission(
    table="inventory_items",
    crud="update",  # Policy will check if staff can update
    actor_role=actor_role,
    restaurant_id=restaurant_id_str,
    restaurant_roles=restaurant_roles,
)
```

### Pattern 3: Self-Scoped Operations

**Direct Check**:
```python
# Already handled - user_id is passed to tool factory
# Tools automatically operate on the current user
```

**Policy Check**:
```python
# For self-scoped operations, policy handles it automatically
allowed, error = check_policy_permission(
    table="users",
    crud="update",
    actor_role=actor_role,
    # No restaurant_id needed for self-scoped
)
```

## Best Practices

1. **Start Simple**: Use direct checks for straightforward operations
2. **Use Policies for Complex**: When you have multiple permission levels, use policy checks
3. **Keep Consistent**: If a table is in the policy allowlist, use policy checks for it
4. **Document Permissions**: Add comments explaining permission logic
5. **Error Messages**: Always return user-friendly error messages

## Current Tools & Their Patterns

| Tool | Pattern | Reason |
|------|---------|--------|
| `get_my_profile` | Direct (self-scoped) | Simple, always self |
| `update_my_profile` | Direct (self-scoped) | Simple, always self |
| `list_my_restaurants` | Direct | Simple membership check |
| `create_restaurant` | Direct | Anyone can create |
| `update_restaurant` | Direct (`is_restaurant_owner`) | Simple ownership check |
| `list_staff` | Direct (`has_restaurant_access`) | Simple membership check |
| `revoke_staff_access` | Direct (`is_restaurant_owner`) | Simple ownership check |
| `create_invite_code` | Direct (`is_restaurant_owner`) | Simple ownership check |

## Future Tools (Recommended Patterns)

| Tool | Recommended Pattern | Reason |
|------|---------------------|--------|
| `inventory_items` | Policy check | Complex, multiple roles, centralized management |
| `suppliers` | Policy check | Complex relationships, restaurant-scoped |
| `pricing` | Policy check | Complex business rules, role-based access |
| `costs` | Policy check | Financial data, strict permissions |
| `wastage` | Policy check | Analytics, role-based reporting |

## Summary

- **Tools define capabilities** (what can be done)
- **Policies define filtering** (who can do it)
- **Direct checks** for simple operations
- **Policy checks** for complex, centralized management
- Both systems work together - choose the right tool for the job

