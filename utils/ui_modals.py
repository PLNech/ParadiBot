"""
UI Modal components for Paradiso Discord Bot
Includes modals and confirmation views for adding movies.
"""

import logging
import time
import discord
from discord.ui import Modal, TextInput, View, Button
from typing import Dict, Any, Optional, List

from utils.algolia_utils import add_movie_to_algolia, _check_movie_exists, generate_user_token
from utils.embed_formatters import format_movie_embed

logger = logging.getLogger("paradiso_bot")

class MovieAddConfirmView(View):
    """View for confirming movie addition when similar movies exist."""

    def __init__(self, bot_instance, movie_data: Dict[str, Any], existing_movies: List[Dict[str, Any]], interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.bot = bot_instance
        self.movie_data = movie_data
        self.existing_movies = existing_movies
        self.original_interaction = interaction

    @discord.ui.button(label="Add Anyway", style=discord.ButtonStyle.green)
    async def add_anyway(self, interaction: discord.Interaction, button: Button):
        """Add the movie despite similar entries."""
        try:
            # Add to Algolia
            await add_movie_to_algolia(self.bot.algolia_client, self.bot.algolia_movies_index_name, self.movie_data)

            # Create response embed
            embed = format_movie_embed(self.movie_data, title_prefix=f"🎬 Added: ")
            embed.set_footer(text=f"Added by {interaction.user.display_name}")

            # Disable all buttons
            for item in self.children:
                item.disabled = True

            await interaction.response.edit_message(
                content="✅ Movie added to the voting queue!",
                embed=embed,
                view=self
            )

            logger.info(f"Force-added movie via modal: {self.movie_data['title']} ({self.movie_data['objectID']})")

        except Exception as e:
            logger.error(f"Error force-adding movie via modal: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ An error occurred while adding the movie: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_add(self, interaction: discord.Interaction, button: Button):
        """Cancel the movie addition."""
        # Disable all buttons
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="❌ Movie addition cancelled.",
            embed=None,
            view=self
        )


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

            # Check if the movie already exists (exact title+year match)
            existing_movie = await _check_movie_exists(self.bot.algolia_client, self.bot.algolia_movies_index_name, title, year)

            if existing_movie:
                await interaction.followup.send(
                    f"❌ This exact movie (title and year) is already in the voting queue: '{existing_movie['title']}' ({existing_movie.get('year', 'N/A')})",
                    ephemeral=True
                )
                return

            # Check for similar movies (title only, fuzzy match)
            index = self.bot.algolia_client.init_index(self.bot.algolia_movies_index_name)
            search_response = index.search(title, {
                'hitsPerPage': 3,
                'attributesToRetrieve': ['objectID', 'title', 'year', 'votes'],
                'typoTolerance': 'min'
            })

            # Filter for similar movies (same title, different or no year)
            similar_movies = []
            for hit in search_response.get('hits', []):
                if hit.get('title', '').lower() == title.lower() and hit.get('year') != year:
                    similar_movies.append(hit)

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

            # If there are similar movies, show confirmation
            if similar_movies:
                embed = discord.Embed(
                    title="⚠️ Similar movies found",
                    description=f"Found movies with the same title. Do you still want to add '{title}' ({year or 'N/A'})?",
                    color=0xFFA500
                )

                for i, movie in enumerate(similar_movies):
                    embed.add_field(
                        name=f"{i+1}. {movie['title']} ({movie.get('year', 'N/A')})",
                        value=f"Votes: {movie.get('votes', 0)}",
                        inline=False
                    )

                view = MovieAddConfirmView(self.bot, movie_data, similar_movies, interaction)
                await interaction.followup.send(embed=embed, view=view)
            else:
                # No similar movies, add directly
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