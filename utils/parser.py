"""
Parser utilities for Paradiso Discord Bot
Handles parsing of filter expressions for Algolia queries.
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger("paradiso_bot")

def parse_algolia_filters(query_string: str) -> Tuple[str, str]:
    """
    Parse a query string that may contain filter expressions.
    
    Returns:
        Tuple of (main_query, filter_string)
        
    Supported filter syntax:
    - key:value (equals)
    - key:"multi word value" (equals with spaces)
    - key:>value (greater than)
    - key:>=value (greater than or equal)
    - key:<value (less than)
    - key:<=value (less than or equal)
    - key>value (alternative greater than)
    - key>=value (alternative greater than or equal)
    - key<value (alternative less than)
    - key<=value (alternative less than or equal)
    
    Example: "action movies year>2010 rating:>7.5 director:\"Christopher Nolan\""
    """
    if not query_string:
        return "", ""
    
    # Separate main query from filter expressions
    # Regex to match filter patterns: key:value, key:"multi word", key:>value, etc.
    filter_pattern = r'(\w+)(?::|\s*[><]=?\s*)(?:"([^"]+)"|([^\s]+))'
    
    # Find all filter expressions
    filter_matches = re.finditer(filter_pattern, query_string)
    
    filters = []
    for match in filter_matches:
        key = match.group(1).lower()
        # Get operator from the match
        operator = ""
        if ":>" in match.group(0):
            operator = ">"
        elif ":<" in match.group(0):
            operator = "<"
        elif ":>=" in match.group(0):
            operator = ">="
        elif ":<=" in match.group(0):
            operator = "<="
        elif ">" in match.group(0) and ":" not in match.group(0):
            operator = ">"
        elif ">=" in match.group(0) and ":" not in match.group(0):
            operator = ">="
        elif "<" in match.group(0) and ":" not in match.group(0):
            operator = "<"
        elif "<=" in match.group(0) and ":" not in match.group(0):
            operator = "<="
        
        # Get the value (either from quoted group or non-quoted)
        value = match.group(2) if match.group(2) else match.group(3)
        
        # Clean up the value if it contains an operator
        if not operator and (value.startswith(">") or value.startswith("<")):
            # Handle case where operator is in the value part (e.g., key:>5)
            if value.startswith(">="):
                operator = ">="
                value = value[2:]
            elif value.startswith("<="):
                operator = "<="
                value = value[2:]
            elif value.startswith(">"):
                operator = ">"
                value = value[1:]
            elif value.startswith("<"):
                operator = "<"
                value = value[1:]
        
        # Default operator is equality
        if not operator:
            operator = "="
        
        # Special handling for numeric fields
        if key in ["year", "votes", "rating"]:
            try:
                float_value = float(value)
                # Use numeric comparison
                filters.append(f"{key} {operator} {float_value}")
            except ValueError:
                # Not a number, use string equality (likely won't match)
                filters.append(f"{key} {operator} \"{value}\"")
        elif key == "genre" or key == "actor" or key == "director":
            # These are typically array fields or text fields that need string matching
            value_quoted = f"\"{value}\""
            if operator == "=":
                filters.append(f"{key}:{value_quoted}")
            else:
                # Not a typical operation for these fields, but allow it
                filters.append(f"{key} {operator} {value_quoted}")
        else:
            # Default handling for other fields
            value_quoted = f"\"{value}\""
            if operator == "=":
                filters.append(f"{key}:{value_quoted}")
            else:
                filters.append(f"{key} {operator} {value_quoted}")
    
    # Remove filter expressions from the main query
    main_query = query_string
    for match in re.finditer(filter_pattern, query_string):
        main_query = main_query.replace(match.group(0), "")
    
    # Clean up main query (remove extra spaces)
    main_query = " ".join(part for part in main_query.split() if part)
    
    # Combine all filters with AND
    filter_string = " AND ".join(filters) if filters else ""
    
    logger.debug(f"Parsed '{query_string}' into query='{main_query}', filters='{filter_string}'")
    
    return main_query, filter_string
