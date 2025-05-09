import pytest
from unittest.mock import MagicMock, AsyncMock # Import AsyncMock
import hashlib # Import hashlib for generate_user_token test

# Import functions from your utils modules - Corrected import path
from utils.parser import parse_algolia_filters, _is_float
from utils.algolia import generate_user_token, find_movie_by_title, _check_movie_exists
# Removed the import of SearchClient as it's not needed for mocking generic objects


# --- Tests for utils.parser ---

def test_parse_algolia_filters_no_filters():
    query = "The Matrix movie"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "The Matrix movie"
    assert filters == ""

def test_parse_algolia_filters_single_filter():
    query = "matrix year:1999"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "matrix"
    assert filters == 'year:"1999"'

def test_parse_algolia_filters_multiple_filters():
    query = "action genre:Comedy director:Nolan year:>2000"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "action"
    # Filters order might vary, check presence
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 3
    assert 'genre:"Comedy"' in filter_list
    assert 'director:"Nolan"' in filter_list
    assert 'year>2000' in filter_list

def test_parse_algolia_filters_quoted_value():
    query = 'search actor:"Tom Hanks"'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "search"
    assert filters == 'actors:"Tom Hanks"' # Assumes 'actor' maps to 'actors'

def test_parse_algolia_filters_quoted_value_in_middle():
    query = 'action movie genre:"Sci-Fi" director:Spielberg'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "action movie"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 2
    assert 'genre:"Sci-Fi"' in filter_list
    assert 'director:"Spielberg"' in filter_list

def test_parse_algolia_filters_numeric_range():
    query = 'movies year:1990 TO 2000 votes:>10'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "movies"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 2
    assert 'year:1990 TO 2000' in filter_list
    assert 'votes>10' in filter_list

def test_parse_algolia_filters_complex_query():
    query = 'best sci-fi actor:"Sigourney Weaver" year:<2010 genre:Horror rating:>=8.5'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "best sci-fi"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 4
    assert 'actors:"Sigourney Weaver"' in filter_list
    assert 'year<2010' in filter_list
    assert 'genre:"Horror"' in filter_list
    assert 'rating>=8.5' in filter_list

def test_parse_algolia_filters_unrecognized_key():
    query = 'movie format:DVD'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "movie format:DVD" # Unrecognized filter is treated as part of query
    assert filters == ""

def test_parse_algolia_filters_empty_string():
    query = ""
    main_query, filters = parse_algolia_filters(query)
    assert main_query == ""
    assert filters == ""

# --- Tests for utils.algolia (Pure functions) ---

def test_generate_user_token():
    user_id_1 = "discord_1234567890"
    user_id_2 = "discord_0987654321"
    token_1a = generate_user_token(user_id_1)
    token_1b = generate_user_token(user_id_1)
    token_2 = generate_user_token(user_id_2)

    assert token_1a == token_1b
    assert token_1a != token_2
    assert len(token_1a) == 64
    assert all(c in '0123456789abcdef' for c in token_1a.lower()) # Ensure case-insensitive check

def test__is_float():
    assert _is_float("123") is True
    assert _is_float("123.45") is True
    assert _is_float("-10") is True
    assert _is_float("0") is True
    assert _is_float("0.0") is True
    assert _is_float(".5") is True
    assert _is_float("-0.75") is True
    assert _is_float("1e-3") is True
    assert _is_float(123) is True
    assert _is_float(123.45) is True
    assert _is_float(0) is True
    assert _is_float(None) is False
    assert _is_float("abc") is False
    assert _is_float("12.3.4") is False
    assert _is_float("123a") is False
    assert _is_float("") is False
    assert _is_float(" ") is False
    assert _is_float([]) is False
    assert _is_float({}) is False

# --- Tests for utils.algolia (Mocked Algolia interactions) ---

# Pytest fixture to create a mock Algolia index object (using MagicMock directly)
@pytest.fixture
def mock_movies_index():
    # Create a MagicMock object to simulate the Algolia index methods used
    index_mock = MagicMock()
    # Mock the 'search' method as an AsyncMock
    index_mock.search = AsyncMock()
    index_mock.get_object = AsyncMock()
    # Add other methods used in algolia.py if needed in future tests (e.g., browse_objects, partial_update_object, wait_task)
    index_mock.browse_objects = MagicMock() # browse_objects is often synchronous iterator in older client
    index_mock.partial_update_object = MagicMock()
    index_mock.wait_task = MagicMock()
    return index_mock

