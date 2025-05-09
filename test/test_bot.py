import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

# Import the bot module we'll refactor
import paradiso_bot


class TestParadisoBot(unittest.TestCase):
    """Test case for Paradiso bot main functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.bot = paradiso_bot.ParadisoBot(
            discord_token="fake_token",
            algolia_app_id="fake_app_id",
            algolia_api_key="fake_api_key",
            algolia_movies_index="fake_movies_index",
            algolia_votes_index="fake_votes_index"
        )
        
        # Mock the algolia client
        self.bot.algolia_client = MagicMock()
        self.bot.movies_index = MagicMock()
        self.bot.votes_index = MagicMock()
        
        # Mock the bot's Discord client
        self.bot.client = MagicMock()
        self.bot.tree = MagicMock()
    
    def test_bot_initialization(self):
        """Test that the bot initializes correctly."""
        self.assertEqual(self.bot.discord_token, "fake_token")
        self.assertEqual(self.bot.algolia_app_id, "fake_app_id")
        self.assertEqual(self.bot.algolia_api_key, "fake_api_key")
        self.assertEqual(self.bot.algolia_movies_index, "fake_movies_index")
        self.assertEqual(self.bot.algolia_votes_index, "fake_votes_index")


if __name__ == "__main__":
    unittest.main() 