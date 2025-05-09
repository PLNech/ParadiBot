#!/usr/bin/env python
"""
Paradiso Merge Sources Script

This script downloads movie data from various sources, processes it,
and updates Algolia indices for the Paradiso movie voting system.

Usage:
    python merge_sources.py --admin-key YOUR_ADMIN_API_KEY --app-id YOUR_APP_ID

Requirements:
    - Python 3.7+
    - algoliasearch package (pip install algoliasearch)
    - requests package (pip install requests)
"""

import argparse
import json
import os
import time
import hashlib
import requests
from algoliasearch.search_client import SearchClient
from datetime import datetime
import urllib.parse

# Define the data sources
WIKIPEDIA_MOVIE_DATA_BASE_URL = "https://raw.githubusercontent.com/prust/wikipedia-movie-data/master/movies-"
WIKIPEDIA_MOVIE_DECADES = ["1900s", "1910s", "1920s", "1930s", "1940s", "1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]
FROSCH_MOVIES_URL = "https://frosch.cosy.sbg.ac.at/datasets/json/movies"
FROSCH_MOVIES_FILENAME = "movies_frosch.json"  # Use the filename you already have
VEGA_MOVIES_URL = "https://github.com/vega/vega/raw/main/docs/data/movies.json"

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Merge movie data from various sources into Algolia indices')
    parser.add_argument('--admin-key', required=True, help='Algolia Admin API Key')
    parser.add_argument('--app-id', required=True, help='Algolia Application ID')
    parser.add_argument('--data-dir', default='./data', help='Directory to store downloaded data')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for Algolia operations')
    parser.add_argument('--skip-download', action='store_true', help='Skip downloading data files (use existing files)')
    return parser.parse_args()

def ensure_data_directory(data_dir):
    """Ensure the data directory exists."""
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    return data_dir

def download_file(url, destination):
    """Download a file from a URL to a destination."""
    try:
        print(f"Downloading {url} to {destination}...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"✅ Downloaded {url}")
        return True
    except Exception as e:
        print(f"❌ Error downloading {url}: {e}")
        return False

def download_large_file(url, destination, chunk_size=8192):
    """Download a large file in chunks and show progress."""
    try:
        print(f"Downloading large file {url} to {destination}...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Get file size if provided in headers
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                f.write(chunk)
                downloaded += len(chunk)
                
                # Print progress
                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    print(f"\rDownloading: {percent:.2f}% ({downloaded}/{total_size} bytes)", end="", flush=True)
                else:
                    print(f"\rDownloading: {downloaded} bytes", end="", flush=True)
        
        print()  # New line after progress
        print(f"✅ Downloaded {url}")
        return True
    except Exception as e:
        print(f"❌ Error downloading {url}: {e}")
        return False

def download_all_data_sources(data_dir, skip_download=False):
    """Download all data sources to the specified directory."""
    if skip_download:
        print("Skipping downloads as requested...")
        return
    
    print(f"Downloading data sources to {data_dir}...")
    
    # Download Wikipedia movie data (for each decade)
    for decade in WIKIPEDIA_MOVIE_DECADES:
        url = f"{WIKIPEDIA_MOVIE_DATA_BASE_URL}{decade}.json"
        destination = os.path.join(data_dir, f"wikipedia-movies-{decade}.json")
        
        if not os.path.exists(destination):
            download_file(url, destination)
        else:
            print(f"⏭️ File {destination} already exists, skipping")
    
    # Download Vega movies data
    vega_destination = os.path.join(data_dir, "vega-movies.json")
    if not os.path.exists(vega_destination):
        download_file(VEGA_MOVIES_URL, vega_destination)
    else:
        print(f"⏭️ File {vega_destination} already exists, skipping")
    
    # Check for Frosch movies data (large file, 4.8GB)
    frosch_destination = os.path.join(data_dir, FROSCH_MOVIES_FILENAME)
    
    # First, check alternative locations where the file might exist
    alt_paths = [
        # Check if it exists in the data directory with the new name
        frosch_destination,
        # Check the absolute path mentioned by the user
        "/home/pln/Work/www/next/data/movies_frosch.json",
        # Check if it's in the current directory
        FROSCH_MOVIES_FILENAME,
        # Check if it's in a parent directory
        os.path.join("..", "data", FROSCH_MOVIES_FILENAME),
        # Try the original script name
        os.path.join(data_dir, "frosch-movies.json")
    ]
    
    # Check all potential locations
    for alt_path in alt_paths:
        if os.path.exists(alt_path):
            print(f"✅ Found existing Frosch movies file at {alt_path}")
            # Copy or symlink the file to our data directory if it's not already there
            if alt_path != frosch_destination and not os.path.exists(frosch_destination):
                print(f"Creating symlink from {alt_path} to {frosch_destination}")
                try:
                    os.symlink(os.path.abspath(alt_path), frosch_destination)
                except:
                    print(f"Unable to create symlink, will use the original file at {alt_path}")
                    frosch_destination = alt_path
            else:
                frosch_destination = alt_path
            break
    else:
        # If we didn't find the file anywhere, ask if we should download it
        if not os.path.exists(frosch_destination):
            # This is a large file so we'll ask for confirmation before downloading
            confirm = input(f"The Frosch movies dataset is 4.8GB. Do you want to download it? (y/n): ")
            if confirm.lower() == 'y':
                download_large_file(FROSCH_MOVIES_URL, frosch_destination)
            else:
                print("Skipping Frosch movies download")
    
    print("✅ Data download complete")

def process_wikipedia_movies(data_dir):
    """Process Wikipedia movie data from all decades."""
    print("Processing Wikipedia movie data...")
    all_movies = []
    
    for decade in WIKIPEDIA_MOVIE_DECADES:
        file_path = os.path.join(data_dir, f"wikipedia-movies-{decade}.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    decade_movies = json.load(f)
                
                print(f"Found {len(decade_movies)} movies from {decade}")
                all_movies.extend(decade_movies)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
    
    print(f"Total Wikipedia movies: {len(all_movies)}")
    return all_movies

def generate_object_id(title, year):
    """Generate a consistent object ID based on title and year."""
    # Ensure inputs are strings
    title_str = str(title)
    year_str = str(year)
    
    # Create a unique hash from title and year
    combined = f"{title_str}_{year_str}".encode('utf-8')
    hash_obj = hashlib.md5(combined).hexdigest()
    return hash_obj[:9]  # Use first 9 characters of hash

def convert_wikipedia_to_algolia_format(wikipedia_movies):
    """Convert Wikipedia movie data to Algolia format."""
    print("Converting Wikipedia movies to Algolia format...")
    algolia_movies = []
    
    for movie in wikipedia_movies:
        # Skip if missing essential data
        if not movie.get('title') or not movie.get('year'):
            continue
        
        # Ensure title is a string
        title = str(movie.get('title', ''))
        
        # Ensure year is an integer or None
        try:
            year = int(movie.get('year', 0))
        except (ValueError, TypeError):
            year = 0
        
        # Create a unique object ID
        object_id = generate_object_id(title, year)
        
        # Extract and process image URL
        image_url = None
        if movie.get('thumbnail'):
            image_url = movie.get('thumbnail')
        
        # Convert genres array if needed
        genres = movie.get('genres', [])
        if not isinstance(genres, list):
            genres = [str(genres)] if genres else []
        else:
            # Ensure all genres are strings
            genres = [str(g) for g in genres]
        
        # Convert cast array if needed
        actors = movie.get('cast', [])
        if not isinstance(actors, list):
            actors = [str(actors)] if actors else []
        else:
            # Ensure all actors are strings
            actors = [str(a) for a in actors]
        
        # Create actor_facets (format: "image_url|actor_name")
        # Since Wikipedia doesn't have actor images, we'll use placeholders
        actor_facets = [f"|{actor}" for actor in actors]
        
        # Extract or default safely
        extract = str(movie.get('extract', ''))
        
        # Convert the Wikipedia data to the Algolia format
        algolia_movie = {
            "objectID": object_id,
            "title": title,
            "alternative_titles": [],  # No alternative titles in Wikipedia data
            "year": year,
            "image": image_url,
            "color": "#0C0E11",  # Default color
            "score": 0.0,  # No score in Wikipedia data
            "rating": 0,  # No rating in Wikipedia data
            "actors": actors,
            "actor_facets": actor_facets,
            "genre": genres,
            "source": "wikipedia",
            "extract": extract
        }
        
        # Add Wikipedia-specific fields
        if movie.get('href'):
            algolia_movie["wikipedia_href"] = str(movie.get('href'))
        
        algolia_movies.append(algolia_movie)
    
    print(f"Converted {len(algolia_movies)} Wikipedia movies to Algolia format")
    return algolia_movies

def process_vega_movies(data_dir):
    """Process Vega movie data."""
    print("Processing Vega movie data...")
    vega_movies = []
    
    file_path = os.path.join(data_dir, "vega-movies.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                vega_movies = json.load(f)
            
            print(f"Found {len(vega_movies)} movies from Vega dataset")
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
    
    return vega_movies

def convert_vega_to_algolia_format(vega_movies):
    """Convert Vega movie data to Algolia format."""
    print("Converting Vega movies to Algolia format...")
    algolia_movies = []
    
    for movie in vega_movies:
        # Skip if missing essential data
        if not movie.get('Title'):
            continue
        
        # Make sure the title is a string
        title = str(movie.get('Title', ''))
        
        # Extract year from Release Date if available
        year = None
        if movie.get('Release Date'):
            try:
                # Format is typically "Jun 12 1998"
                year = int(movie.get('Release Date').split()[-1])
            except (ValueError, IndexError):
                year = None
        
        # Create a unique object ID
        object_id = generate_object_id(title, year or 0)
        
        # Determine genre
        genre = []
        if movie.get('Major Genre'):
            genre = [str(movie.get('Major Genre'))]
        
        # Get director
        director = str(movie.get('Director', '')) if movie.get('Director') else ''
        
        # Convert IMDB rating to float safely
        try:
            imdb_rating = float(movie.get('IMDB Rating', 0.0) or 0.0)
        except (ValueError, TypeError):
            imdb_rating = 0.0
        
        # Convert votes to int safely
        try:
            imdb_votes = int(movie.get('IMDB Votes', 0) or 0)
        except (ValueError, TypeError):
            imdb_votes = 0
            
        # Convert gross values to int safely
        try:
            us_gross = int(movie.get('US Gross', 0) or 0)
        except (ValueError, TypeError):
            us_gross = 0
            
        try:
            worldwide_gross = int(movie.get('Worldwide Gross', 0) or 0)
        except (ValueError, TypeError):
            worldwide_gross = 0
            
        try:
            budget = int(movie.get('Production Budget', 0) or 0)
        except (ValueError, TypeError):
            budget = 0
            
        try:
            running_time = int(movie.get('Running Time min', 0) or 0)
        except (ValueError, TypeError):
            running_time = 0
        
        # Convert the Vega data to the Algolia format
        algolia_movie = {
            "objectID": object_id,
            "title": title,
            "alternative_titles": [],  # No alternative titles in Vega data
            "year": year,
            "image": None,  # No images in Vega data
            "color": "#0C0E11",  # Default color
            "score": imdb_rating,
            "rating": 0,  # No direct rating in Vega data
            "actors": [],  # No actors in Vega data
            "actor_facets": [],  # No actor images in Vega data
            "genre": genre,
            "source": "vega",
            "imdb_rating": imdb_rating,
            "imdb_votes": imdb_votes,
            "us_gross": us_gross,
            "worldwide_gross": worldwide_gross,
            "budget": budget,
            "mpaa_rating": str(movie.get('MPAA Rating', '')),
            "running_time": running_time,
            "distributor": str(movie.get('Distributor', ''))
        }
        
        # Add director if available
        if director:
            algolia_movie["director"] = director
        
        algolia_movies.append(algolia_movie)
    
    print(f"Converted {len(algolia_movies)} Vega movies to Algolia format")
    return algolia_movies

def process_frosch_reviews(data_dir, batch_size=1000):
    """Process Frosch review data in batches (streaming due to large file size)."""
    print("Processing Frosch review data (streaming)...")
    file_path = os.path.join(data_dir, FROSCH_MOVIES_FILENAME)
    
    if not os.path.exists(file_path):
        # Try alternative paths if the file isn't found in the data directory
        alt_paths = [
            # Check if it exists in the current directory
            FROSCH_MOVIES_FILENAME,
            # Check the absolute path mentioned by the user
            "/home/pln/Work/www/next/data/movies_frosch.json",
            # Check if it's in a parent directory
            os.path.join("..", "data", FROSCH_MOVIES_FILENAME),
            # Try the original script name
            os.path.join(data_dir, "frosch-movies.json")
        ]
        
        for alt_path in alt_paths:
            if os.path.exists(alt_path):
                file_path = alt_path
                print(f"✅ Found Frosch movies file at {file_path}")
                break
        else:
            print(f"❌ Frosch movies file not found in any expected location, skipping reviews processing")
            return []
    
    reviews = []
    review_count = 0
    batch_count = 0
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Process the file line by line to handle the large size
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    # Handle possible JSON formatting issues
                    # Remove comma at the end if present (last item in a list)
                    if line.endswith(','):
                        line = line[:-1]
                    
                    # Remove array brackets if present
                    if line.startswith('['):
                        line = line[1:]
                    if line.endswith(']'):
                        line = line[:-1]
                    
                    # Parse the JSON object
                    review = json.loads(line)
                    
                    # Skip if missing essential data
                    if not review.get('asin') or not review.get('reviewText'):
                        continue
                    
                    # Process the review
                    reviews.append(review)
                    review_count += 1
                    
                    # Process in batches to manage memory
                    if len(reviews) >= batch_size:
                        batch_count += 1
                        print(f"Processed batch {batch_count} ({review_count} reviews so far)")
                        yield reviews
                        reviews = []
                    
                except json.JSONDecodeError as e:
                    print(f"Error parsing line in Frosch reviews: {e}")
                    continue
                except Exception as e:
                    print(f"Unexpected error processing line in Frosch reviews: {e}")
                    continue
        
        # Yield any remaining reviews
        if reviews:
            batch_count += 1
            print(f"Processed final batch {batch_count} ({review_count} reviews total)")
            yield reviews
    
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        if reviews:
            yield reviews

def convert_reviews_to_algolia_format(reviews):
    """Convert review data to Algolia format."""
    print(f"Converting {len(reviews)} reviews to Algolia format...")
    algolia_reviews = []
    
    for review in reviews:
        # Create a unique object ID
        reviewer_product = f"{review.get('reviewerID', '')}_{review.get('asin', '')}"
        object_id = hashlib.md5(reviewer_product.encode('utf-8')).hexdigest()[:9]
        
        # Extract style information
        style = review.get('style', {})
        format_type = style.get('Format:', '') if isinstance(style, dict) else ''
        
        # Convert unix timestamp to readable date if available
        review_date = None
        if review.get('unixReviewTime'):
            review_date = datetime.fromtimestamp(review.get('unixReviewTime')).isoformat()
        
        # Parse the rating
        try:
            rating = float(review.get('overall', 0.0))
        except (ValueError, TypeError):
            rating = 0.0
        
        # Parse votes
        try:
            votes = int(review.get('vote', '0'))
        except (ValueError, TypeError):
            votes = 0
        
        # Convert the review data to the Algolia format
        algolia_review = {
            "objectID": object_id,
            "movie_asin": review.get('asin', ''),  # Foreign key to link to movie
            "rating": rating,
            "verified": bool(review.get('verified', False)),
            "review_time": review.get('reviewTime', ''),
            "reviewer_id": review.get('reviewerID', ''),
            "reviewer_name": review.get('reviewerName', ''),
            "review_text": review.get('reviewText', ''),
            "summary": review.get('summary', ''),
            "format": format_type,
            "votes": votes,
            "review_date": review_date,
            "source": "frosch",
            "timestamp": int(time.time())
        }
        
        # Add image URLs if available
        if review.get('image') and isinstance(review.get('image'), list):
            algolia_review["image_urls"] = review.get('image')
        
        algolia_reviews.append(algolia_review)
    
    return algolia_reviews

def get_existing_algolia_movies(client, index_name):
    """Get all existing movies from the Algolia index."""
    print(f"Retrieving existing movies from Algolia index {index_name}...")
    
    index = client.init_index(index_name)
    hits = []
    
    try:
        # Browse all objects in the index
        browser = index.browse_objects({"attributesToRetrieve": ["objectID", "title", "year"]})
        for hit in browser:
            hits.append(hit)
        
        print(f"Found {len(hits)} existing movies in Algolia index")
        return hits
    except Exception as e:
        print(f"Error retrieving movies from Algolia: {e}")
        return []

def create_movie_lookup_map(existing_movies):
    """Create a lookup map for existing movies."""
    lookup_map = {}
    
    for movie in existing_movies:
        # Convert title to string and then lowercase to handle any numeric titles
        title = str(movie.get('title', ''))
        year = movie.get('year', '')
        
        # Use title and year as key for lookup
        key = f"{title.lower()}_{year}"
        lookup_map[key] = movie.get('objectID')
    
    # Also create a lookup by objectID
    id_map = {movie.get('objectID'): True for movie in existing_movies}
    
    return lookup_map, id_map

def update_algolia_movies_index(client, index_name, new_movies, title_lookup_map, id_lookup_map, batch_size=1000):
    """Update the Algolia movies index with new movies."""
    print(f"Updating Algolia movies index {index_name}...")
    
    index = client.init_index(index_name)
    additions = []
    
    for movie in new_movies:
        # Check if the movie already exists by ID
        if movie.get('objectID') in id_lookup_map:
            continue
        
        # Ensure title is a string before calling lower()
        title = str(movie.get('title', ''))
        year = movie.get('year', '')
        
        # Check if the movie already exists by title+year
        key = f"{title.lower()}_{year}"
        if key in title_lookup_map:
            continue
        
        # Add the movie to the batch
        additions.append(movie)
    
    print(f"Adding {len(additions)} new movies to Algolia index")
    
    # Save movies in batches to avoid hitting Algolia limits
    for i in range(0, len(additions), batch_size):
        batch = additions[i:i+batch_size]
        try:
            index.save_objects(batch)
            print(f"✅ Added batch {i//batch_size + 1}/{(len(additions) + batch_size - 1)//batch_size} of movies to {index_name} index")
        except Exception as e:
            print(f"❌ Error adding batch to {index_name} index: {e}")
            # Print a sample of the problematic records
            if batch:
                print(f"Sample record that might be causing issues: {batch[0]}")
                
    return len(additions)

def create_reviews_index(client, index_name):
    """Create and configure the reviews index."""
    print(f"Creating and configuring Algolia reviews index {index_name}...")
    
    reviews_index = client.init_index(index_name)
    
    # Configure reviews index settings
    reviews_settings = {
        "searchableAttributes": [
            "review_text",
            "summary",
            "reviewer_name",
            "movie_asin"
        ],
        "attributesForFaceting": [
            "movie_asin",
            "rating",
            "verified",
            "format"
        ],
        # Ranking based on rating and recency
        "customRanking": [
            "desc(rating)",
            "desc(timestamp)"
        ],
        "highlightPreTag": "<em>",
        "highlightPostTag": "</em>",
        "hitsPerPage": 20
    }
    
    # Apply settings to the index
    try:
        reviews_index.set_settings(reviews_settings)
        print(f"✅ Created and configured {index_name} index")
        return True
    except Exception as e:
        print(f"❌ Error configuring {index_name} index: {e}")
        return False

def update_algolia_reviews_index(client, index_name, reviews_batch):
    """Update the Algolia reviews index with new reviews."""
    print(f"Updating Algolia reviews index {index_name} with {len(reviews_batch)} reviews...")
    
    index = client.init_index(index_name)
    
    try:
        index.save_objects(reviews_batch)
        print(f"✅ Added {len(reviews_batch)} reviews to {index_name} index")
        return True
    except Exception as e:
        print(f"❌ Error adding reviews to {index_name} index: {e}")
        return False

def main():
    """Main function to orchestrate the data processing and Algolia updates."""
    args = parse_args()
    
    print("\n== Paradiso Data Merge Process ==")
    print(f"Running from {os.getcwd()}")
    
    # Ensure data directory exists
    data_dir = ensure_data_directory(args.data_dir)
    
    print(f"Using data directory: {os.path.abspath(data_dir)}")
    # Check if the user's existing Frosch movies file is accessible
    try:
        known_path = "/home/pln/Work/www/next/data/movies_frosch.json"
        if os.path.exists(known_path):
            print(f"✅ Found existing Frosch movies file at {known_path}")
            file_size_gb = os.path.getsize(known_path) / (1024**3)
            print(f"   File size: {file_size_gb:.2f} GB")
    except:
        # If we can't access that path, continue with normal download process
        pass

    # Download all data sources
    download_all_data_sources(data_dir, args.skip_download)
    
    # Initialize the Algolia client
    client = SearchClient.create(args.app_id, args.admin_key)
    
    # Get existing movies from Algolia
    existing_movies = get_existing_algolia_movies(client, "paradiso_movies")
    title_lookup_map, id_lookup_map = create_movie_lookup_map(existing_movies)
    
    # Process Wikipedia movies
    wikipedia_movies = process_wikipedia_movies(data_dir)
    algolia_wikipedia_movies = convert_wikipedia_to_algolia_format(wikipedia_movies)
    
    # Update Algolia movies index with Wikipedia movies first
    added_wikipedia = update_algolia_movies_index(client, "paradiso_movies", algolia_wikipedia_movies, 
                              title_lookup_map, id_lookup_map, args.batch_size)
    
    # Update title_lookup_map with newly added Wikipedia movies
    # This prevents duplicate entries when we add the Vega movies
    if added_wikipedia > 0:
        print(f"Updating lookup maps after adding {added_wikipedia} Wikipedia movies")
        existing_movies = get_existing_algolia_movies(client, "paradiso_movies")
        title_lookup_map, id_lookup_map = create_movie_lookup_map(existing_movies)
    
    # Process Vega movies
    vega_movies = process_vega_movies(data_dir)
    algolia_vega_movies = convert_vega_to_algolia_format(vega_movies)
    
    # Update Algolia movies index with Vega movies
    update_algolia_movies_index(client, "paradiso_movies", algolia_vega_movies, 
                              title_lookup_map, id_lookup_map, args.batch_size)
    
    # Create and configure reviews index if it doesn't exist
    create_reviews_index(client, "paradiso_reviews")
    
    # Process and update Frosch reviews in batches (streaming)
    for reviews_batch in process_frosch_reviews(data_dir, args.batch_size):
        algolia_reviews_batch = convert_reviews_to_algolia_format(reviews_batch)
        update_algolia_reviews_index(client, "paradiso_reviews", algolia_reviews_batch)
    
    print("\n== Data Merge Process Complete ==")
    print("Your Algolia indices have been updated with new movies and reviews!")

if __name__ == "__main__":
    main()