@pytest.mark.asyncio
async def test_find_movie_by_title_found_exact(mock_movies_index):
    mock_hit = {"objectID": "movie_1", "title": "Exact Match Movie"}
    mock_hit_highlighted = {**mock_hit, "_highlightResult": {"title": {"value": "<em>Exact Match Movie</em>", "matchLevel": "full"}}} # Added highlight structure
    mock_movies_index.search.return_value = {
        "hits": [mock_hit_highlighted, {"objectID": "movie_2", "title": "Similar Movie"}],
        "nbHits": 2
    }

    title = "Exact Match Movie"
    found_movie = await find_movie_by_title(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
        title,
        {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
            "attributesToHighlight": ["title", "originalTitle"],
            "typoTolerance": "strict"
        }
    )
    # It should return the hit with full match level
    assert found_movie == mock_hit_highlighted

@pytest.mark.asyncio
async def test_find_movie_by_title_found_inexact_returns_top_hit(mock_movies_index):
    top_hit = {"objectID": "movie_1", "title": "The Matrix Reloaded"}
    second_hit = {"objectID": "movie_2", "title": "The Matrix Revolutions"}
    mock_movies_index.search.return_value = {
        "hits": [top_hit, second_hit],
        "nbHits": 2
    }

    title = "Matrix" # Query that is not an exact title
    found_movie = await find_movie_by_title(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
         title, # Should search with the query
         {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
            "attributesToHighlight": ["title", "originalTitle"],
            "typoTolerance": "strict"
        }
    )
    # It should return the first hit from the search results if no exact/full match is found
    assert found_movie == top_hit


@pytest.mark.asyncio
async def test_find_movie_by_title_not_found(mock_movies_index):
    mock_movies_index.search.return_value = {"hits": [], "nbHits": 0}
    title = "NonExistent Movie"
    found_movie = await find_movie_by_title(mock_movies_index, title)
    mock_movies_index.search.assert_called_once()
    assert found_movie is None

@pytest.mark.asyncio
async def test__check_movie_exists_found_exact(mock_movies_index):
    mock_hit = {"objectID": "movie_1", "title": "Exact Match Movie"}
    mock_hit_highlighted = {**mock_hit, "_highlightResult": {"title": {"value": "<em>Exact Match Movie</em>", "matchLevel": "full"}}} # Added highlight structure
    mock_movies_index.search.return_value = {
        "hits": [mock_hit_highlighted, {"objectID": "movie_2", "title": "Similar Movie"}],
        "nbHits": 2
    }

    title = "Exact Match Movie"
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
        title,
        {
            "hitsPerPage": 5,
            "attributesToRetrieve": ["objectID", "title"],
            "attributesToHighlight": ["title"],
            "typoTolerance": "strict"
        }
    )
    # It should return the hit object if a full match level or exact string match is found
    assert exists == mock_hit_highlighted


@pytest.mark.asyncio
async def test__check_movie_exists_not_found(mock_movies_index):
    mock_movies_index.search.return_value = {"hits": [], "nbHits": 0}
    title = "NonExistent Movie"
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once()
    assert exists is None

@pytest.mark.asyncio
async def test__check_movie_exists_found_only_partial_match(mock_movies_index):
    # Simulate a search that finds a hit, but only with a partial match level
    partial_hit = {"objectID": "movie_1", "title": "The Matrix Reloaded", "_highlightResult": {"title": {"value": "The <em>Matrix</em> Reloaded", "matchLevel": "partial"}}}
    mock_movies_index.search.return_value = {
        "hits": [partial_hit],
        "nbHits": 1
    }

    title = "Matrix" # Search term
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once()
    # It should return None because it only found a partial match and not an exact string match
    assert exists is None


# More tests can be added here following the same pattern for other async functions
# in utils.algolia like search_movies_for_vote, get_top_movies, get_all_movies.
# Testing vote_for_movie is more complex as it involves multiple mocks (movies_index, votes_index)
# and mocking partial_update_object and wait_task.