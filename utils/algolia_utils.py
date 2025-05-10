import hashlib
import time
import random
import logging
from typing import List, Dict, Any, Optional, Union, Tuple

from algoliasearch.search_client import SearchClient
from algoliasearch.search_index import SearchIndex

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


# Algolia interaction methods using v3 API structure
async def _check_movie_exists(client: SearchClient, index_name: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a movie with a similar title already exists in Algolia.
    Uses search and checks for strong matches.
    """
    if not title:
        return None
    try:
        index = client.init_index(index_name)

        search_response = index.search(title, {
            'hitsPerPage': 5,
            'attributesToRetrieve': ['objectID', 'title'],
            'attributesToHighlight': ['title'],
            'typoTolerance': 'strict'
        })

        if not search_response or search_response.get('nbHits', 0) == 0:
            return None

        for hit in search_response.get('hits', []):
            title_highlight = hit.get('_highlightResult', {}).get('title', {})
            if title_highlight.get('matchLevel') == 'full':
                logger.info(f"Existing movie check: Found full title match for '{title}': {hit['objectID']}")
                return hit
            if hit.get('title', '').lower() == title.lower():
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
        index = client.init_index(index_name)

        # Ensure the data has required fields for your schema
        processed_data = {
            'objectID': movie_data.get('objectID', f"manual_{int(time.time())}_{random.randint(0, 999)}"),
            'title': movie_data.get('title', 'Unknown Movie'),
            'originalTitle': movie_data.get('originalTitle', movie_data.get('title', 'Unknown Movie')),
            'year': movie_data.get('year'),
            'director': movie_data.get('director', 'Unknown'),
            'actors': movie_data.get('actors', []) if isinstance(movie_data.get('actors'), list) else [],
            'genre': movie_data.get('genre', []) if isinstance(movie_data.get('genre'), list) else [],
            'plot': movie_data.get('plot', 'No plot available.'),
            'image': movie_data.get('image'),
            'rating': movie_data.get('rating'),
            'imdbID': movie_data.get('imdbID'),
            'tmdbID': movie_data.get('tmdbID'),
            'source': movie_data.get('source', 'manual'),
            'votes': movie_data.get('votes', 0),
            'addedDate': movie_data.get('addedDate', int(time.time())),
            'addedBy': movie_data.get('addedBy', ''),
            'voted': movie_data.get('voted', False)
        }

        # V3 API: Simple save_object call
        index.save_object(processed_data)
        logger.info(f"Added movie to Algolia: {processed_data.get('title')} ({processed_data.get('objectID')})")
    except Exception as e:
        logger.error(f"Error adding movie to Algolia: {e}", exc_info=True)
        raise  # Re-raise the exception


async def vote_for_movie(client: SearchClient, movies_index_name: str, votes_index_name: str,
                         movie_id: str, user_id: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """Vote for a movie in Algolia."""
    try:
        user_token = generate_user_token(user_id)
        votes_index = client.init_index(votes_index_name)

        # Check if user already voted for this movie using the votes index
        search_response = votes_index.search('', {
            'filters': f"userToken:'{user_token}' AND movieId:'{movie_id}'"
        })

        if search_response.get('nbHits', 0) > 0:
            logger.info(f"User {user_id} ({user_token[:8]}...) already voted for movie {movie_id}.")
            existing_movie = await get_movie_by_id(client, movies_index_name, movie_id)
            return False, existing_movie if existing_movie else "Already voted"

        # Record the vote in the votes index
        vote_obj = {
            'objectID': f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}",
            'userToken': user_token,
            'movieId': movie_id,
            'timestamp': int(time.time())
        }

        votes_index.save_object(vote_obj)
        logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

        # Increment the movie's vote count in the movies index
        movies_index = client.init_index(movies_index_name)

        logger.info(f"Sending increment task for movie {movie_id}.")
        update_result = movies_index.partial_update_object({
            'objectID': movie_id,
            'votes': {
                '_operation': 'Increment',
                'value': 1
            }
        })

        # V3 API: Wait for task using index method
        movies_index.wait_task(update_result['taskID'])
        logger.info(f"Algolia task {update_result['taskID']} completed for index {movies_index_name}.")

        # Fetch the updated movie object
        updated_movie = await get_movie_by_id(client, movies_index_name, movie_id)
        if updated_movie:
            logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
            return True, updated_movie
        else:
            logger.error(
                f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting.")
            return True, {'objectID': movie_id, 'votes': 'Unknown', 'title': 'Unknown Movie', 'image': None}

    except Exception as e:
        logger.error(f"FATAL error voting for movie {movie_id} by user {user_id}: {e}", exc_info=True)
        return False, str(e)


async def get_movie_by_id(client: SearchClient, index_name: str, movie_id: str) -> Optional[Dict[str, Any]]:
    """Get a movie by its ID from Algolia movies index."""
    try:
        index = client.init_index(index_name)
        response_obj = index.get_object(movie_id)
        return response_obj
    except Exception as e:
        # Check for specific "object not found"
        if 'ObjectID does not exist' in str(e) or '404' in str(e):
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
        index = client.init_index(index_name)

        search_response = index.search(title, {
            'hitsPerPage': 5,
            'attributesToRetrieve': [
                'objectID', 'title', 'originalTitle', 'year', 'director',
                'actors', 'genre', 'plot', 'image', 'votes', 'rating',
                'imdbID', 'tmdbID'
            ],
            'attributesToHighlight': ['title', 'originalTitle'],
            'typoTolerance': 'strict'
        })

        if not search_response or search_response.get('nbHits', 0) == 0:
            return None

        # Prioritize matches based on highlight results and exact string match
        for hit in search_response.get('hits', []):
            highlight_result = hit.get('_highlightResult', {})
            title_highlight = highlight_result.get('title', {})
            original_title_highlight = highlight_result.get('originalTitle', {})

            if title_highlight.get('matchLevel') == 'full' or \
                    original_title_highlight.get('matchLevel') == 'full':
                logger.info(f"Found strong title match for '{title}': {hit.get('title')} ({hit.get('objectID')})")
                return hit

            if hit.get('title', '').lower() == title.lower() or \
                    hit.get('originalTitle', '').lower() == title.lower():
                logger.info(f"Found exact string match for '{title}': {hit.get('title')} ({hit.get('objectID')})")
                return hit

        # If no strong match, return the top hit if any
        top_hit = search_response['hits'][0]
        logger.info(
            f"No strong/exact title match for '{title}', returning top relevant hit: {top_hit.get('title')} ({top_hit.get('objectID')})")
        return top_hit

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
        return {'hits': [], 'nbHits': 0}
    try:
        index = client.init_index(index_name)

        search_response = index.search(title, {
            'hitsPerPage': 5,
            'attributesToRetrieve': [
                'objectID', 'title', 'year', 'votes', 'image'
            ],
            'typoTolerance': True
        })

        logger.info(f"Vote search for '{title}' found {search_response.get('nbHits', 0)} hits.")
        return {
            'hits': search_response.get('hits', []),
            'nbHits': search_response.get('nbHits', 0)
        }

    except Exception as e:
        logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
        return {'hits': [], 'nbHits': 0}


async def get_top_movies(client: SearchClient, index_name: str, count: int = 5) -> List[Dict[str, Any]]:
    """Get the top voted movies from Algolia movies index."""
    try:
        index = client.init_index(index_name)

        search_response = index.search('', {
            'hitsPerPage': count,
            'attributesToRetrieve': [
                'objectID', 'title', 'year', 'director',
                'actors', 'genre', 'image', 'votes', 'plot', 'rating'
            ]
        })

        return search_response.get('hits', [])

    except Exception as e:
        logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
        return []


async def get_all_movies(client: SearchClient, index_name: str) -> List[Dict[str, Any]]:
    """Get all movies from Algolia movies index using browse_objects."""
    all_movies: List[Dict[str, Any]] = []
    try:
        index = client.init_index(index_name)

        # V3 API: Simple browse_objects call
        for hit in index.browse_objects():
            all_movies.append(hit)

        logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse_objects.")
        # Sort in Python if needed, though browse doesn't guarantee order like search
        all_movies.sort(key=lambda m: (m.get('votes', 0), m.get('title', '')), reverse=True)

        return all_movies

    except Exception as e:
        logger.error(f"Error getting all movies from Algolia: {e}", exc_info=True)
        # Fallback to search-based approach
        try:
            logger.info("Attempting fallback search-based approach for get_all_movies")
            index = client.init_index(index_name)

            search_response = index.search('', {
                'hitsPerPage': 1000  # Increase if needed
            })

            all_movies = search_response.get('hits', [])
            all_movies.sort(key=lambda m: (m.get('votes', 0), m.get('title', '')), reverse=True)
            logger.info(f"Fallback fetched {len(all_movies)} movies using search")
            return all_movies
        except Exception as fallback_e:
            logger.error(f"Fallback search also failed: {fallback_e}", exc_info=True)

        return []