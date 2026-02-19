"""
Emoji taxonomy service — maps item keywords to appropriate emojis.

Provides consistent visual grammar across inventory, price, and product displays.
"""

from __future__ import annotations

import re
from typing import ClassVar


class EmojiTaxonomy:
    """Maps item keywords to category emojis."""

    # Primary mapping: keyword → emoji
    # Keywords are matched case-insensitively
    _CATEGORY_MAP: ClassVar[dict[str, str]] = {
        # Meat & poultry
        "chicken": "🐔",
        "poultry": "🐔",
        "duck": "🦆",
        "turkey": "🦃",
        "lamb": "🐑",
        "beef": "🥩",
        "pork": "🥩",
        "steak": "🥩",
        "ribs": "🥩",
        "mince": "🥩",
        "sausage": "🌭",
        "bacon": "🥓",
        "ham": "🍖",
        
        # Fish & seafood
        "fish": "🐟",
        "salmon": "🐟",
        "tuna": "🐟",
        "cod": "🐟",
        "prawn": "🦐",
        "shrimp": "🦐",
        "crab": "🦀",
        "lobster": "🦞",
        "oyster": "🦪",
        "mussel": "🦪",
        "clam": "🦪",
        
        # Dairy
        "yoghurt": "🧀",
        "yogurt": "🧀",
        "cheese": "🧀",
        "milk": "🥛",
        "cream": "🥛",
        "butter": "🧈",
        "egg": "🥚",
        "eggs": "🥚",
        
        # Oil & vinegar
        "oil": "🫙",
        "olive oil": "🫙",
        "vinegar": "🫙",
        "balsamic": "🫙",
        
        # Vegetables
        "tomato": "🍅",
        "potato": "🥔",
        "onion": "🧅",
        "garlic": "🧄",
        "carrot": "🥕",
        "lettuce": "🥬",
        "spinach": "🥬",
        "kale": "🥬",
        "broccoli": "🥦",
        "cauliflower": "🥦",
        "pepper": "🫑",
        "bell pepper": "🫑",
        
        # Fruits
        "apple": "🍎",
        "banana": "🍌",
        "orange": "🍊",
        "lemon": "🍋",
        "lime": "🍋",
        "berry": "🫐",
        "strawberry": "🍓",
        "grape": "🍇",
        
        # Grains & bread
        "bread": "🍞",
        "rice": "🍚",
        "pasta": "🍝",
        "noodle": "🍜",
        "flour": "🌾",
        "oat": "🌾",
        
        # Beverages
        "water": "💧",
        "soda": "🥤",
        "juice": "🧃",
        "coffee": "☕",
        "tea": "🍵",
        "wine": "🍷",
        "beer": "🍺",
        
        # Spices & herbs
        "salt": "🧂",
        "pepper": "🌶️",
        "chili": "🌶️",
        "spice": "🌿",
        "herb": "🌿",
        "basil": "🌿",
        "parsley": "🌿",
        "coriander": "🌿",
        
        # Miscellaneous
        "sugar": "🍬",
        "honey": "🍯",
        "chocolate": "🍫",
        "ice": "🧊",
        "sauce": "🥫",
        "ketchup": "🥫",
        "mayo": "🥫",
        "mustard": "🥫",
    }
    
    # Fallback categories for broader matching
    _CATEGORY_PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = [
        (re.compile(r".*\b(chicken|poultry|duck|turkey)\b.*", re.IGNORECASE), "🐔"),
        (re.compile(r".*\b(lamb|beef|pork|steak|ribs|mince)\b.*", re.IGNORECASE), "🥩"),
        (re.compile(r".*\b(fish|salmon|tuna|cod|prawn|shrimp|crab|lobster)\b.*", re.IGNORECASE), "🐟"),
        (re.compile(r".*\b(yoghurt|yogurt|cheese|milk|cream|butter)\b.*", re.IGNORECASE), "🧀"),
        (re.compile(r".*\b(oil|vinegar|balsamic)\b.*", re.IGNORECASE), "🫙"),
        (re.compile(r".*\b(tomato|potato|onion|garlic|carrot|lettuce|spinach)\b.*", re.IGNORECASE), "🥦"),
        (re.compile(r".*\b(apple|banana|orange|lemon|lime|berry|strawberry)\b.*", re.IGNORECASE), "🍎"),
        (re.compile(r".*\b(bread|rice|pasta|noodle|flour|oat)\b.*", re.IGNORECASE), "🍞"),
        (re.compile(r".*\b(water|soda|juice|coffee|tea|wine|beer)\b.*", re.IGNORECASE), "🥤"),
    ]
    
    @classmethod
    def get_emoji_for_item(cls, item_name: str) -> str:
        """Return an emoji for the given item name.
        
        Args:
            item_name: The item name to match (case-insensitive)
            
        Returns:
            An emoji string, or empty string if no match found.
        """
        if not item_name:
            return ""
        
        item_lower = item_name.strip().lower()
        
        # First, check for exact keyword matches in the map
        for keyword, emoji in cls._CATEGORY_MAP.items():
            if keyword in item_lower:
                return emoji
        
        # Then, try pattern matching for broader categories
        for pattern, emoji in cls._CATEGORY_PATTERNS:
            if pattern.match(item_lower):
                return emoji
        
        # Default: no emoji
        return ""
    
    @classmethod
    def format_with_emoji(cls, item_name: str) -> str:
        """Format item name with emoji prefix if available.
        
        Returns:
            Formatted string like "🐔 Chicken Breast" or "Chicken Breast"
        """
        emoji = cls.get_emoji_for_item(item_name)
        if emoji:
            return f"{emoji} {item_name}"
        return item_name


# Singleton instance for easy import
emoji_taxonomy = EmojiTaxonomy()