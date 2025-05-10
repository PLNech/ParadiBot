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
    
    Supported filter syntax:
    - actor:name or actors:name
    - director:name
    - year:value or year>value or year<value
    - genre:name
    """
    if not query_string:
        return "", ""
    
    # Regex to match various filter patterns
    filter_patterns = [
        r'(?:actor|actors):\s*(["\']?)([^"\'\s]+(?:\s+[^"\'\s]+)*)\1',  # actor patterns
        r'director:\s*(["\']?)([^"\'\s]+(?:\s+[^"\'\s]+)*)\1',          # director pattern
        r'year\s*([><]=?)\s*(\d+)',                                    # year with operators
        r'year:\s*(\d+)',                                              # year exact match
        r'genre:\s*(["\']?)([^"\'\s]+(?:\s+[^"\'\s]+)*)\1'             # genre pattern
    ]
    
    filters = []
    main_query = query_string
    
    # Process each filter pattern
    for pattern in filter_patterns:
        matches = list(re.finditer(pattern, query_string, re.IGNORECASE))
        for match in matches:
            # Remove the matched filter from the main query
            main_query = main_query.replace(match.group(0), '', 1)
            
            if 'actor' in match.group(0).lower():
                # Handle actor/actors filter
                actor_name = match.group(2).strip()
                filters.append(f'actors:"{actor_name}"')
                
            elif 'director' in match.group(0).lower():
                # Handle director filter  
                director_name = match.group(2).strip()
                filters.append(f'director:"{director_name}"')
                
            elif 'year' in match.group(0).lower():
                # Handle year filter with operators
                if len(match.groups()) == 2 and match.group(2).isdigit():
                    # year with operator
                    operator = match.group(1)
                    year_value = match.group(2)
                    filters.append(f'year {operator} {year_value}')
                elif len(match.groups()) == 1 and match.group(1).isdigit():
                    # year exact match
                    year_value = match.group(1)
                    filters.append(f'year:{year_value}')
                    
            elif 'genre' in match.group(0).lower():
                # Handle genre filter
                genre_name = match.group(2).strip()
                filters.append(f'genre:"{genre_name}"')
    
    # Clean up main query
    main_query = " ".join(part for part in main_query.split() if part)
    
    # Combine all filters
    filter_string = " AND ".join(filters) if filters else ""
    
    logger.debug(f"Parsed '{query_string}' into query='{main_query}', filters='{filter_string}'")
    
    return main_query, filter_string