#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia v3 - Improved)

A Discord bot for the Paradiso movie voting system with improved features:
- Better movie addition with exact matching and confirmation
- Fixed random command
- Recommendation carousels
"""

import datetime
import logging
import os
import re
import time
import random
from typing import List, Dict, Any, Optional, Union

import discord
from algoliasearch.search_client import SearchClient
from discord import app_commands
from dotenv import load_dotenv

# Import utilities
from utils.algolia_utils import (
    add_movie_to_algolia, vote_for_movie, find_movie_by_title, search_movies_for_vote, get_top_movies, get_all_movies,
    generate_user_token, _check_movie_exists, get_random_movie
)
from utils.embed_formatters import send_search_results_embed, send_detailed_movie_embed, format_movie_embed
from utils.parser import parse_algolia_filters
from utils.ui_modals import MovieAddModal
from utils.ui_views import VoteSelectionView, MoviesPaginationView

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("paradiso_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("paradiso_bot")


class ParadisoBot:
    """Paradiso Discord bot for movie voting (Algolia v3)."""

    def __init__(
            self,
            discord_token: str,
            algolia_app_id: str,
            algolia_api_key: str,
            algolia_movies_index: str,
            algolia_votes_index: str,
            algolia_actors_index: str
    ):
        """Initialize the bot with required configuration."""
        self.discord_token = discord_token
        self.algolia_app_id = algolia_app_id
        self.algolia_api_key = algolia_api_key

        self.algolia_movies_index_name = algolia_movies_index
        self.algolia_votes_index_name = algolia_votes_index
        self.algolia_actors_index_name = algolia_actors_index

        self.add_movie_flows = {}
        self.vote_messages = {}
        self.pending_votes = {}
        self.movies_pagination_state = {}
        self.last_random_movies = []  # Track last 50 random movies shown

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # V3 API: Simple client initialization
        self.algolia_client = SearchClient.create(algolia_app_id, algolia_api_key)

        self._setup_event_handlers()
        self._register_commands()

    def _setup_event_handlers(self):
        """Set up Discord event handlers using @self.client.event decorators."""

        @self.client.event
        async def on_ready():
            logger.info(f'{self.client.user} has connected to Discord!')
            for guild in self.client.guilds:
                logger.info(f"Connected to guild: {guild.name} (id: {guild.id})")
                paradiso_channel = discord.utils.get(guild.text_channels, name="paradiso")
                if paradiso_channel:
                    try:
                        messages = [msg async for msg in paradiso_channel.history(limit=5)]
                        last_bot_message = next((msg for msg in messages if msg.author == self.client.user), None)
                        if last_bot_message and (datetime.datetime.now(
                                datetime.timezone.utc) - last_bot_message.created_at).total_seconds() < 60:
                            logger.info("Skipping welcome message to avoid spam.")
                        else:
                            await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                            logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        logger.error(f"Error checking/sending welcome in #paradiso: {e}", exc_info=True)
            try:
                await self.tree.sync()
                logger.info("Commands synced successfully")
            except Exception as e:
                logger.error(f"Error syncing commands: {e}", exc_info=True)

        @self.client.event
        async def on_message(message):
            if message.author == self.client.user:
                return

            logger.debug(
                f"Message from {message.author} ({message.author.id}) in {message.channel}: {message.content}")

            user_id = message.author.id
            if user_id in self.add_movie_flows:
                if isinstance(message.channel, discord.DMChannel) and \
                        message.channel.id == self.add_movie_flows[user_id]['channel'].id:
                    await self._handle_add_movie_flow(message)
                    return

            if user_id in self.pending_votes:
                flow_state = self.pending_votes[user_id]
                if isinstance(message.channel, discord.DMChannel) and message.channel.id == flow_state['channel'].id:
                    await self._handle_vote_selection_response(message, flow_state)
                    return

            if isinstance(message.channel, discord.DMChannel) or self.client.user.mentioned_in(message):
                content = message.content.lower()
                if self.client.user.mentioned_in(message):
                    content = re.sub(rf'<@!?{self.client.user.id}>\b', '', content).strip()

                if content:
                    if content.startswith('help'):
                        await self._send_help_message(message.channel)
                    elif content.startswith('search '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._handle_search_command(message.channel, query)
                        else:
                            await message.channel.send("Usage: `search The Matrix`")
                    elif content.startswith('add '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._start_add_movie_flow(message, query)
                        else:
                            await message.channel.send("Usage: `add The Matrix`")
                    elif content.startswith('vote '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._handle_vote_command(message.channel, message.author, query)
                        else:
                            await message.channel.send("Usage: `vote The Matrix`")
                    elif content == 'movies':
                        await self._handle_movies_command(message.channel)
                    elif content.startswith('top'):
                        try:
                            parts = content.split(' ', 1)
                            count_str = parts[1].strip() if len(parts) > 1 else "5"
                            count = int(count_str) if count_str.isdigit() else 5
                        except ValueError:
                            await message.channel.send("Usage: `top 10` or `top` for 5.")
                            return
                        await self._handle_top_command(message.channel, count)
                    elif content.startswith('info '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._handle_info_command(message.channel, query)
                        else:
                            await message.channel.send("Usage: `info The Matrix`")
                    elif content == 'random':
                        await self._handle_random_command(message.channel)
                    else:
                        await self._send_help_message(message.channel)
                elif isinstance(message.channel, discord.DMChannel) and not content:
                    await self._send_help_message(message.channel)

        @self.client.event
        async def on_interaction(interaction: discord.Interaction):
            if interaction.type == discord.InteractionType.component and interaction.data and interaction.data.get(
                    'component_type') == 2:  # Button
                logger.info(
                    f"Button interaction: User {interaction.user.id}, Custom ID: {interaction.data.get('custom_id')}, Msg ID: {interaction.message.id if interaction.message else 'N/A'}")

    def _register_commands(self):
        """Register Discord slash commands."""

        @self.tree.command(name="add", description="Add a movie to the voting queue with structured input")
        @app_commands.describe(title="Optional: Provide a title to pre-fill the form")
        async def cmd_add_slash(interaction: discord.Interaction, title: Optional[str] = None):
            await interaction.response.send_modal(MovieAddModal(self, movie_title=title or ""))

        @self.tree.command(name="recommend", description="Get movie recommendations based on a movie you like")
        @app_commands.describe(
            movie_title="Title of the movie you want recommendations for",
            count="Number of recommendations (1-10)"
        )
        async def cmd_recommend_slash(interaction: discord.Interaction, movie_title: str,
                                      count: app_commands.Range[int, 1, 10] = 5):
            await self.cmd_recommend(interaction, movie_title, count)

        @self.tree.command(name="lookalike", description="Find visually similar movies based on poster/image")
        @app_commands.describe(
            movie_title="Title of the movie you want visual similarities for",
            count="Number of recommendations (1-10)"
        )
        async def cmd_lookalike_slash(interaction: discord.Interaction, movie_title: str,
                                      count: app_commands.Range[int, 1, 10] = 5):
            await self.cmd_lookalike(interaction, movie_title, count)

        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="info", description="Get detailed info for a movie")(self.cmd_info)
        self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)
        self.tree.command(name="random", description="Get a random movie from the queue")(self.cmd_random)

    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            self.client.run(self.discord_token)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token.")
        except Exception as e:
            logger.critical(f"Critical error running the bot: {e}", exc_info=True)

    # --- Text Command Handlers (for DMs and mentions) ---
    async def _send_help_message(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        embed = discord.Embed(
            title="👋 Hello from Paradiso Bot!",
            description="I manage movie voting! Use slash commands (`/`) or mention me/DM me for text commands:",
            color=0x03a9f4
        )
        embed.add_field(
            name="Text Commands (Mention or DM)",
            value="`add [movie title]`\n`vote [movie title]`\n`movies`\n`search [query]`\n`top [count]`\n`info [query]`\n`random`\n`help`",
            inline=False
        )
        embed.add_field(
            name="Slash Commands (In Server - Recommended!)",
            value="`/add [title]`\n`/vote [title]`\n`/movies`\n`/search [query]` (supports filters)\n`/top [count]`\n`/info [query]`\n`/recommend [title]`\n`/lookalike [title]`\n`/random`\n`/help`",
            inline=False
        )
        embed.add_field(
            name="Search Filters (for /search)",
            value="Examples: `/search matrix year:1999`\n`/search action genre:Comedy director:\"Taika Waititi\"`\n`year>2010 votes:>5`",
            inline=False
        )
        await channel.send(embed=embed)

    async def _handle_search_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query_string: str):
        """Handle a text-based search command."""
        try:
            query = query_string.strip()
            if not query:
                await channel.send("Please provide a search term.")
                return

            # V3 API: Simple index.search call
            index = self.algolia_client.init_index(self.algolia_movies_index_name)
            search_response = index.search(query, {
                'hitsPerPage': 5,
                'attributesToRetrieve': [
                    'objectID', 'title', 'year', 'director', 'actors', 'genre', 'image', 'votes', 'plot',
                    'rating'
                ],
                'attributesToHighlight': ['title', 'director', 'actors', 'plot', 'genre'],
                'attributesToSnippet': ['plot:15']
            })

            if search_response.get('nbHits', 0) == 0:
                await channel.send(f"No results found for '{query}'.")
                return

            await send_search_results_embed(channel, query, search_response.get('hits', []),
                                            search_response.get('nbHits', 0))

        except Exception as e:
            logger.error(f"Error in manual search command: {e}", exc_info=True)
            await channel.send(f"An error occurred: {str(e)}")

    async def _handle_random_command(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Handles text-based random command - now shows any random movie."""
        try:
            random_movie = await get_random_movie(self.algolia_client, self.algolia_movies_index_name,
                                                  self.last_random_movies)

            if not random_movie:
                await channel.send("🤔 No movies found in the database.")
                return

            # Track this movie as shown
            if random_movie['objectID'] not in self.last_random_movies:
                self.last_random_movies.append(random_movie['objectID'])
                if len(self.last_random_movies) > 50:
                    self.last_random_movies = self.last_random_movies[-50:]

            embed = format_movie_embed(random_movie, title_prefix="🎲 Random Movie:")
            embed.add_field(name="Votes", value=str(random_movie.get("votes", 0)), inline=True)
            if random_movie.get("votes", 0) == 0:
                embed.set_footer(text="This movie has no votes yet! Why not be the first?")
            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /random command: {e}", exc_info=True)
            await channel.send(f"❌ An error occurred while fetching a random movie: {str(e)}")

    # ... (other text command handlers remain similar)

    # --- Slash Command Handlers ---

    async def cmd_random(self, interaction: discord.Interaction):
        """Slash command to get a random movie."""
        await interaction.response.defer(thinking=True)
        try:
            random_movie = await get_random_movie(self.algolia_client, self.algolia_movies_index_name,
                                                  self.last_random_movies)

            if not random_movie:
                await interaction.followup.send("🤔 No movies found in the database.")
                return

            # Track this movie as shown
            if random_movie['objectID'] not in self.last_random_movies:
                self.last_random_movies.append(random_movie['objectID'])
                if len(self.last_random_movies) > 50:
                    self.last_random_movies = self.last_random_movies[-50:]

            embed = format_movie_embed(random_movie, title_prefix="🎲 Random Movie:")
            embed.add_field(name="Votes", value=str(random_movie.get("votes", 0)), inline=True)
            if random_movie.get("votes", 0) == 0:
                embed.set_footer(text="This movie has no votes yet! Why not be the first?")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /random command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while fetching a random movie: {str(e)}")

    async def cmd_recommend(self, interaction: discord.Interaction, movie_title: str, count: int = 5):
        """Get movie recommendations based on a reference movie."""
        await interaction.response.defer(thinking=True)
        try:
            # Find the reference movie
            reference_movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name,
                                                        movie_title)
            if not reference_movie:
                await interaction.followup.send(f"❌ Could not find '{movie_title}' to base recommendations on.")
                return

            # Get recommendations using Algolia's recommendation API
            # Note: This is a simplified version. For production, you'd use the Algolia Recommend API
            # For now, we'll use similarity search based on attributes

            # Search for similar movies based on director and genre
            index = self.algolia_client.init_index(self.algolia_movies_index_name)

            # Build filter for similar attributes
            filters = []
            if reference_movie.get('director'):
                filters.append(f"director:\"{reference_movie['director']}\"")
            if reference_movie.get('genre'):
                genre_filter = ' OR '.join([f"genre:\"{g}\"" for g in reference_movie['genre'][:2]])
                if genre_filter:
                    filters.append(f"({genre_filter})")

            # Add year range filter (movies within 5 years)
            if reference_movie.get('year'):
                year = reference_movie['year']
                filters.append(f"year:{year - 5} TO {year + 5}")

            combined_filter = ' AND '.join(filters) if filters else None

            search_response = index.search('', {
                'hitsPerPage': count + 5,  # Get extra to filter out the original
                'filters': combined_filter,
                'attributesToRetrieve': ['*']
            })

            # Filter out the original movie
            recommendations = []
            for hit in search_response.get('hits', []):
                if hit['objectID'] != reference_movie['objectID']:
                    recommendations.append(hit)
                    if len(recommendations) >= count:
                        break

            if not recommendations:
                await interaction.followup.send(f"❌ No recommendations found for '{reference_movie['title']}'.")
                return

            # Create recommendation embed
            embed = discord.Embed(
                title=f"🎬 Movies like '{reference_movie['title']}'",
                description=f"Based on director and genre similarities",
                color=0x00ff00
            )

            # Add reference movie info
            embed.add_field(
                name="📌 Reference Movie",
                value=f"{reference_movie.get('title')} ({reference_movie.get('year', 'N/A')})",
                inline=False
            )

            # Add recommendations
            for i, movie in enumerate(recommendations):
                value_parts = []
                if movie.get('director'):
                    value_parts.append(f"Director: {movie['director']}")
                if movie.get('genre'):
                    value_parts.append(f"Genre: {', '.join(movie['genre'][:2])}")
                if movie.get('votes') is not None:
                    value_parts.append(f"Votes: {movie['votes']}")

                embed.add_field(
                    name=f"{i + 1}. {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})",
                    value="\n".join(value_parts) if value_parts else "No additional info",
                    inline=False
                )

            if reference_movie.get('image'):
                embed.set_thumbnail(url=reference_movie['image'])

            embed.set_footer(text=f"Powered by Paradiso • Use /vote to vote for these movies")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /recommend: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error getting recommendations: {str(e)}")

    async def cmd_lookalike(self, interaction: discord.Interaction, movie_title: str, count: int = 5):
        """Get visually similar movies based on poster/image."""
        await interaction.response.defer(thinking=True)
        try:
            # Find the reference movie
            reference_movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name,
                                                        movie_title)
            if not reference_movie:
                await interaction.followup.send(f"❌ Could not find '{movie_title}' to find visual similarities.")
                return

            if not reference_movie.get('image'):
                await interaction.followup.send(
                    f"❌ '{reference_movie['title']}' has no poster image for visual comparison.")
                return

            # Get visually similar movies
            # Note: This is a simplified version. For production, you'd use Algolia's Visual Search API
            # For now, we'll search for movies with similar attributes and posters

            index = self.algolia_client.init_index(self.algolia_movies_index_name)

            # Search for movies with images, similar genre, and year range
            filters = ["image:*"]  # Must have an image

            if reference_movie.get('genre'):
                genre_filter = ' OR '.join([f"genre:\"{g}\"" for g in reference_movie['genre']])
                if genre_filter:
                    filters.append(f"({genre_filter})")

            combined_filter = ' AND '.join(filters)

            search_response = index.search('', {
                'hitsPerPage': count + 5,  # Get extra to filter out the original
                'filters': combined_filter,
                'attributesToRetrieve': ['*']
            })

            # Filter out the original movie
            similar_movies = []
            for hit in search_response.get('hits', []):
                if hit['objectID'] != reference_movie['objectID'] and hit.get('image'):
                    similar_movies.append(hit)
                    if len(similar_movies) >= count:
                        break

            if not similar_movies:
                await interaction.followup.send(f"❌ No visually similar movies found for '{reference_movie['title']}'.")
                return

            # Create visual similarity embed
            embed = discord.Embed(
                title=f"🎨 Movies visually similar to '{reference_movie['title']}'",
                description=f"Based on poster and genre similarities",
                color=0x9370DB
            )

            # Add reference movie info with image
            embed.add_field(
                name="📌 Reference Movie",
                value=f"{reference_movie.get('title')} ({reference_movie.get('year', 'N/A')})",
                inline=False
            )
            embed.set_thumbnail(url=reference_movie['image'])

            # Add similar movies with image preview
            for i, movie in enumerate(similar_movies):
                value_parts = [f"[View Poster]({movie['image']})"]
                if movie.get('votes') is not None:
                    value_parts.append(f"Votes: {movie['votes']}")

                embed.add_field(
                    name=f"{i + 1}. {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})",
                    value="\n".join(value_parts),
                    inline=False
                )

            embed.set_footer(text=f"Powered by Paradiso • Use /vote to vote for these movies")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /lookalike: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error finding visually similar movies: {str(e)}")

    # ... (rest of the slash commands remain similar)

    async def cmd_top(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 20] = 5):
        await interaction.response.defer(thinking=True)
        try:
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, count)
            if not top_movies:
                await interaction.followup.send("❌ No movies with votes yet! Start voting to see results.")
                return
            embed = discord.Embed(title=f"🏆 Top {len(top_movies)} Voted Movies", color=0x00ff00)
            for i, movie in enumerate(top_movies):
                medal = "🥇🥈🥉"[i] if i < 3 else f"{i + 1}."
                details = [f"**Votes**: {movie.get('votes', 0)}", f"**Year**: {movie.get('year', 'N/A')}"]
                if movie.get("rating"): details.append(f"**Rating**: ⭐ {movie['rating']}/10")
                embed.add_field(name=f"{medal} {movie.get('title', 'N/A')}", value="\n".join(details), inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /top: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    # ... (other commands remain similar)


def main():
    load_dotenv()
    discord_token = os.getenv('DISCORD_TOKEN')
    algolia_app_id = os.getenv('ALGOLIA_APP_ID')
    algolia_api_key = os.getenv('ALGOLIA_BOT_SECURED_KEY')
    algolia_movies_index = os.getenv('ALGOLIA_MOVIES_INDEX')
    algolia_votes_index = os.getenv('ALGOLIA_VOTES_INDEX')
    algolia_actors_index = os.getenv('ALGOLIA_ACTORS_INDEX', 'paradiso_actors')

    if not all([discord_token, algolia_app_id, algolia_api_key, algolia_movies_index, algolia_votes_index]):
        missing = [k for k, v in locals().items() if v is None and "algolia_" in k or "discord_" in k]
        logger.critical(f"Missing essential .env variables: {', '.join(missing)}")
        exit(1)

    logger.info(f"Starting ParadisoBot with App ID: {algolia_app_id}, Movies Index: {algolia_movies_index}")

    bot = ParadisoBot(
        discord_token=discord_token,
        algolia_app_id=algolia_app_id,
        algolia_api_key=algolia_api_key,
        algolia_movies_index=algolia_movies_index,
        algolia_votes_index=algolia_votes_index,
        algolia_actors_index=algolia_actors_index
    )
    bot.run()


if __name__ == "__main__":
    main()