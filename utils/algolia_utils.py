import hashlib
import time
import random
import logging
from typing import List, Dict, Any, Optional, Union, Tuple

from algoliasearch.search_client import SearchClient

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


# Algolia interaction methods now take client and index_name instead of index objects
async def _check_movie_exists(client: SearchClient, index_name: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a movie with a similar title already exists in Algolia.
    Uses search and checks for strong matches.
    """
    if not title: return None
    try:
        # Use client.search with index_name as first argument
        search_result = client.search(index_name, title, {
            "hitsPerPage": 5,
            "attributesToRetrieve": ["objectID", "title"],
            "attributesToHighlight": ["title"],
            "typoTolerance": "strict"
        })

        if search_result["nbHits"] == 0:
            return None

        for hit in search_result["hits"]:
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


def add_movie_to_algolia(client: SearchClient, index_name: str, movie_data: Dict[str, Any]) -> None:
    """Add a movie to Algolia movies index."""
    try:
        # Use client.save_objects with index_name
        client.save_objects(index_name, [movie_data])
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
        search_result = client.search(votes_index_name, "", {
            "filters": f"userToken:{user_token} AND movieId:{movie_id}"
        })

        if search_result["nbHits"] > 0:
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
        client.save_objects(votes_index_name, [vote_obj])
        logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

        # Increment the movie's vote count in the movies index
        update_result = client.partial_update_objects(movies_index_name, [{
            "objectID": movie_id,
            "votes": {
                "_operation": "Increment",
                "value": 1
            },
        }])
        
        # Wait for the task to complete - in v4 client we use wait_task with taskID
        update_task_id = update_result['objectIDs'][0]['taskID']  # v4 structure
        logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_task_id}")

        try:
            client.wait_task(movies_index_name, update_task_id)
            logger.info(f"Algolia task {update_task_id} completed.")
        except Exception as e:
             logger.warning(f"Failed to wait for Algolia task {update_task_id}: {e}. Fetching potentially stale movie data.", exc_info=True)

        # Fetch the updated movie object
        updated_movie = await get_movie_by_id(client, movies_index_name, movie_id)
        if updated_movie:
             logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
             return True, updated_movie
        else:
             logger.error(f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting. Attempting fallback.", exc_info=True)
             # Fallback: Get latest known data and increment votes locally
             try:
                  movie_before_vote = await get_movie_by_id(client, movies_index_name, movie_id)
                  fallback_votes = movie_before_vote.get('votes', 0) + 1 if movie_before_vote else 'Unknown'
                  fallback_title = movie_before_vote.get('title', 'Unknown Movie')
                  fallback_image = movie_before_vote.get('image')
                  logger.warning(f"Returning fallback info for movie {movie_id} vote confirmation.")
                  return True, {"objectID": movie_id, "votes": fallback_votes, 'title': fallback_title, 'image': fallback_image}
             except Exception:
                  logger.error(f"Failed to fetch movie {movie_id} even with fallback.", exc_info=True)
                  return True, {"objectID": movie_id, "votes": 'Unknown', 'title': 'Unknown Movie', 'image': None}

    except Exception as e:
        logger.error(f"FATAL error voting for movie {movie_id} by user {user_id}: {e}", exc_info=True)
        return False, str(e)


async def get_movie_by_id(client: SearchClient, index_name: str, movie_id: str) -> Optional[Dict[str, Any]]:
    """Get a movie by its ID from Algolia movies index."""
    try:
        return client.get_object(index_name, movie_id)
    except Exception as e:
        logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}", exc_info=True)
        return None


async def find_movie_by_title(client: SearchClient, index_name: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Find a movie by title in Algolia movies index using search.
    Prioritizes strong matches. Used for commands like /info, /related,
    and add pre-check where a single reference movie is needed.
    """
    if not title: return None
    try:
        search_result = client.search(index_name, title, {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
             "attributesToHighlight": ["title", "originalTitle"],
             "typoTolerance": "strict"
        })

        if search_result["nbHits"] == 0:
            return None

        for hit in search_result["hits"]:
             title_highlight = hit.get("_highlightResult", {}).get("title", {})
             original_title_highlight = hit.get("_highlightResult", {}).get("originalTitle", {})

             if title_highlight.get('matchLevel') == 'full' or original_title_highlight.get('matchLevel') == 'full':
                  logger.info(f"Found strong title match for '{title}': {hit['title']} ({hit['objectID']})")
                  return hit

             if hit.get("title", "").lower() == title.lower() or hit.get("originalTitle", "").lower() == title.lower():
                  logger.info(f"Found exact string match for '{title}': {hit['title']} ({hit['objectID']})")
                  return hit

        logger.info(f"No strong/exact title match for '{title}', returning top relevant hit: {search_result['hits'][0].get('title')} ({search_result['hits'][0].get('objectID')})")
        return search_result["hits"][0]

    except Exception as e:
        logger.error(f"Error finding movie by title '{title}' in Algolia: {e}", exc_info=True)
        return None


def search_movies_for_vote(client: SearchClient, index_name: str, title: str) -> Dict[str, Any]:
    """
    Searches for movies by title for the voting command.
    Returns search results (up to ~5 hits) allowing for ambiguity.
    """
    if not title: return {"hits": [], "nbHits": 0}
    try:
        search_result = client.search(index_name, title, {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "year", "votes", "image"
            ],
             "typoTolerance": True
        })

        logger.info(f"Vote search for '{title}' found {search_result['nbHits']} hits.")
        return search_result

    except Exception as e:
        logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
        return {"hits": [], "nbHits": 0}


async def get_top_movies(client: SearchClient, index_name: str, count: int = 5) -> List[Dict[str, Any]]:
    """Get the top voted movies from Algolia movies index."""
    try:
        search_result = client.search(index_name, "", {
            "filters": "votes > 0",
            "hitsPerPage": count,
            "attributesToRetrieve": [
                "objectID", "title", "year", "director",
                "actors", "genre", "image", "votes", "plot", "rating"
            ],
            # Rely on customRanking including "desc(votes)"
        })

        top_movies = sorted(search_result["hits"], key=lambda m: m.get("votes", 0), reverse=True)
        return top_movies

    except Exception as e:
        logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
        return []


async def get_all_movies(client: SearchClient, index_name: str) -> List[Dict[str, Any]]:
    """Get all movies from Algolia movies index."""
    try:
        all_movies = []
        for hit in client.browse_objects(index_name, {'hitsPerPage': 1000}):
             all_movies.append(hit)

        logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse.")
        all_movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

        return all_movies

    except Exception as e:
        logger.error(f"Error getting all movies from Algolia: {e}", exc_info=True)
        return [] 