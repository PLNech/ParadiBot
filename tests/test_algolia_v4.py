#!/usr/bin/env python
"""
Test Suite for Paradiso Bot Algolia v4 Integration

These tests validate the core Algolia functions used by the bot.
To run, you'll need valid Algolia credentials with test indices.

Use:
pytest -xvs tests/test_algolia_v4.py
"""

import os
import time
import random
import unittest
from unittest import mock
import pytest
from dotenv import load_dotenv

from algoliasearch.search_client import SearchClient
from utils.algolia_utils import (
    add_movie_to_algolia, vote_for_movie, get_movie_by_id,
    find_movie_by_title, search_movies_for_vote, get_top_movies, get_all_movies,
    generate_user_token, _is_float, _check_movie_exists
)

# Load environment variables from .env file
load_dotenv()

# Use test indices instead of production ones
TEST_MOVIES_INDEX = os.getenv('TEST_ALGOLIA_MOVIES_INDEX', 'test_paradiso_movies')
TEST_VOTES_INDEX = os.getenv('TEST_ALGOLIA_VOTES_INDEX', 'test_paradiso_votes')

# Skip tests if no Algolia credentials are found
pytestmark = pytest.mark.skipif(
    not os.getenv('ALGOLIA_APP_ID') or not os.getenv('ALGOLIA_API_KEY'),
    reason="Algolia credentials not found in environment"
)


