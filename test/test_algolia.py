import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import paradiso_bot
from paradiso_bot import ParadisoBot


class TestAlgoliaIntegration(unittest.TestCase):
    """Test case for Algolia integration."""
    
    def setUp(self):
        """Set up test environment."""
        self.bot = ParadisoBot(
            discord_token="fake_token",
            algolia_app_id="fake_app_id",
            algolia_api_key="fake_api_key",
            algolia_movies_index="fake_movies_index",
            algolia_votes_index="fake_votes_index"
        )
        
        # Mock the algolia client and indices
        self.bot.algolia_client = MagicMock()
        self.bot.movies_index = MagicMock()
        self.bot.votes_index = MagicMock()
    
    async def test_add_movie_to_algolia(self):
        """Test adding a movie to Algolia."""
        # Mock data
        movie_data = {
            "id": "tt1234567",
            "title": "Test Movie",
            "original_title": "Test Movie Original",
            "year": 2022,
            "director": "Test Director",
            "actors": ["Actor 1", "Actor 2"],
            "genre": ["Action", "Drama"],
            "plot": "This is a test movie plot.",
            "poster": "https://example.com/poster.jpg",
            "imdb_rating": 8.5,
            "imdb_id": "tt1234567",
            "tmdb_id": "12345",
            "source": "test"
        }
        
        # Mock Algolia save_object
        self.bot.movies_index.save_object.return_value = True
        
        # Test the function
        result = await self.bot.add_movie_to_algolia(movie_data, "123456789")
        
        # Assertions
        self.bot.movies_index.save_object.assert_called_once()
        self.assertEqual(result["objectID"], "tt1234567")
        self.assertEqual(result["title"], "Test Movie")
        self.assertEqual(result["addedBy"], "discord_123456789")
    
    async def test_find_movie_by_title(self):
        """Test finding a movie by title."""
        # Mock data
        mock_search_result = {
            "hits": [
                {
                    "objectID": "tt1234567",
                    "title": "Test Movie",
                    "year": 2022
                },
                {
                    "objectID": "tt7654321",
                    "title": "Test Movie 2",
                    "year": 2021
                }
            ],
            "nbHits": 2
        }
        
        # Mock Algolia search
        self.bot.movies_index.search = AsyncMock(return_value=mock_search_result)
        
        # Test with exact match
        result = await self.bot.find_movie_by_title("Test Movie")
        
        # Assertions
        self.bot.movies_index.search.assert_called_once()
        self.assertEqual(result["objectID"], "tt1234567")
        
        # Reset mock
        self.bot.movies_index.search.reset_mock()
        
        # Test with no match (return first result)
        result = await self.bot.find_movie_by_title("Unknown Movie")
        
        # Assertions
        self.bot.movies_index.search.assert_called_once()
        self.assertEqual(result["objectID"], "tt1234567")  # Should return first hit
    
    async def test_vote_for_movie(self):
        """Test voting for a movie."""
        # Mock votes index search (user hasn't voted)
        self.bot.votes_index.search.return_value = {"nbHits": 0}
        
        # Mock saving vote
        self.bot.votes_index.save_object.return_value = True
        
        # Mock updating movie
        self.bot.movies_index.partial_update_object.return_value = True
        
        # Test the function
        result = await self.bot.vote_for_movie("tt1234567", "123456789")
        
        # Assertions
        self.assertTrue(result)
        self.bot.votes_index.search.assert_called_once()
        self.bot.votes_index.save_object.assert_called_once()
        self.bot.movies_index.partial_update_object.assert_called_once_with({
            "objectID": "tt1234567",
            "votes": {
                "_operation": "Increment",
                "value": 1
            }
        })
        
        # Reset mocks
        self.bot.votes_index.search.reset_mock()
        self.bot.votes_index.save_object.reset_mock()
        self.bot.movies_index.partial_update_object.reset_mock()
        
        # Test when user already voted
        self.bot.votes_index.search.return_value = {"nbHits": 1}
        
        # Test the function
        result = await self.bot.vote_for_movie("tt1234567", "123456789")
        
        # Assertions
        self.assertFalse(result)
        self.bot.votes_index.search.assert_called_once()
        self.bot.votes_index.save_object.assert_not_called()
        self.bot.movies_index.partial_update_object.assert_not_called()
    
    async def test_get_top_movies(self):
        """Test getting top movies."""
        # Mock data
        mock_search_result = {
            "hits": [
                {
                    "objectID": "tt1234567",
                    "title": "Test Movie 1",
                    "votes": 10
                },
                {
                    "objectID": "tt7654321",
                    "title": "Test Movie 2",
                    "votes": 8
                },
                {
                    "objectID": "tt9876543",
                    "title": "Test Movie 3",
                    "votes": 5
                }
            ],
            "nbHits": 3
        }
        
        # Mock Algolia search
        self.bot.movies_index.search = AsyncMock(return_value=mock_search_result)
        
        # Test the function
        result = await self.bot.get_top_movies(count=3)
        
        # Assertions
        self.bot.movies_index.search.assert_called_once()
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["objectID"], "tt1234567")
        self.assertEqual(result[0]["votes"], 10)


if __name__ == "__main__":
    unittest.main() 