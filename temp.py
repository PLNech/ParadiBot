#!/usr/bin/env python3
"""
TMDB API Test Script

This script tests the TMDB API integration by fetching trending movies, 
top rated movies, and movie details.

Usage:
    python temp.py --api-key YOUR_TMDB_API_KEY
"""

import argparse
import json
import requests
from pprint import pprint

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Test TMDB API integration')
    parser.add_argument('--api-key', required=True, help='TMDB API Key')
    return parser.parse_args()

def get_trending_movies(api_key):
    """Get trending movies for the week."""
    url = f"https://api.themoviedb.org/3/trending/movie/week?api_key={api_key}"
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Error fetching trending movies: {response.status_code}")
        return None
    
    return response.json()

def get_top_rated_movies(api_key):
    """Get top rated movies."""
    url = f"https://api.themoviedb.org/3/movie/top_rated?api_key={api_key}"
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Error fetching top rated movies: {response.status_code}")
        return None
    
    return response.json()

def get_movie_details(api_key, movie_id):
    """Get detailed information about a movie."""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={api_key}&append_to_response=credits,videos,images,keywords,reviews,similar,recommendations"
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Error fetching movie details: {response.status_code}")
        return None
    
    return response.json()

def main():
    """Main function."""
    args = parse_args()
    api_key = args.api_key
    
    print("Testing TMDB API Integration")
    print("===========================")
    
    # Test trending movies
    print("\nFetching trending movies...")
    trending = get_trending_movies(api_key)
    if trending:
        print(f"Successfully fetched {len(trending['results'])} trending movies")
        print("First movie:")
        pprint(trending['results'][0])
    
    # Test top rated movies
    print("\nFetching top rated movies...")
    top_rated = get_top_rated_movies(api_key)
    if top_rated:
        print(f"Successfully fetched {len(top_rated['results'])} top rated movies")
        print("First movie:")
        pprint(top_rated['results'][0])
    
    # Test movie details
    if trending and trending['results']:
        movie_id = trending['results'][0]['id']
        print(f"\nFetching details for movie ID {movie_id}...")
        details = get_movie_details(api_key, movie_id)
        if details:
            print(f"Successfully fetched details for '{details['title']}'")
            print("Movie information:")
            print(f"Title: {details['title']}")
            print(f"Release Date: {details['release_date']}")
            print(f"Runtime: {details['runtime']} minutes")
            print(f"Vote Average: {details['vote_average']}")
            print(f"Overview: {details['overview'][:100]}...")
            
            # Check for trailer
            if details['videos']['results']:
                trailers = [v for v in details['videos']['results'] if v['type'] == 'Trailer' and v['site'] == 'YouTube']
                if trailers:
                    print(f"Trailer: https://www.youtube.com/watch?v={trailers[0]['key']}")
            
            # Check for cast
            if details['credits']['cast']:
                cast = [actor['name'] for actor in details['credits']['cast'][:5]]
                print(f"Cast: {', '.join(cast)}")
            
            # Check for director
            if details['credits']['crew']:
                directors = [crew['name'] for crew in details['credits']['crew'] if crew['job'] == 'Director']
                if directors:
                    print(f"Director(s): {', '.join(directors)}")

if __name__ == "__main__":
    main() 