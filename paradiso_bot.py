#!/usr/bin/env python
"""
Paradiso Discord Bot

A Discord bot for the Paradiso movie voting system, using Algolia for data storage.

Requirements:
  - Python 3.9+
  - discord.py
  - python-dotenv
  - algoliasearch
  - requests
"""

import os
import json
import random
import logging
import time
import datetime
import re
from typing import List, Dict, Any, Optional, Union, Tuple

import discord
from discord import app_commands
from dotenv import load_dotenv
import requests
from algoliasearch.search_client import SearchClient

from keep_alive import keep_alive

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
    """Paradiso Discord bot for movie voting."""

    def __init__(
            self,
            discord_token: str,
            algolia_app_id: str,
            algolia_api_key: str,
            algolia_movies_index: str,
            algolia_votes_index: str
    ):
        """Initialize the bot with required configuration."""
        self.discord_token = discord_token
        self.algolia_app_id = algolia_app_id
        self.algolia_api_key = algolia_api_key
        self.algolia_movies_index = algolia_movies_index
        self.algolia_votes_index = algolia_votes_index

        # Track users in movie-adding flow
        self.add_movie_flows = {}

        # Initialize Discord client
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # Initialize Algolia client
        self.algolia_client = SearchClient.create(algolia_app_id, algolia_api_key)
        self.movies_index = self.algolia_client.init_index(algolia_movies_index)
        self.votes_index = self.algolia_client.init_index(algolia_votes_index)

        # Set up event handlers
        self._setup_event_handlers()
        # Register slash commands
        self._register_commands()

    def _setup_event_handlers(self):
        """Set up Discord event handlers."""

        @self.client.event
        async def on_ready():
            """Handle bot ready event."""
            logger.info(f'{self.client.user} has connected to Discord!')

            # Log server information for debugging
            for guild in self.client.guilds:
                logger.info(f"Connected to guild: {guild.name} (id: {guild.id})")

                # Find and send a message to the #paradiso channel if it exists
                paradiso_channel = discord.utils.get(guild.text_channels, name="paradiso")
                if paradiso_channel:
                    await paradiso_channel.send(
                        "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands.")
                    logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")

            # Sync commands
            try:
                await self.tree.sync()
                logger.info("Commands synced successfully")
            except Exception as e:
                logger.error(f"Error syncing commands: {e}")

        @self.client.event
        async def on_message(message):
            """Handle incoming messages."""
            # Don't respond to our own messages
            if message.author == self.client.user:
                return

            # Log message for debugging
            logger.info(f"Message received from {message.author}: {message.content}")

            # Check if this is part of an active add movie flow
            if message.author.id in self.add_movie_flows:
                await self._handle_add_movie_flow(message)
                return

            # Handle manual commands with text parsing for DMs and mentions
            if isinstance(message.channel, discord.DMChannel) or self.client.user.mentioned_in(message):
                content = message.content.lower()

                # Remove mention from the message if it exists
                if self.client.user.mentioned_in(message):
                    content = re.sub(f'<@!?{self.client.user.id}>', '', content).strip()

                # Handle different command formats
                if content.startswith('/help') or content == 'help':
                    await self._send_help_message(message.channel)

                elif content.startswith('/search') or content.startswith('search'):
                    query = content.split(' ', 1)[1] if ' ' in content else ''
                    if query:
                        await self._handle_search_command(message.channel, query)
                    else:
                        await message.channel.send("Please provide a search term. Example: `search The Matrix`")

                elif content.startswith('/add') or content.startswith('add'):
                    query = content.split(' ', 1)[1] if ' ' in content else ''
                    if query:
                        # Start the add movie flow
                        await self._start_add_movie_flow(message, query)
                    else:
                        await message.channel.send("Please provide a movie title. Example: `add The Matrix`")

                elif content.startswith('/vote') or content.startswith('vote'):
                    query = content.split(' ', 1)[1] if ' ' in content else ''
                    if query:
                        await self._handle_vote_command(message.channel, message.author, query)
                    else:
                        await message.channel.send(
                            "Please provide a movie title to vote for. Example: `vote The Matrix`")

                elif content.startswith('/movies') or content == 'movies':
                    await self._handle_movies_command(message.channel)

                elif content.startswith('/top') or content.startswith('top'):
                    try:
                        count = int(content.split(' ', 1)[1]) if ' ' in content else 5
                    except ValueError:
                        count = 5
                    await self._handle_top_command(message.channel, count)

                # Default response for other messages
                elif isinstance(message.channel, discord.DMChannel) or self.client.user.mentioned_in(message):
                    await self._send_help_message(message.channel)

    def _register_commands(self):
        """Register Discord slash commands."""
        self.tree.command(name="add", description="Add a movie to the voting queue")(self.cmd_add)
        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="related", description="Find related movies based on search terms")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)

    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            self.client.run(self.discord_token)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token. Please check your DISCORD_TOKEN environment variable.")
        except Exception as e:
            logger.error(f"Error running the bot: {e}")

    # New helper methods for message-based commands
    async def _send_help_message(self, channel):
        """Send help information to the channel."""
        help_embed = discord.Embed(
            title="👋 Hello from Paradiso Bot!",
            description="I'm here to help you manage movie voting for your movie nights!",
            color=0x03a9f4
        )

        help_embed.add_field(
            name="Getting Started",
            value="You can use slash commands in the server or simple commands in DMs.",
            inline=False
        )

        help_embed.add_field(
            name="Available Commands",
            value="• `/add [movie title]` - Add a movie to vote on\n"
                  "• `/vote [movie title]` - Vote for a movie\n"
                  "• `/movies` - See all movies in the queue\n"
                  "• `/search [query]` - Search for movies\n"
                  "• `/related [query]` - Find related movies\n"
                  "• `/top [count]` - Show top voted movies",
            inline=False
        )

        help_embed.set_footer(text="Happy voting! 🎬")

        await channel.send(embed=help_embed)

    async def _handle_search_command(self, channel, query):
        """Handle a text-based search command."""
        try:
            # Search in Algolia
            search_results = self.movies_index.search(query, {
                "hitsPerPage": 5,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "poster", "votes", "plot"
                ]
            })

            if search_results["nbHits"] == 0:
                await channel.send(f"No movies found matching '{query}'.")
                return

            # Create an embed for search results
            embed = discord.Embed(
                title=f"🔍 Search Results for '{query}'",
                description=f"Found {search_results['nbHits']} results:",
                color=0x03a9f4
            )

            for i, movie in enumerate(search_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                movie_details = []
                if movie.get("director"):
                    movie_details.append(f"**Director**: {movie['director']}")
                if movie.get("actors") and len(movie["actors"]) > 0:
                    movie_details.append(f"**Starring**: {', '.join(movie['actors'][:2])}")
                movie_details.append(f"**Votes**: {votes}")

                embed.add_field(
                    name=f"{i + 1}. {title}{year}",
                    value="\n".join(movie_details),
                    inline=False
                )

            # Add instructions
            embed.set_footer(text="Use /vote [title] to vote for a movie")

            # Add thumbnail from first result if available
            if search_results["hits"][0].get("poster"):
                embed.set_thumbnail(url=search_results["hits"][0]["poster"])

            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in search command: {e}")
            await channel.send(f"An error occurred during search: {str(e)}")

    async def _start_add_movie_flow(self, message, title):
        """Start the interactive flow to add a movie."""
        user_id = message.author.id

        # Create a flow state for this user
        self.add_movie_flows[user_id] = {
            'title': title,
            'year': None,
            'director': None,
            'actors': [],
            'genre': [],
            'stage': 'year',
            'channel': message.channel
        }

        # First, check if the movie exists
        try:
            # Check if movie already exists in Algolia
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 1
            })

            if search_result["nbHits"] > 0:
                hit = search_result["hits"][0]
                if title.lower() in hit["title"].lower():
                    await message.channel.send(
                        f"❌ A movie with a similar title '{hit['title']}' is already in the voting queue!")
                    del self.add_movie_flows[user_id]
                    return
        except Exception as e:
            logger.error(f"Error checking existing movie: {e}")

        # Ask for the year
        await message.channel.send(
            f"📽️ Let's add '{title}' to the voting queue!\n\nWhat year was it released? (Type 'unknown' if you're not sure)")

    async def _handle_add_movie_flow(self, message):
        """Handle responses in the add movie flow."""
        user_id = message.author.id
        flow = self.add_movie_flows[user_id]
        response = message.content.strip()

        if response.lower() == 'cancel':
            await message.channel.send("Movie addition cancelled.")
            del self.add_movie_flows[user_id]
            return

        if flow['stage'] == 'year':
            if response.lower() == 'unknown':
                flow['year'] = None
            else:
                try:
                    flow['year'] = int(response)
                except ValueError:
                    await message.channel.send("Please enter a valid year (e.g., 2023) or 'unknown'.")
                    return

            flow['stage'] = 'director'
            await message.channel.send("Who directed this movie? (Type 'unknown' if you're not sure)")

        elif flow['stage'] == 'director':
            flow['director'] = None if response.lower() == 'unknown' else response
            flow['stage'] = 'actors'
            await message.channel.send("Who are the main actors? (Separate names with commas, or type 'unknown')")

        elif flow['stage'] == 'actors':
            if response.lower() == 'unknown':
                flow['actors'] = []
            else:
                flow['actors'] = [actor.strip() for actor in response.split(',')]

            flow['stage'] = 'genre'
            await message.channel.send("What genre(s) is this movie? (Separate genres with commas, or type 'unknown')")

        elif flow['stage'] == 'genre':
            if response.lower() == 'unknown':
                flow['genre'] = []
            else:
                flow['genre'] = [genre.strip() for genre in response.split(',')]

            flow['stage'] = 'confirm'

            # Show the movie details and ask for confirmation
            confirm_embed = discord.Embed(
                title=f"Confirm Movie Details: {flow['title']}",
                description="Please confirm these details are correct:",
                color=0x03a9f4
            )

            confirm_embed.add_field(name="Year", value=flow['year'] or "Unknown", inline=True)
            confirm_embed.add_field(name="Director", value=flow['director'] or "Unknown", inline=True)
            confirm_embed.add_field(name="Actors", value=", ".join(flow['actors']) or "Unknown", inline=False)
            confirm_embed.add_field(name="Genre", value=", ".join(flow['genre']) or "Unknown", inline=False)

            confirm_embed.set_footer(text="Type 'yes' to confirm, 'no' to cancel")

            await message.channel.send(embed=confirm_embed)

        elif flow['stage'] == 'confirm':
            if response.lower() in ['yes', 'y']:
                # Add the movie
                try:
                    movie_data = {
                        "id": f"movie_{int(time.time())}",
                        "title": flow['title'],
                        "original_title": flow['title'],
                        "year": flow['year'],
                        "director": flow['director'] or "Unknown",
                        "actors": flow['actors'],
                        "genre": flow['genre'],
                        "plot": f"Added by {message.author.display_name}.",
                        "poster": None,
                        "imdb_rating": None,
                        "imdb_id": None,
                        "tmdb_id": None,
                        "source": "manual"
                    }

                    movie_obj = await self.add_movie_to_algolia(movie_data, str(message.author.id))

                    # Create embed for movie
                    embed = discord.Embed(
                        title=f"🎬 Added: {movie_obj['title']} ({movie_obj['year'] if movie_obj['year'] else 'N/A'})",
                        description=movie_obj["plot"],
                        color=0x00ff00
                    )

                    if movie_obj["director"]:
                        embed.add_field(name="Director", value=movie_obj["director"], inline=True)

                    if movie_obj["actors"]:
                        embed.add_field(name="Starring", value=", ".join(movie_obj["actors"][:3]), inline=True)

                    if movie_obj["genre"]:
                        embed.add_field(name="Genre", value=", ".join(movie_obj["genre"]), inline=True)

                    embed.set_footer(text=f"Added by {message.author.display_name}")

                    await message.channel.send(embed=embed)

                except Exception as e:
                    logger.error(f"Error adding movie in flow: {e}")
                    await message.channel.send(f"❌ An error occurred while adding the movie: {str(e)}")
            else:
                await message.channel.send("Movie addition cancelled.")

            # Clean up the flow
            del self.add_movie_flows[user_id]

    async def _handle_vote_command(self, channel, author, title):
        """Handle a text-based vote command."""
        try:
            # Find the movie in Algolia
            movie = await self.find_movie_by_title(title)

            if not movie:
                await channel.send(
                    f"❌ Could not find '{title}' in the voting queue. Use /movies to see available movies.")
                return

            # Record the vote
            success, result = await self.vote_for_movie(movie["objectID"], str(author.id))

            if not success:
                if isinstance(result, str):
                    logger.error(f"Vote error: {result}")
                    await channel.send(f"❌ An error occurred while voting.")
                else:
                    await channel.send(f"❌ You have already voted for '{movie['title']}'!")
                return

            # Use the immediately updated movie from the vote function
            updated_movie = result

            # Create embed for vote confirmation
            embed = discord.Embed(
                title=f"✅ Vote recorded for: {updated_movie['title']}",
                description=f"This movie now has {updated_movie['votes']} vote(s)!",
                color=0x00ff00
            )

            if updated_movie.get("poster"):
                embed.set_thumbnail(url=updated_movie["poster"])

            embed.set_footer(text=f"Voted by {author.display_name}")

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in vote command: {e}")
            await channel.send(f"❌ An error occurred: {str(e)}")

    async def _handle_movies_command(self, channel):
        """Handle a text-based movies command."""
        try:
            movies = await self.get_all_movies()

            if not movies:
                await channel.send("No movies have been added yet! Use `/add` to add one.")
                return

            # Sort movies by vote count
            movies.sort(key=lambda m: m.get("votes", 0), reverse=True)

            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting",
                description=f"Here are the movies currently in the queue ({len(movies)} total):",
                color=0x03a9f4,
                timestamp=datetime.datetime.now()
            )

            # Add each movie to the embed
            for i, movie in enumerate(movies[:10]):  # Limit to top 10
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                embed.add_field(
                    name=f"{medal} {title}{year} - {votes} votes",
                    value=movie.get("plot", "No description available.")[:100] + "..."
                    if movie.get("plot") and len(movie.get("plot")) > 100
                    else movie.get("plot", "No description available."),
                    inline=False
                )

            if len(movies) > 10:
                embed.set_footer(text=f"Showing top 10 out of {len(movies)} movies. Use /search to find more.")

            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in movies command: {e}")
            await channel.send("An error occurred while getting the movies. Please try again.")

    async def _handle_top_command(self, channel, count=5):
        """Handle a text-based top command."""
        try:
            # Limit count to reasonable values
            count = max(1, min(10, count))

            # Get top voted movies
            top_movies = await self.get_top_movies(count)

            if not top_movies:
                await channel.send("❌ No movies have been voted for yet!")
                return

            # Create embed for top movies
            embed = discord.Embed(
                title=f"🏆 Top {len(top_movies)} Voted Movies",
                description="Here are the most popular movies for our next movie night!",
                color=0x00ff00
            )

            for i, movie in enumerate(top_movies):
                # Get medal emoji for top 3
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                # Create field for each movie
                movie_details = [
                    f"**Votes**: {movie['votes']}",
                    f"**Year**: {movie['year'] if movie.get('year') else 'N/A'}",
                ]

                if movie.get("imdbRating"):
                    movie_details.append(f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10")

                if movie.get("director"):
                    movie_details.append(f"**Director**: {movie['director']}")

                embed.add_field(
                    name=f"{medal} {movie['title']}",
                    value="\n".join(movie_details),
                    inline=False
                )

            # Add instructions on how to vote
            embed.set_footer(text="Use /vote to vote for a movie!")

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in top command: {e}")
            await channel.send(f"❌ An error occurred: {str(e)}")

    # Command handlers
    async def cmd_add(self, interaction: discord.Interaction, title: str):
        """Add a movie to the voting queue."""
        await interaction.response.defer(thinking=True)

        try:
            # Start the add movie flow in DMs
            user = interaction.user
            dm_channel = await user.create_dm()

            # Initialize the flow
            user_id = user.id

            # Check if the movie exists
            try:
                # Check if movie already exists in Algolia
                search_result = self.movies_index.search(title, {
                    "hitsPerPage": 1
                })

                if search_result["nbHits"] > 0:
                    hit = search_result["hits"][0]
                    if title.lower() in hit["title"].lower():
                        await interaction.followup.send(
                            f"❌ A movie with a similar title '{hit['title']}' is already in the voting queue!")
                        return
            except Exception as e:
                logger.error(f"Error checking existing movie: {e}")

            # Create a flow state for this user
            self.add_movie_flows[user_id] = {
                'title': title,
                'year': None,
                'director': None,
                'actors': [],
                'genre': [],
                'stage': 'year',
                'channel': dm_channel,
                'interaction': interaction
            }

            # Send instructions to the user's DM
            await dm_channel.send(
                f"📽️ Let's add '{title}' to the voting queue!\n\nWhat year was it released? (Type 'unknown' if you're not sure)")

            # Notify in the original channel
            await interaction.followup.send(
                f"📬 I've sent you a DM to collect details about '{title}'! Please check your DMs to complete adding the movie.")

        except Exception as e:
            logger.error(f"Error in add command: {e}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        """Vote for a movie in the queue."""
        await interaction.response.defer(thinking=True)

        try:
            # Find the movie in Algolia
            movie = await self.find_movie_by_title(title)

            if not movie:
                await interaction.followup.send(
                    f"❌ Could not find '{title}' in the voting queue. Use /movies to see available movies.")
                return

            # Record the vote
            success, result = await self.vote_for_movie(movie["objectID"], str(interaction.user.id))

            if not success:
                if isinstance(result, str):
                    logger.error(f"Vote error: {result}")
                    await interaction.followup.send(f"❌ An error occurred while voting.")
                else:
                    await interaction.followup.send(f"❌ You have already voted for '{movie['title']}'!")
                return

            # Use the immediately updated movie from the vote function
            updated_movie = result

            # Create embed for vote confirmation
            embed = discord.Embed(
                title=f"✅ Vote recorded for: {updated_movie['title']}",
                description=f"This movie now has {updated_movie['votes']} vote(s)!",
                color=0x00ff00
            )

            if updated_movie.get("poster"):
                embed.set_thumbnail(url=updated_movie["poster"])

            embed.set_footer(text=f"Voted by {interaction.user.display_name}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in vote command: {e}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def cmd_movies(self, interaction: discord.Interaction):
        """List all movies in the voting queue."""
        await interaction.response.defer()

        try:
            movies = await self.get_all_movies()

            if not movies:
                await interaction.followup.send("No movies have been added yet! Use `/add` to add one.")
                return

            # Sort movies by vote count
            movies.sort(key=lambda m: m.get("votes", 0), reverse=True)

            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting",
                description=f"Here are the movies currently in the queue ({len(movies)} total):",
                color=0x03a9f4,
                timestamp=datetime.datetime.now()
            )

            # Add each movie to the embed
            for i, movie in enumerate(movies[:10]):  # Limit to top 10
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                embed.add_field(
                    name=f"{medal} {title}{year} - {votes} votes",
                    value=movie.get("plot", "No description available.")[:100] + "..."
                    if movie.get("plot") and len(movie.get("plot")) > 100
                    else movie.get("plot", "No description available."),
                    inline=False
                )

            if len(movies) > 10:
                embed.set_footer(text=f"Showing top 10 out of {len(movies)} movies. Use /search to find more.")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /movies command: {e}")
            await interaction.followup.send("An error occurred while getting the movies. Please try again.")

    async def cmd_search(self, interaction: discord.Interaction, query: str):
        """Search for movies in the database."""
        await interaction.response.defer()

        try:
            # Search in Algolia
            search_results = self.movies_index.search(query, {
                "hitsPerPage": 5,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "poster", "votes", "plot"
                ]
            })

            if search_results["nbHits"] == 0:
                await interaction.followup.send(f"No movies found matching '{query}'.")
                return

            # Create an embed for search results
            embed = discord.Embed(
                title=f"🔍 Search Results for '{query}'",
                description=f"Found {search_results['nbHits']} results:",
                color=0x03a9f4
            )

            for i, movie in enumerate(search_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                movie_details = []
                if movie.get("director"):
                    movie_details.append(f"**Director**: {movie['director']}")
                if movie.get("actors") and len(movie["actors"]) > 0:
                    movie_details.append(f"**Starring**: {', '.join(movie['actors'][:2])}")
                movie_details.append(f"**Votes**: {votes}")

                embed.add_field(
                    name=f"{i + 1}. {title}{year}",
                    value="\n".join(movie_details),
                    inline=False
                )

            # Add instructions
            embed.set_footer(text="Use /vote [title] to vote for a movie")

            # Add thumbnail from first result if available
            if search_results["hits"][0].get("poster"):
                embed.set_thumbnail(url=search_results["hits"][0]["poster"])

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /search command: {e}")
            await interaction.followup.send(f"An error occurred during search: {str(e)}")


def main():
    """Run the bot."""
    # Load environment variables
    load_dotenv()
    discord_token = os.getenv('DISCORD_TOKEN')
    algolia_app_id = os.getenv('ALGOLIA_APP_ID')
    algolia_api_key = os.getenv('ALGOLIA_API_KEY')
    algolia_movies_index = os.getenv('ALGOLIA_MOVIES_INDEX')
    algolia_votes_index = os.getenv('ALGOLIA_VOTES_INDEX')

    # Check if all environment variables are set
    if not all([discord_token, algolia_app_id, algolia_api_key,
                algolia_movies_index, algolia_votes_index]):
        logger.error("Missing required environment variables. Please check your .env file.")
        exit(1)

    # Log configuration details (with secrets partially masked)
    logger.info(
        f"Starting with token: {discord_token[:5]}...{discord_token[-5:] if len(discord_token) > 10 else '****'}")
    logger.info(f"Using Algolia app ID: {algolia_app_id}")
    logger.info(f"Using Algolia indices: {algolia_movies_index}, {algolia_votes_index}")

    # Start keep-alive web server
    keep_alive()

    # Create and run the bot
    bot = ParadisoBot(
        discord_token=discord_token,
        algolia_app_id=algolia_app_id,
        algolia_api_key=algolia_api_key,
        algolia_movies_index=algolia_movies_index,
        algolia_votes_index=algolia_votes_index
    )
    bot.run()


if __name__ == "__main__":
    main()