import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

import paradiso_bot
from paradiso_bot import ParadisoBot


class TestBotCommands(unittest.TestCase):
    """Test case for Paradiso bot commands."""
    
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
        
        # Mock Discord client and interactions
        self.bot.client = MagicMock()
        self.bot.tree = MagicMock()
        
        # Create a mock interaction for testing
        self.interaction = AsyncMock()
        self.interaction.response = AsyncMock()
        self.interaction.response.defer = AsyncMock()
        self.interaction.followup = AsyncMock()
        self.interaction.followup.send = AsyncMock()
        self.interaction.user = MagicMock()
        self.interaction.user.id = "123456789"
        self.interaction.user.display_name = "TestUser"
    
    @patch("paradiso_bot.ParadisoBot.search_movie")
    async def test_add_command(self, mock_search_movie):
        """Test the add command."""
        # Setup mock response from search_movie
        mock_movie_data = {
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
        mock_search_movie.return_value = mock_movie_data
        
        # Mock Algolia search to simulate movie not already in index
        self.bot.movies_index.search.return_value = {"nbHits": 0}
        
        # Mock Algolia save_object
        self.bot.movies_index.save_object.return_value = True
        
        # Test the command
        await self.bot.cmd_add(self.interaction, "Test Movie")
        
        # Assertions
        self.interaction.response.defer.assert_called_once()
        mock_search_movie.assert_called_once_with("Test Movie")
        self.bot.movies_index.search.assert_called_once()
        self.bot.movies_index.save_object.assert_called_once()
        self.interaction.followup.send.assert_called_once()

    @patch("paradiso_bot.ParadisoBot.get_movie_by_id")
    async def test_vote_command(self, mock_get_movie):
        """Test the vote command."""
        # Mock finding movie
        mock_movie = {
            "objectID": "tt1234567",
            "title": "Test Movie",
            "votes": 0
        }
        self.bot.find_movie_by_title = AsyncMock(return_value=mock_movie)
        
        # Mock votes index search (user hasn't voted yet)
        self.bot.votes_index.search.return_value = {"nbHits": 0}
        
        # Mock saving vote and updating movie
        self.bot.votes_index.save_object.return_value = True
        self.bot.movies_index.partial_update_object.return_value = True
        
        # Mock getting updated movie
        updated_movie = mock_movie.copy()
        updated_movie["votes"] = 1
        mock_get_movie.return_value = updated_movie
        
        # Test the command
        await self.bot.cmd_vote(self.interaction, "Test Movie")
        
        # Assertions
        self.interaction.response.defer.assert_called_once()
        self.bot.find_movie_by_title.assert_called_once_with("Test Movie")
        self.bot.votes_index.search.assert_called_once()
        self.bot.votes_index.save_object.assert_called_once()
        self.bot.movies_index.partial_update_object.assert_called_once()
        mock_get_movie.assert_called_once_with("tt1234567")
        self.interaction.followup.send.assert_called_once()
    
    async def test_search_command(self):
        """Test the search command."""
        # Mock Algolia search results
        search_results = {
            "hits": [
                {
                    "objectID": "movie1",
                    "title": "Test Movie 1",
                    "year": 2022,
                    "director": "Director 1",
                    "actors": ["Actor 1", "Actor 2"],
                    "poster": "https://example.com/poster1.jpg"
                },
                {
                    "objectID": "movie2",
                    "title": "Test Movie 2",
                    "year": 2021,
                    "director": "Director 2",
                    "actors": ["Actor 3", "Actor 4"],
                    "poster": "https://example.com/poster2.jpg"
                }
            ],
            "nbHits": 2
        }
        self.bot.movies_index.search = AsyncMock(return_value=search_results)
        
        # Test the command
        await self.bot.cmd_search(self.interaction, "Test Movie")
        
        # Assertions
        self.interaction.response.defer.assert_called_once()
        self.bot.movies_index.search.assert_called_once()
        self.interaction.followup.send.assert_called_once()
    
    async def test_related_command(self):
        """Test the related command."""
        # Mock Algolia search for initial movie
        top_movie = {
            "objectID": "movie1",
            "title": "Test Movie 1",
            "year": 2022,
            "director": "Director 1",
            "actors": ["Actor 1", "Actor 2"],
            "genre": ["Action", "Drama"],
            "poster": "https://example.com/poster1.jpg"
        }
        initial_search = {"hits": [top_movie], "nbHits": 1}
        
        # Mock related movies search
        related_movies = {
            "hits": [
                {
                    "objectID": "movie2",
                    "title": "Related Movie 1",
                    "year": 2020,
                    "director": "Director 2",
                    "actors": ["Actor 1", "Actor 5"],
                    "genre": ["Action", "Thriller"],
                    "poster": "https://example.com/poster2.jpg"
                },
                {
                    "objectID": "movie3",
                    "title": "Related Movie 2",
                    "year": 2021,
                    "director": "Director 3",
                    "actors": ["Actor 6", "Actor 2"],
                    "genre": ["Drama", "Comedy"],
                    "poster": "https://example.com/poster3.jpg"
                }
            ],
            "nbHits": 2
        }
        
        # Mock the search method to return different results for different calls
        async def mock_search(query, params=None):
            if query == "Test Movie":
                return initial_search
            else:
                return related_movies
        
        self.bot.movies_index.search = AsyncMock(side_effect=mock_search)
        
        # Test the command
        await self.bot.cmd_related(self.interaction, "Test Movie")
        
        # Assertions
        self.interaction.response.defer.assert_called_once()
        self.assertTrue(self.bot.movies_index.search.called)
        self.interaction.followup.send.assert_called_once()


if __name__ == "__main__":
    unittest.main() 