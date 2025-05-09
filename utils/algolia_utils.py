import hashlib
import time
import random
import logging
from typing import List, Dict, Any, Optional, Union, Tuple

from algoliasearch.search.client import SearchClient
from algoliasearch.search.models.search_response import SearchResponse
from algoliasearch.search.models.search_responses import SearchResponses

# For browse_objects, the specific response type might not be explicitly needed for simple iteration
# from algoliasearch.search.models.browse_response import BrowseResponse # Not strictly necessary for current usage

logger = logging.getLogger("paradiso_bot")


# Helper function
def generate_user_token(user_id: str) -> str:
    """Generate a consistent, non-reversible user token for Algolia from Discord user ID."""
    return hashlib.sha256(user_id.encode()).hexdigest()


# Helper function
def _is_float(value: Any) -> bool:
    """Helper to check if a value can be converted to a float."""
    if value is None:
        return False
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


# Algolia interaction methods using v4 API structure
async def _check_movie_exists(client: SearchClient, index_name: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a movie with a similar title already exists in Algolia.
    Uses search and checks for strong matches.
    """
    if not title:
        return None
    try:
        search_params_payload = {
            "requests": [
                {
                    "indexName": index_name,
                    "query": title,
                    "params": {
                        "hitsPerPage": 5,
                        "attributesToRetrieve": ["objectID", "title"],
                        "attributesToHighlight": ["title"],
                        "typoTolerance": "strict"
                    }
                }
            ]
        }

        search_response: SearchResponses = await client.search(search_method_params=search_params_payload)
        # For multi-search, results is a list. We expect one result here.
        if not search_response.results or len(search_response.results) == 0:
            logger.warning(f"No results array from Algolia for _check_movie_exists with title '{title}'")
            return None

        search_result: SearchResponse = search_response.results[0]

        if search_result.nb_hits == 0:  # nb_hits in v4
            return None

        for hit in search_result.hits:  # hits is a list of dicts
            title_highlight = hit.get("_highlightResult", {}).get("title", {})
            if title_highlight.get('matchLevel') == 'full':
                logger.info(f"Existing movie check: Found full title match for '{title}': {hit['objectID']}")
                return hit
            if hit.get("title", "").lower() == title.lower():
                logger.info(f"Existing movie check: Found exact string match for '{title}': {hit['objectID']}")
                return hit

        logger.info(f"Existing movie check: No strong title match for '{title}' among top hits.")
        return None

    except Exception as e:
        logger.error(f"Error checking existence for title '{title}' in Algolia: {e}", exc_info=True)
        return None


async def add_movie_to_algolia(client: SearchClient, index_name: str, movie_data: Dict[str, Any]) -> None:
    """Add a movie to Algolia movies index."""
    try:
        # V4 API: save_object with index_name as first arg, then body
        await client.save_object(index_name=index_name, body=movie_data)
        logger.info(f"Added movie to Algolia: {movie_data.get('title')} ({movie_data.get('objectID')})")
    except Exception as e:
        logger.error(f"Error adding movie to Algolia: {e}", exc_info=True)
        raise  # Re-raise the exception


async def vote_for_movie(client: SearchClient, movies_index_name: str, votes_index_name: str,
                         movie_id: str, user_id: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """Vote for a movie in Algolia."""
    try:
        user_token = generate_user_token(user_id)

        # Check if user already voted for this movie using the votes index
        search_params_payload = {
            "requests": [
                {
                    "indexName": votes_index_name,
                    "query": "",
                    "params": {
                        "filters": f"userToken:'{user_token}' AND movieId:'{movie_id}'"
                        # Ensure strings are quoted for filters
                    }
                }
            ]
        }

        search_response: SearchResponses = await client.search(search_method_params=search_params_payload)
        if not search_response.results or len(search_response.results) == 0:
            logger.error(f"No results array from Algolia for vote check for movie {movie_id}, user {user_id}")
            return False, "Error checking existing vote"

        search_result: SearchResponse = search_response.results[0]

        if search_result.nb_hits > 0:
            logger.info(f"User {user_id} ({user_token[:8]}...) already voted for movie {movie_id}.")
            existing_movie = await get_movie_by_id(client, movies_index_name, movie_id)
            return False, existing_movie if existing_movie else "Already voted"

        # Record the vote in the votes index
        vote_obj = {
            "objectID": f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}",
            "userToken": user_token,
            "movieId": movie_id,
            "timestamp": int(time.time())
        }

        await client.save_object(index_name=votes_index_name, body=vote_obj)
        logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

        # Increment the movie's vote count in the movies index
        # In V4, partial_update_object takes attributes_to_update as the body
        attributes_to_update_payload = {
            "votes": {
                "_operation": "Increment",
                "value": 1
            }
        }

        logger.info(f"Sending increment task for movie {movie_id}.")
        update_result = await client.partial_update_object(
            index_name=movies_index_name,
            object_id=movie_id,
            attributes_to_update=attributes_to_update_payload,  # This is the body
            create_if_not_exists=False  # This is a specific parameter for this method
        )

        # Wait for the task to complete
        update_task_id = update_result.task_id
        logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_task_id}")

        try:
            await client.wait_for_task(index_name=movies_index_name, task_id=update_task_id)
            logger.info(f"Algolia task {update_task_id} completed for index {movies_index_name}.")
        except Exception as e:
            logger.warning(
                f"Failed to wait for Algolia task {update_task_id} for index {movies_index_name}: {e}. Fetching potentially stale movie data.",
                exc_info=True)

        # Fetch the updated movie object
        updated_movie = await get_movie_by_id(client, movies_index_name, movie_id)
        if updated_movie:
            logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
            return True, updated_movie
        else:
            logger.error(
                f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting. Attempting fallback.",
                exc_info=True)
            # Fallback: This logic can remain similar, but ensure get_movie_by_id is robust
            try:
                # Attempt to fetch again, in case of transient issue
                movie_before_vote_again = await get_movie_by_id(client, movies_index_name, movie_id)
                if movie_before_vote_again:
                    fallback_votes = movie_before_vote_again.get('votes',
                                                                 0)  # Assume increment happened if task was sent
                    fallback_title = movie_before_vote_again.get('title', 'Unknown Movie')
                    fallback_image = movie_before_vote_again.get('image')
                    logger.warning(
                        f"Returning fallback info for movie {movie_id} vote confirmation using re-fetched data.")
                    return True, {"objectID": movie_id, "votes": fallback_votes, 'title': fallback_title,
                                  'image': fallback_image}
                else:  # if still cannot fetch, use a placeholder
                    logger.error(f"Failed to fetch movie {movie_id} even with fallback re-fetch.", exc_info=True)
                    return True, {"objectID": movie_id, "votes": 'Unknown (increment sent)', 'title': 'Unknown Movie',
                                  'image': None}

            except Exception:
                logger.error(f"Exception in fallback for movie {movie_id}.", exc_info=True)
                return True, {"objectID": movie_id, "votes": 'Unknown (increment sent)', 'title': 'Unknown Movie',
                              'image': None}


    except Exception as e:
        logger.error(f"FATAL error voting for movie {movie_id} by user {user_id}: {e}", exc_info=True)
        return False, str(e)


async def get_movie_by_id(client: SearchClient, index_name: str, movie_id: str) -> Optional[Dict[str, Any]]:
    """Get a movie by its ID from Algolia movies index."""
    try:
        # V4 API: get_object takes index_name and object_id
        response_obj = await client.get_object(index_name=index_name, object_id=movie_id)
        # The response_obj itself is the movie dictionary in v4
        return response_obj
    except Exception as e:
        # Check for specific "object not found"
        if "ObjectID does not exist" in str(e) or (hasattr(e, 'status_code') and e.status_code == 404):
            logger.warning(f"Movie by ID {movie_id} not found in Algolia: {e}")
        else:
            logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}", exc_info=True)
        return None


async def find_movie_by_title(client: SearchClient, index_name: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Find a movie by title in Algolia movies index using search.
    Prioritizes strong matches. Used for commands like /info, /related,
    and add pre-check where a single reference movie is needed.
    """
    if not title:
        return None
    try:
        search_params_payload = {
            "requests": [
                {
                    "indexName": index_name,
                    "query": title,
                    "params": {
                        "hitsPerPage": 5,
                        "attributesToRetrieve": [
                            "objectID", "title", "originalTitle", "year", "director",
                            "actors", "genre", "plot", "image", "votes", "rating",
                            "imdbID", "tmdbID"
                        ],
                        "attributesToHighlight": ["title", "originalTitle"],
                        "typoTolerance": "strict"  # strict typo tolerance
                    }
                }
            ]
        }

        search_response: SearchResponses = await client.search(search_method_params=search_params_payload)
        if not search_response.results or len(search_response.results) == 0:
            logger.warning(f"No results array from Algolia for find_movie_by_title with title '{title}'")
            return None

        search_result: SearchResponse = search_response.results[0]

        if search_result.nb_hits == 0:
            return None

        # Prioritize matches based on highlight results and exact string match
        for hit in search_result.hits:
            highlight_result = hit.get("_highlightResult", {})
            title_highlight = highlight_result.get("title", {})
            original_title_highlight = highlight_result.get("originalTitle", {})

            if title_highlight.get('matchLevel') == 'full' or \
                    original_title_highlight.get('matchLevel') == 'full':
                logger.info(f"Found strong title match for '{title}': {hit.get('title')} ({hit.get('objectID')})")
                return hit  # hit is already a dict

            if hit.get("title", "").lower() == title.lower() or \
                    hit.get("originalTitle", "").lower() == title.lower():
                logger.info(f"Found exact string match for '{title}': {hit.get('title')} ({hit.get('objectID')})")
                return hit

        # If no strong match, return the top hit if any
        logger.info(
            f"No strong/exact title match for '{title}', returning top relevant hit: {search_result.hits[0].get('title')} ({search_result.hits[0].get('objectID')})")
        return search_result.hits[0]  # hit is already a dict

    except Exception as e:
        logger.error(f"Error finding movie by title '{title}' in Algolia: {e}", exc_info=True)
        return None


async def search_movies_for_vote(client: SearchClient, index_name: str, title: str) -> Dict[str, Any]:
    """
    Searches for movies by title for the voting command.
    Returns search results (up to ~5 hits) allowing for ambiguity.
    This function expects a dictionary with 'hits' and 'nbHits' keys.
    """
    if not title:
        return {"hits": [], "nbHits": 0}
    try:
        search_params_payload = {
            "requests": [
                {
                    "indexName": index_name,
                    "query": title,
                    "params": {
                        "hitsPerPage": 5,
                        "attributesToRetrieve": [
                            "objectID", "title", "year", "votes", "image"
                        ],
                        "typoTolerance": True  # More lenient typo tolerance for voting
                    }
                }
            ]
        }

        search_responses_obj: SearchResponses = await client.search(search_method_params=search_params_payload)
        if not search_responses_obj.results or len(search_responses_obj.results) == 0:
            logger.warning(f"No results array from Algolia for search_movies_for_vote with title '{title}'")
            return {"hits": [], "nbHits": 0}

        search_result: SearchResponse = search_responses_obj.results[0]

        logger.info(f"Vote search for '{title}' found {search_result.nb_hits} hits.")
        # search_result.hits are already dictionaries
        return {"hits": search_result.hits, "nbHits": search_result.nb_hits}

    except Exception as e:
        logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
        return {"hits": [], "nbHits": 0}


async def get_top_movies(client: SearchClient, index_name: str, count: int = 5) -> List[Dict[str, Any]]:
    """Get the top voted movies from Algolia movies index."""
    try:
        search_params_payload = {
            "requests": [
                {
                    "indexName": index_name,
                    "query": "",  # No query text needed for fetching by rank/filter
                    "params": {
                        # "filters": "votes > 0", # Not needed if relying on custom ranking primarily
                        "hitsPerPage": count,
                        "attributesToRetrieve": [
                            "objectID", "title", "year", "director",
                            "actors", "genre", "image", "votes", "plot", "rating"
                        ],
                        # Ensure your index has customRanking: desc(votes), asc(title) or similar
                    }
                }
            ]
        }

        search_response: SearchResponses = await client.search(search_method_params=search_params_payload)
        if not search_response.results or len(search_response.results) == 0:
            logger.warning(f"No results array from Algolia for get_top_movies")
            return []

        search_result: SearchResponse = search_response.results[0]

        # Hits are already sorted by Algolia based on ranking formula (which should include votes)
        # If you need to be absolutely sure or apply a secondary sort in Python:
        # top_movies = sorted(search_result.hits, key=lambda m: m.get("votes", 0), reverse=True)
        return search_result.hits  # hits are already dicts

    except Exception as e:
        logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
        return []


async def get_all_movies(client: SearchClient, index_name: str) -> List[Dict[str, Any]]:
    """Get all movies from Algolia movies index using browse_objects."""
    all_movies: List[Dict[str, Any]] = []
    try:
        # browse_objects is an async generator in v4
        async for hit in client.browse_objects(index_name=index_name):
            all_movies.append(hit)  # hit is already a dict

        logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse_objects.")
        # Sort in Python if needed, though browse doesn't guarantee order like search
        all_movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

        return all_movies

    except Exception as e:
        logger.error(f"Error getting all movies from Algolia: {e}", exc_info=True)
        return []