"""
UI Modal components for Paradiso Discord Bot
Includes modals for adding movies.
"""

import logging
import time
import discord
from discord.ui import Modal, TextInput
from typing import Dict, Any, Optional

from utils.algolia_utils import add_movie_to_algolia, _check_movie_exists, generate_user_token
from utils.embed_formatters import format_movie_embed

logger = logging.getLogger("paradiso_bot")

class MovieAddModal(Modal, title="Add Movie to Paradiso"):
    """
    Modal for entering movie details when adding a new movie.
    Used with the /add command.
    """

    title_input = TextInput(
        label="Movie Title",
        placeholder="e.g., The Matrix",
        required=True,
        max_length=100
    )

    year_input = TextInput(
        label="Release Year",
        placeholder="e.g., 1999",
        required=False,
        max_length=4
    )

    director_input = TextInput(
        label="Director",
        placeholder="e.g., Lana & Lilly Wachowski",
        required=False,
        max_length=100
    )

    actors_input = TextInput(
        label="Actors (comma-separated)",
        placeholder="e.g., Keanu Reeves, Laurence Fishburne, Carrie-Anne Moss",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph
    )

    genre_input = TextInput(
        label="Genres (comma-separated)",
        placeholder="e.g., Action, Sci-Fi, Thriller",
        required=False,
        max_length=100
    )

    def __init__(self, bot_instance, movie_title: str = ""):
        """Initialize with bot instance and optional pre-filled title."""
        super().__init__(timeout=300)  # 5 minute timeout
        self.bot = bot_instance

        # Pre-fill title if provided
        if movie_title:
            self.title_input.default = movie_title

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        await interaction.response.defer(thinking=True)

        try:
            # Extract and process values from the form
            title = self.title_input.value.strip()

            # Parse year
            year = self.year_input.value.strip() if self.year_input.value else None
            if year:
                try:
                    year = int(year)
                    if not (1850 <= year <= 2030):  # Reasonable range
                        await interaction.followup.send(
                            "⚠️ The release year must be a valid year between 1850 and 2030.",
                            ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.followup.send(
                        "⚠️ The release year must be a valid 4-digit number.",
                        ephemeral=True
                    )
                    return

            # Process other inputs
            director = self.director_input.value.strip() if self.director_input.value else None
            actors = [a.strip() for a in self.actors_input.value.split(',') if a.strip()] if self.actors_input.value else []
            genres = [g.strip() for g in self.genre_input.value.split(',') if g.strip()] if self.genre_input.value else []

            # Check if the movie already exists
            existing_movie = await _check_movie_exists(self.bot.algolia_client, self.bot.algolia_movies_index_name, title)

            if existing_movie:
                await interaction.followup.send(
                    f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'",
                    ephemeral=True
                )
                return

            # Prepare movie data
            movie_data = {
                'objectID': f"modal_{int(time.time())}",
                'title': title,
                'originalTitle': title,
                'year': year,
                'director': director or "Unknown",
                'actors': actors,
                'genre': genres,
                'plot': f"Added manually by {interaction.user.display_name}.",
                'image': None,
                'rating': None,
                'imdbID': None,
                'tmdbID': None,
                'source': 'modal',
                'votes': 0,
                'addedDate': int(time.time()),
                'addedBy': generate_user_token(str(interaction.user.id)),
                'voted': False
            }

            # Add to Algolia
            await add_movie_to_algolia(self.bot.algolia_client, self.bot.algolia_movies_index_name, movie_data)

            # Create response embed
            embed = format_movie_embed(movie_data, title_prefix=f"🎬 Added: ")
            embed.set_footer(text=f"Added by {interaction.user.display_name}")
            
            # Send confirmation
            await interaction.followup.send(
                "✅ Movie added to the voting queue!",
                embed=embed
            )
            
            logger.info(f"Added movie via modal: {title} ({movie_data['objectID']})")
        
        except Exception as e:
            logger.error(f"Error adding movie via modal: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ An error occurred while adding the movie: {str(e)}",
                ephemeral=True
            )
    
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handle errors during modal submission."""
        logger.error(f"Error in movie add modal: {error}", exc_info=True)
        
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ An error occurred while processing your submission. Please try again.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ An error occurred while processing your submission. Please try again.",
                ephemeral=True
            )