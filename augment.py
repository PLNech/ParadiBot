#!/usr/bin/env python3
import asyncio
import aiohttp
import argparse
import os
import json
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from pathlib import Path
import sys
from datetime import datetime
import logging
from algoliasearch.search_client import SearchClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TMDBAugmenter:
    def __init__(self, tmdb_api_key: str, algolia_client, algolia_index, parallel: int = 5):
        self.tmdb_api_key = tmdb_api_key
        self.algolia_client = algolia_client
        self.algolia_index = algolia_index
        self.parallel = parallel
        self.session = None
        self.processed_count = 0
        self.updated_count = 0
        self.error_count = 0
        self.start_time = None
        # Headers for TMDB API
        self.headers = {
            'Authorization': f'Bearer {tmdb_api_key}',
            'accept': 'application/json'
        }

    async def init_session(self):
        self.session = aiohttp.ClientSession()
        self.start_time = datetime.now()

    async def close_session(self):
        if self.session:
            await self.session.close()
        
        if self.start_time:
            duration = datetime.now() - self.start_time
            logger.info(f"\nProcessing complete:")
            logger.info(f"Total processed: {self.processed_count}")
            logger.info(f"Successfully updated: {self.updated_count}")
            logger.info(f"Errors: {self.error_count}")
            logger.info(f"Duration: {duration}")

    async def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Search for a movie by title and optionally year"""
        search_url = "https://api.themoviedb.org/3/search/movie"
        params = {
            'query': title,
            'include_adult': 'false',
            'language': 'en-US',
            'page': '1'
        }
        
        if year:
            params['year'] = str(year)

        async with self.session.get(search_url, params=params, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(f"TMDB API returned {response.status}")
            
            data = await response.json()
            
            if not data.get('results'):
                # Try searching without the year if we had one
                if year:
                    logger.info(f"No results found for '{title}' in year {year}, trying without year...")
                    return await self.search_movie(title)
                return None
            
            # If we have a year, try to find an exact match first
            if year:
                for result in data['results']:
                    if result.get('release_date', '').startswith(str(year)):
                        return result
            
            # Get the most relevant result (first one)
            result = data['results'][0]
            logger.info(f"Found match for '{title}': {result['title']} ({result.get('release_date', 'N/A')})")
            return result

    async def fetch_tmdb_data(self, movie_id: str, title: Optional[str] = None, year: Optional[int] = None) -> Dict[str, Any]:
        """Fetch movie data from TMDB API"""
        # First try to get TMDB ID from IMDB ID if that's what we have
        if movie_id and movie_id.startswith('tt'):
            find_url = f"https://api.themoviedb.org/3/find/{movie_id}"
            params = {
                'external_source': 'imdb_id'
            }
            
            async with self.session.get(find_url, params=params, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"TMDB API returned {response.status}")
                
                find_data = await response.json()
                
                if not find_data.get('movie_results'):
                    raise Exception(f"No TMDB match found for IMDB ID {movie_id}")
                
                movie_id = str(find_data['movie_results'][0]['id'])
        elif movie_id and movie_id.isdigit():
            # We already have a valid TMDB ID
            pass
        elif title:
            # If we don't have a valid ID but have a title, search for it
            search_result = await self.search_movie(title, year)
            if not search_result:
                raise Exception(f"No TMDB match found for title '{title}'")
            movie_id = str(search_result['id'])
        else:
            raise Exception("No valid movie ID or title provided")

        # Now get detailed movie info
        details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        params = {
            'append_to_response': 'credits,videos,images,keywords,reviews,similar,recommendations'
        }

        async with self.session.get(details_url, params=params, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(f"TMDB API returned {response.status}")
            
            return await response.json()

    def format_tmdb_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format raw TMDB data into our desired structure"""
        # Extract director
        director = 'Unknown'
        if data.get('credits', {}).get('crew'):
            directors = [
                member['name'] for member in data['credits']['crew']
                if member['job'] == 'Director'
            ]
            director = ', '.join(directors) if directors else 'Unknown'

        # Extract actors
        actors = []
        if data.get('credits', {}).get('cast'):
            actors = [
                actor['name'] for actor in data['credits']['cast'][:5]
            ]

        # Extract trailer
        trailers = []
        trailer_key = None
        if data.get('videos', {}).get('results'):
            trailers = [
                {
                    'key': video['key'],
                    'name': video['name'],
                    'site': video['site']
                }
                for video in data['videos']['results']
                if video['type'] == 'Trailer' and video['site'] == 'YouTube'
            ]
            if trailers:
                trailer_key = trailers[0]['key']

        # Extract keywords
        keywords = []
        if data.get('keywords', {}).get('keywords'):
            keywords = [k['name'] for k in data['keywords']['keywords']]

        return {
            'id': str(data['id']),
            'title': data['title'],
            'originalTitle': data['original_title'],
            'year': int(data['release_date'][:4]) if data.get('release_date') else None,
            'director': director,
            'actors': actors,
            'genre': [genre['name'] for genre in data.get('genres', [])],
            'plot': data['overview'],
            'poster_path': data['poster_path'],
            'backdrop_path': data['backdrop_path'],
            'imdbID': data.get('imdb_id'),
            'tmdbID': str(data['id']),
            'vote_average': data['vote_average'],
            'vote_count': data['vote_count'],
            'popularity': data['popularity'],
            'release_date': data['release_date'],
            'runtime': data['runtime'],
            'revenue': data['revenue'],
            'budget': data['budget'],
            'tagline': data['tagline'],
            'status': data['status'],
            'original_language': data['original_language'],
            'production_companies': [
                company['name'] for company in data.get('production_companies', [])
            ],
            'production_countries': [
                country['name'] for country in data.get('production_countries', [])
            ],
            'spoken_languages': [
                lang.get('english_name', lang.get('name'))
                for lang in data.get('spoken_languages', [])
            ],
            'trailerKey': trailer_key,
            'trailers': trailers,
            'keywords': keywords,
            'raw': data  # Store complete raw response
        }

    async def process_movie(self, movie: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single movie record"""
        self.processed_count += 1
        
        try:
            # Skip if already augmented
            if movie.get('augment', {}).get('tmdb'):
                logger.debug(f"Skipping already augmented movie: {movie.get('title', movie.get('objectID'))}")
                return movie

            # Get movie ID (prefer IMDB ID if available)
            movie_id = movie.get('imdbID') or movie.get('tmdbID')
            title = movie.get('title')
            year = movie.get('year')
            
            if not movie_id and not title:
                raise Exception("No IMDB/TMDB ID or title found")

            # Fetch and format TMDB data
            tmdb_data = await self.fetch_tmdb_data(movie_id, title, year)
            formatted_data = self.format_tmdb_data(tmdb_data)

            # Update movie record
            if 'augment' not in movie:
                movie['augment'] = {}
            movie['augment']['tmdb'] = formatted_data
            
            # Update the record in Algolia
            self.algolia_index.partial_update_object({
                'objectID': movie['objectID'],
                'augment': movie['augment']
            })
            
            self.updated_count += 1
            logger.info(f"Successfully augmented: {movie.get('title', movie.get('objectID'))}")
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error processing movie {movie.get('title', movie.get('objectID'))}: {str(e)}")
            
        return movie

    async def process_batch(self, movies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of movies concurrently"""
        return await asyncio.gather(*[
            self.process_movie(movie) for movie in movies
        ])

    async def fetch_all_movies(self) -> List[Dict[str, Any]]:
        """Fetch all movies from Algolia index that need augmentation"""
        # Use the Algolia browse method to get all records
        browse_iterator = self.algolia_index.browse_objects({
            'query': '',
            'filters': 'NOT augment.tmdb:*',  # Only get movies without TMDB augmentation
            'attributesToRetrieve': ['objectID', 'title', 'year', 'imdbID', 'tmdbID']
        })
        
        movies = []
        for hit in browse_iterator:
            movies.append(hit)
            
        logger.info(f"Found {len(movies)} movies that need TMDB augmentation")
        return movies

async def main():
    parser = argparse.ArgumentParser(description='Augment Algolia movie database with TMDB data')
    parser.add_argument('--env', type=str, default='.env',
                      help='Path to .env file (default: .env)')
    parser.add_argument('--parallel', type=int, default=5,
                      help='Number of parallel requests (default: 5)')
    parser.add_argument('--app-id', type=str,
                      help='Algolia Application ID (overrides env file)')
    parser.add_argument('--admin-key', type=str,
                      help='Algolia Admin API Key (overrides env file)')
    parser.add_argument('--index', type=str, default='paradiso_movies',
                      help='Algolia index name (default: paradiso_movies)')
    parser.add_argument('--tmdb-key', type=str,
                      help='TMDB API key (overrides env file)')
    parser.add_argument('--limit', type=int, default=0,
                      help='Limit number of movies to process (default: 0 = all)')
    args = parser.parse_args()

    # Load environment variables
    env_path = Path(args.env)
    if env_path.exists():
        load_dotenv(env_path)
    
    # Get Algolia credentials
    algolia_app_id = args.app_id or os.getenv('NEXT_PUBLIC_ALGOLIA_APP_ID') or os.getenv('ALGOLIA_APP_ID')
    algolia_admin_key = args.admin_key or os.getenv('ALGOLIA_ADMIN_KEY')
    
    if not algolia_app_id or not algolia_admin_key:
        logger.error("Algolia credentials not found. Provide them via arguments or environment variables")
        sys.exit(1)
    
    # Get TMDB API key
    tmdb_api_key = args.tmdb_key or os.getenv('TMDB_API_KEY')
    if not tmdb_api_key:
        logger.error("TMDB API key not found. Provide it via --tmdb-key or TMDB_API_KEY env variable")
        sys.exit(1)

    # Initialize Algolia client
    client = SearchClient.create(algolia_app_id, algolia_admin_key)
    index = client.init_index(args.index)
    
    # Initialize augmenter
    augmenter = TMDBAugmenter(tmdb_api_key, client, index, args.parallel)
    await augmenter.init_session()

    try:
        # Fetch movies that need augmentation
        movies = await augmenter.fetch_all_movies()
        
        # Apply limit if specified
        if args.limit > 0:
            movies = movies[:args.limit]
            logger.info(f"Limited to processing {args.limit} movies")
        
        # Process movies in batches
        batch_size = args.parallel
        for i in range(0, len(movies), batch_size):
            batch = movies[i:i + batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}/{(len(movies) + batch_size - 1)//batch_size}")
            await augmenter.process_batch(batch)
            # Small delay to avoid overwhelming Algolia with updates
            await asyncio.sleep(0.5)

    finally:
        await augmenter.close_session()

if __name__ == "__main__":
    asyncio.run(main()) 