class TestAlgoliaV4Integration(unittest.TestCase):
    """Test suite for Algolia v4 client integration."""

    @classmethod
    def setUpClass(cls):
        """Set up Algolia client and test data."""
        # Initialize Algolia client
        cls.algolia_app_id = os.getenv('ALGOLIA_APP_ID')
        cls.algolia_api_key = os.getenv('ALGOLIA_API_KEY')
        cls.client = SearchClient.create(cls.algolia_app_id, cls.algolia_api_key)
        
        # Clear test indices
        cls._clear_test_indices()
        
        # Add test data
        cls.test_movies = cls._create_test_movies()
        for movie in cls.test_movies:
            cls.client.save_objects(TEST_MOVIES_INDEX, [movie])
        
        # Wait for indexing
        time.sleep(2)

    @classmethod
    def tearDownClass(cls):
        """Clean up test data."""
        cls._clear_test_indices()
    
    @classmethod
    def _clear_test_indices(cls):
        """Clear test indices."""
        try:
            # Clear movies index
            cls.client.clear_objects(TEST_MOVIES_INDEX)
            # Clear votes index
            cls.client.clear_objects(TEST_VOTES_INDEX)
            # Wait for clearing to take effect
            time.sleep(2)
        except Exception as e:
            print(f"Error clearing test indices: {e}")
    
    @classmethod
    def _create_test_movies(cls):
        """Create test movie data."""
        return [
            {
                "objectID": "test_movie_1",
                "title": "Test Movie 1",
                "originalTitle": "Test Movie 1",
                "year": 2020,
                "director": "Test Director",
                "actors": ["Actor 1", "Actor 2"],
                "genre": ["Action", "Comedy"],
                "plot": "A test movie plot.",
                "image": None,
                "rating": 7.5,
                "votes": 5,
                "addedDate": int(time.time()),
                "addedBy": generate_user_token("test_user")
            },
            {
                "objectID": "test_movie_2",
                "title": "Test Movie 2",
                "originalTitle": "Test Movie 2",
                "year": 2021,
                "director": "Test Director 2",
                "actors": ["Actor 3", "Actor 4"],
                "genre": ["Drama", "Thriller"],
                "plot": "Another test movie plot.",
                "image": None,
                "rating": 8.0,
                "votes": 3,
                "addedDate": int(time.time()),
                "addedBy": generate_user_token("test_user")
            },
            {
                "objectID": "test_movie_3",
                "title": "Different Title",
                "originalTitle": "Different Title",
                "year": 2019,
                "director": "Another Director",
                "actors": ["Actor 5", "Actor 6"],
                "genre": ["Comedy", "Romance"],
                "plot": "Yet another test movie plot.",
                "image": None,
                "rating": 6.5,
                "votes": 2,
                "addedDate": int(time.time()),
                "addedBy": generate_user_token("test_user")
            }
        ]
    
    async def test_search_movies_for_vote(self):
        """Test searching for movies to vote for."""
        # Test exact match
        results = search_movies_for_vote(self.client, TEST_MOVIES_INDEX, "Test Movie 1")
        assert results["nbHits"] > 0
        assert any(hit["title"] == "Test Movie 1" for hit in results["hits"])
        
        # Test partial match
        results = search_movies_for_vote(self.client, TEST_MOVIES_INDEX, "Test")
        assert results["nbHits"] >= 2
        
        # Test no match
        results = search_movies_for_vote(self.client, TEST_MOVIES_INDEX, "Nonexistent Movie")
        assert results["nbHits"] == 0
    
    async def test_find_movie_by_title(self):
        """Test finding a movie by title."""
        # Test exact match
        movie = await find_movie_by_title(self.client, TEST_MOVIES_INDEX, "Test Movie 1")
        assert movie is not None
        assert movie["title"] == "Test Movie 1"
        
        # Test partial match
        movie = await find_movie_by_title(self.client, TEST_MOVIES_INDEX, "Different")
        assert movie is not None
        assert movie["title"] == "Different Title"
        
        # Test no match
        movie = await find_movie_by_title(self.client, TEST_MOVIES_INDEX, "Nonexistent Movie")
        assert movie is None
    
    async def test_get_movie_by_id(self):
        """Test getting a movie by ID."""
        movie = await get_movie_by_id(self.client, TEST_MOVIES_INDEX, "test_movie_1")
        assert movie is not None
        assert movie["title"] == "Test Movie 1"
        
        # Test non-existent ID
        movie = await get_movie_by_id(self.client, TEST_MOVIES_INDEX, "nonexistent_id")
        assert movie is None
    
    async def test_add_movie_to_algolia(self):
        """Test adding a movie to Algolia."""
        new_movie = {
            "objectID": "test_movie_4",
            "title": "New Test Movie",
            "originalTitle": "New Test Movie",
            "year": 2022,
            "director": "New Director",
            "actors": ["New Actor 1", "New Actor 2"],
            "genre": ["Sci-Fi", "Action"],
            "plot": "A new test movie plot.",
            "image": None,
            "rating": 9.0,
            "votes": 0,
            "addedDate": int(time.time()),
            "addedBy": generate_user_token("test_user")
        }
        
        add_movie_to_algolia(self.client, TEST_MOVIES_INDEX, new_movie)
        
        # Wait for indexing
        time.sleep(2)
        
        # Verify the movie was added
        movie = await get_movie_by_id(self.client, TEST_MOVIES_INDEX, "test_movie_4")
        assert movie is not None
        assert movie["title"] == "New Test Movie"
    
    async def test_vote_for_movie(self):
        """Test voting for a movie."""
        movie_id = "test_movie_1"
        user_id = "test_voter_1"
        
        # Check initial votes
        movie_before = await get_movie_by_id(self.client, TEST_MOVIES_INDEX, movie_id)
        initial_votes = movie_before["votes"]
        
        # Vote for the movie
        success, result = await vote_for_movie(self.client, TEST_MOVIES_INDEX, TEST_VOTES_INDEX, movie_id, user_id)
        
        # Verify vote was recorded
        assert success is True
        assert isinstance(result, dict)
        assert result["votes"] == initial_votes + 1
        
        # Try voting again with the same user
        success, result = await vote_for_movie(self.client, TEST_MOVIES_INDEX, TEST_VOTES_INDEX, movie_id, user_id)
        
        # Verify duplicate vote was prevented
        assert success is False
        assert result == "Already voted" or isinstance(result, dict)
    
    async def test_get_top_movies(self):
        """Test getting top voted movies."""
        top_movies = await get_top_movies(self.client, TEST_MOVIES_INDEX, 5)
        
        # Verify we get results
        assert len(top_movies) > 0
        
        # Verify they're sorted by votes (descending)
        for i in range(len(top_movies) - 1):
            assert top_movies[i]["votes"] >= top_movies[i + 1]["votes"]
    
    async def test_get_all_movies(self):
        """Test getting all movies."""
        all_movies = await get_all_movies(self.client, TEST_MOVIES_INDEX)
        
        # Verify we get all test movies
        assert len(all_movies) >= 4  # Including the one added in test_add_movie_to_algolia
        
        # Verify they're sorted by votes (descending)
        for i in range(len(all_movies) - 1):
            if all_movies[i]["votes"] == all_movies[i + 1]["votes"]:
                # If votes are equal, they should be sorted by title
                continue
            assert all_movies[i]["votes"] >= all_movies[i + 1]["votes"]
    
    async def test_check_movie_exists(self):
        """Test checking if a movie exists."""
        # Test exact match
        existing_movie = await _check_movie_exists(self.client, TEST_MOVIES_INDEX, "Test Movie 1")
        assert existing_movie is not None
        assert existing_movie["title"] == "Test Movie 1"
        
        # Test no match
        existing_movie = await _check_movie_exists(self.client, TEST_MOVIES_INDEX, "Nonexistent Movie")
        assert existing_movie is None

if __name__ == "__main__":
    unittest.main() 