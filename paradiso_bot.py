#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia Pure)

A Discord bot for the Paradiso movie voting system, using Algolia for all data storage,
search, recommendations (via attribute search), and vote handling.

Requirements:
  - Python 3.9+
  - discord.py>=2.0 (for Modals)
  - python-dotenv
  - algoliasearch<4.0.0 (as requested)
  - aiohttp>=3.8.0 (for discord.py)
  - hashlib (standard library)
"""

import os
import json
import random
import logging
import time
import datetime
import re
import hashlib # Added for user token hashing
from typing import List, Dict, Any, Optional, Union, Tuple

import discord
from discord import app_commands
from discord.ui import Modal, TextInput # Added for Modals
from dotenv import load_dotenv
# Note: algoliasearch < 4.0.0 client might have slightly different API
# compared to the latest algoliasearch-client. This code is written assuming
# the older client specified in the prompt.
from algoliasearch.search_client import SearchClient


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


# --- Discord Modal for Adding Movies ---
class MovieAddModal(Modal, title="Add Movie Details"):
    """Modal for structured movie input via slash command."""
    def __init__(self, bot_instance, interaction: discord.Interaction, movie_title: str):
        super().__init__()
        self.bot_instance = bot_instance
        self.interaction = interaction # Store interaction for followup
        self.movie_title = movie_title # Store the title from the slash command

        # Pre-fill title from command argument, make required
        self.title_input = TextInput(
            label="Movie Title",
            placeholder="e.g., The Matrix",
            default=movie_title,
            required=True,
            max_length=200
        )
        self.add_item(self.title_input)

        # Year (required, number input placeholder)
        self.year_input = TextInput(
            label="Release Year",
            placeholder="e.g., 1999",
            required=True,
            max_length=4,
            min_length=4
        )
        self.add_item(self.year_input)

        # Director (optional)
        self.director_input = TextInput(
            label="Director",
            placeholder="e.g., Lana Wachowski, Lilly Wachowski",
            required=False,
            max_length=200
        )
        self.add_item(self.director_input)

        # Actors (bonus, multi-line)
        self.actors_input = TextInput(
            label="Main Actors (comma-separated)",
            placeholder="e.g., Keanu Reeves, Laurence Fishburne, Carrie-Anne Moss",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.actors_input)

        # Genre (bonus, multi-line)
        self.genre_input = TextInput(
            label="Genres (comma-separated)",
            placeholder="e.g., Sci-Fi, Action",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.genre_input)


    async def on_submit(self, interaction: discord.Interaction):
        """Handles the modal submission."""
        await interaction.response.defer(thinking=True, ephemeral=False) # Defer the response

        title = self.title_input.value.strip()
        year_str = self.year_input.value.strip()
        director = self.director_input.value.strip() or "Unknown"
        actors_str = self.actors_input.value.strip()
        genre_str = self.genre_input.value.strip()

        # Basic validation for year
        try:
            year = int(year_str)
            if not 1850 <= year <= datetime.datetime.now().year + 5: # Basic range check
                 await interaction.followup.send("❌ Invalid year provided. Please enter a valid 4-digit year.")
                 return
        except ValueError:
            await interaction.followup.send("❌ Invalid year format. Please enter a 4-digit number.")
            return

        # Process actors and genre strings
        actors = [actor.strip() for actor in actors_str.split(',') if actor.strip()] if actors_str else []
        genre = [g.strip() for g in genre_str.split(',') if g.strip()] if genre_str else []

        # Construct movie data dictionary
        movie_data = {
            "id": f"manual_{int(time.time())}", # Unique ID for manual entries
            "title": title,
            "original_title": title, # Assume original title is the same unless manually specified
            "year": year,
            "director": director,
            "actors": actors,
            "genre": genre,
            "plot": f"Added manually by {interaction.user.display_name}.", # Minimal plot for manual entries
            "poster": None, # No poster for manual entries
            "imdb_rating": None,
            "imdb_id": None,
            "tmdb_id": None,
            "source": "manual"
        }

        try:
            # Check if movie already exists (basic title check)
            existing_movie = await self.bot_instance.find_movie_by_title(title)
            if existing_movie and existing_movie.get("title", "").lower() == title.lower():
                 await interaction.followup.send(
                    f"❌ A movie with that title ('{existing_movie['title']}') is already in the voting queue.")
                 return

            # Add movie to Algolia
            movie_obj = await self.bot_instance.add_movie_to_algolia(movie_data, str(interaction.user.id))

            # Create embed for confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_obj['title']} ({movie_obj['year'] if movie_obj['year'] else 'N/A'})",
                description=movie_obj.get("plot", "No plot available."),
                color=0x00ff00
            )

            if movie_obj.get("director"):
                embed.add_field(name="Director", value=movie_obj["director"], inline=True)

            if movie_obj.get("actors"):
                embed.add_field(name="Starring", value=", ".join(movie_obj["actors"][:5]), inline=False) # Show more actors in embed

            if movie_obj.get("genre"):
                embed.add_field(name="Genre", value=", ".join(movie_obj["genre"]), inline=True)

            if movie_obj.get("poster"):
                 embed.set_thumbnail(url=movie_obj["poster"])

            embed.set_footer(text=f"Added by {interaction.user.display_name}")

            await interaction.followup.send("✅ Movie added to the voting queue!", embed=embed)

        except Exception as e:
            logger.error(f"Error adding movie via modal: {e}")
            await interaction.followup.send(f"❌ An error occurred while adding the movie: {str(e)}")


class ParadisoBot:
    """Paradiso Discord bot for movie voting (Algolia Pure)."""

    def __init__(
            self,
            discord_token: str,
            algolia_app_id: str,
            algolia_api_key: str, # This should be your SECURED bot key
            algolia_movies_index: str,
            algolia_votes_index: str,
            algolia_actors_index: str # Still keeping this name as per prompt, but not used in this pure Algolia model
    ):
        """Initialize the bot with required configuration."""
        self.discord_token = discord_token
        self.algolia_app_id = algolia_app_id
        self.algolia_api_key = algolia_api_key # Use the secured key

        # Algolia Index names
        self.algolia_movies_index = algolia_movies_index
        self.algolia_votes_index = algolia_votes_index
        self.algolia_actors_index = algolia_actors_index # Not currently used for movie data

        # Track users in movie-adding flow (for text-based DM flow)
        self.add_movie_flows = {} # Dict to store user_id: flow_state

        # Initialize Discord client
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True # Needed for user display names etc.
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # Initialize Algolia client
        # Ensure you are using the correct secured API key here with appropriate ACLs
        # (addObject, deleteObject, getObject, search, browse, partialUpdateObject)
        self.algolia_client = SearchClient.create(algolia_app_id, algolia_api_key)
        self.movies_index = self.algolia_client.init_index(algolia_movies_index)
        self.votes_index = self.algolia_client.init_index(algolia_votes_index)
        # self.actors_index = self.algolia_client.init_index(algolia_actors_index) # Not currently used

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
                    # Check if the last message in the channel was from the bot recently
                    # to avoid spamming on restarts.
                    try:
                        last_message = await paradiso_channel.fetch_message(paradiso_channel.last_message_id)
                        if last_message.author == self.client.user and \
                           (datetime.datetime.utcnow() - last_message.created_at.replace(tzinfo=datetime.timezone.utc)).total_seconds() < 60:
                             logger.info("Skipping welcome message to avoid spam.")
                        else:
                             await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                             logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except discord.errors.NotFound:
                         # Channel exists but has no messages or last message was deleted
                         await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                         logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        # Catch other potential errors during message fetch
                        logger.error(f"Error checking last message in #paradiso: {e}")
                        await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                        logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")


            # Sync commands
            try:
                # Sync globally for simplicity, or specify guild IDs
                await self.tree.sync()
                logger.info("Commands synced successfully")
            except Exception as e:
                logger.error(f"Error syncing commands: {e}")


        @self.client.event
        async def on_message(message):
            """Handle incoming messages for text commands or add movie flow."""
            # Don't respond to our own messages or slash commands
            if message.author == self.client.user or message.is_command():
                return

            # Log message for debugging
            logger.info(f"Message received from {message.author} in {message.channel}: {message.content}")

            # Check if this is part of an active add movie flow (text-based DM flow)
            if message.author.id in self.add_movie_flows:
                await self._handle_add_movie_flow(message)
                return

            # Handle manual commands with text parsing for DMs and mentions
            # Only process if in DM or if the bot is mentioned in a server channel
            if isinstance(message.channel, discord.DMChannel) or self.client.user.mentioned_in(message):
                content = message.content.lower()

                # Remove mention from the message if it exists
                if self.client.user.mentioned_in(message):
                    content = re.sub(f'<@!?{self.client.user.id}>', '', content).strip()

                # Process command if it starts with a known command word or is just the command word
                if content.startswith('help'):
                    await self._send_help_message(message.channel)

                elif content.startswith('search '):
                    query = content.split(' ', 1)[1].strip()
                    if query:
                        await self._handle_search_command(message.channel, query)
                    else:
                        await message.channel.send("Please provide a search term. Example: `search The Matrix`")

                elif content.startswith('add '):
                    query = content.split(' ', 1)[1].strip()
                    if query:
                        # Start the text-based add movie flow
                        await self._start_add_movie_flow(message, query)
                    else:
                        await message.channel.send("Please provide a movie title. Example: `add The Matrix`")

                elif content.startswith('vote '):
                    query = content.split(' ', 1)[1].strip()
                    if query:
                        await self._handle_vote_command(message.channel, message.author, query)
                    else:
                        await message.channel.send(
                            "Please provide a movie title to vote for. Example: `vote The Matrix`")

                elif content == 'movies':
                    await self._handle_movies_command(message.channel)

                elif content.startswith('top'):
                    try:
                        count = int(content.split(' ', 1)[1].strip()) if ' ' in content else 5
                    except ValueError:
                        await message.channel.send("Invalid number for top count. Please use a number, e.g., `top 10`.")
                        return
                    count = max(1, min(10, count)) # Limit count to 1-10 for text commands
                    await self._handle_top_command(message.channel, count)

                # Default response for unhandled messages in DMs or mentions
                elif isinstance(message.channel, discord.DMChannel) or (self.client.user.mentioned_in(message) and not content):
                     await self._send_help_message(message.channel)


    def _register_commands(self):
        """Register Discord slash commands."""
        # Use the Modal for the /add command
        @self.tree.command(name="add", description="Add a movie to the voting queue with structured input")
        async def cmd_add_slash(interaction: discord.Interaction, title: str):
            """Slash command to add a movie, prompting with a modal."""
            # Show the modal to the user
            await interaction.response.send_modal(MovieAddModal(self, interaction, title))

        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        # Implement related search using Algolia's attribute search and filters
        self.tree.command(name="related", description="Find related movies based on a movie in the database")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)


    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            # Assumes keep_alive() runs a web server in a separate thread/process
            # to prevent the bot from sleeping on platforms like Repl.it.
            # If not needed, this line can be removed.
            # keep_alive()
            self.client.run(self.discord_token)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token. Please check your DISCORD_TOKEN environment variable.")
        except Exception as e:
            logger.error(f"Error running the bot: {e}")


    # --- Manual Command Handlers (for DMs and mentions) ---

    async def _send_help_message(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Send help information to the channel."""
        help_embed = discord.Embed(
            title="👋 Hello from Paradiso Bot!",
            description="I'm here to help you manage movie voting for your movie nights!\n\n"
                        "Use slash commands (`/`) in servers for a better experience!\n"
                        "Or mention me or DM me to use text commands:",
            color=0x03a9f4
        )

        help_embed.add_field(
            name="Text Commands (Mention or DM)",
            value="`add [movie title]` - Start adding a movie (I'll DM you for details)\n"
                  "`vote [movie title]` - Vote for a movie\n"
                  "`movies` - See all movies in the queue\n"
                  "`search [query]` - Search for movies\n"
                  "`related [query]` - Find related movies\n"
                  "`top [count]` - Show top voted movies\n"
                  "`help` - Show this help message",
            inline=False
        )

        help_embed.add_field(
            name="Slash Commands (In Server)",
            value="`/add [title]` - Add a movie using a pop-up form (Recommended!)\n"
                  "`/vote [title]` - Vote for a movie\n"
                  "`/movies` - See all movies\n"
                  "`/search [query]` - Search movies\n"
                  "`/related [query]` - Find related movies\n"
                  "`/top [count]` - Show top voted movies\n"
                  "`/help` - Show this help message",
            inline=False
        )


        help_embed.set_footer(text="Happy voting! 🎬")

        await channel.send(embed=help_embed)

    async def _handle_search_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query: str):
        """Handle a text-based search command."""
        try:
            # Search in Algolia leveraging searchableAttributes
            search_results = self.movies_index.search(query, {
                "hitsPerPage": 5,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "poster", "votes", "plot", "imdbRating"
                ],
                 "attributesToHighlight": [ # Add highlighting
                     "title", "originalTitle", "director", "actors", "year", "plot"
                ],
                "attributesToSnippet": [ # Add snippets for plot
                    "plot:15" # Snippet 15 words around the match
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
                # Use highlighted versions if available, fall back to raw data
                title_display = movie.get("_highlightResult", {}).get("title", {}).get("value", movie.get("title", "Unknown"))
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                movie_details = []
                if movie.get("director"):
                    director_display = movie.get("_highlightResult", {}).get("director", {}).get("value", movie["director"])
                    movie_details.append(f"**Director**: {director_display}")
                if movie.get("actors") and len(movie["actors"]) > 0:
                    actors_display = movie.get("_highlightResult", {}).get("actors", {}).get("value", ", ".join(movie["actors"][:3]))
                    movie_details.append(f"**Starring**: {actors_display}")
                if movie.get("imdbRating") is not None: # Check explicitly for None
                     movie_details.append(f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10")
                movie_details.append(f"**Votes**: {votes}")

                # Use snippet or truncate plot
                plot_display = movie.get("_snippetResult", {}).get("plot", {}).get("value", movie.get("plot", "No description available."))
                # Basic truncation if snippet isn't helpful or too long
                if len(plot_display) > 150:
                     plot_display = plot_display[:150] + "..."


                embed.add_field(
                    name=f"{i + 1}. {title_display}{year}",
                    value="\n".join(movie_details) + f"\n**Plot**: {plot_display}",
                    inline=False
                )

            # Add instructions
            embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server")

            # Add thumbnail from first result if available
            if search_results["hits"][0].get("poster"):
                embed.set_thumbnail(url=search_results["hits"][0]["poster"])

            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual search command: {e}")
            await channel.send(f"An error occurred during search: {str(e)}")


    async def _start_add_movie_flow(self, message: discord.Message, title: str):
        """Start the interactive text-based flow to add a movie in DMs."""
        user_id = message.author.id

        # Check if a flow is already active for this user
        if user_id in self.add_movie_flows:
            await message.channel.send("You are already in the process of adding a movie. Please complete or type 'cancel'.")
            # Also inform the user in the original channel if it wasn't a DM
            if not isinstance(message.channel, discord.DMChannel):
                dm_channel = await message.author.create_dm()
                await message.channel.send(f"📬 You are already in the process of adding a movie. Please check your DMs ({dm_channel.mention}).")
            return

        # Start the manual flow
        dm_channel = await message.author.create_dm()
        self.add_movie_flows[user_id] = {
            'title': title,
            'year': None,
            'director': None,
            'actors': [],
            'genre': [],
            'stage': 'year',
            'channel': dm_channel,
            'original_channel': message.channel # Store original channel to send confirmation message
        }

        await dm_channel.send(
            f"📽️ Let's add '{title}' to the voting queue!\n\nWhat year was it released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
        if not isinstance(message.channel, discord.DMChannel):
             await message.channel.send(f"📬 Please check your DMs ({dm_channel.mention}) to provide details for '{title}'.")


    async def _handle_add_movie_flow(self, message: discord.Message):
        """Handle responses in the text-based add movie flow (in DMs)."""
        user_id = message.author.id
        flow = self.add_movie_flows.get(user_id)

        # Ensure the message is in the correct DM channel for the flow
        # And ensure a flow actually exists (it might have been cancelled/finished)
        if not flow or message.channel.id != flow['channel'].id:
             logger.warning(f"Received message from user {user_id} in incorrect channel or no active flow.")
             return # Ignore messages not in the flow's DM channel

        response = message.content.strip()

        if response.lower() == 'cancel':
            await message.channel.send("Movie addition cancelled.")
            if 'original_channel' in flow and flow['original_channel'] and not isinstance(flow['original_channel'], discord.DMChannel):
                 try:
                    await flow['original_channel'].send(f"Movie addition for '{flow.get('title', 'a movie')}' was cancelled.")
                 except Exception as e:
                     logger.warning(f"Could not send cancel message to original channel: {e}")

            del self.add_movie_flows[user_id]
            return

        if flow['stage'] == 'year':
            if response.lower() == 'unknown':
                flow['year'] = None
            else:
                try:
                    year = int(response)
                    if not 1850 <= year <= datetime.datetime.now().year + 5:
                        await message.channel.send("Please enter a valid 4-digit year (e.g., 2023) or 'unknown'.")
                        return # Stay in this stage
                    flow['year'] = year
                except ValueError:
                    await message.channel.send("Please enter a valid year (e.g., 2023) or 'unknown'.")
                    return # Stay in this stage

            flow['stage'] = 'director'
            await message.channel.send("Who directed this movie? (Type 'unknown' if you're not sure, or 'cancel' to stop)")

        elif flow['stage'] == 'director':
            flow['director'] = None if response.lower() == 'unknown' else response.strip()
            flow['stage'] = 'actors'
            await message.channel.send("Who are the main actors? (Separate names with commas, type 'unknown' if none, or 'cancel' to stop)")

        elif flow['stage'] == 'actors':
            if response.lower() == 'unknown':
                flow['actors'] = []
            else:
                flow['actors'] = [actor.strip() for actor in response.split(',') if actor.strip()]

            flow['stage'] = 'genre'
            await message.channel.send("What genre(s) is this movie? (Separate genres with commas, type 'unknown' if none, or 'cancel' to stop)")

        elif flow['stage'] == 'genre':
            if response.lower() == 'unknown':
                flow['genre'] = []
            else:
                flow['genre'] = [genre.strip() for genre in response.split(',') if genre.strip()]

            flow['stage'] = 'confirm_manual' # New stage for manual confirmation

            # Show the movie details and ask for confirmation
            confirm_embed = discord.Embed(
                title=f"Confirm Movie Details: {flow.get('title', 'Unknown')}",
                description="Please confirm these details are correct:",
                color=0x03a9f4
            )

            confirm_embed.add_field(name="Year", value=flow['year'] or "Unknown", inline=True)
            confirm_embed.add_field(name="Director", value=flow['director'] or "Unknown", inline=True)
            confirm_embed.add_field(name="Actors", value=", ".join(flow['actors']) or "Unknown", inline=False)
            confirm_embed.add_field(name="Genre", value=", ".join(flow['genre']) or "Unknown", inline=False)

            confirm_embed.set_footer(text="Type 'yes' to confirm, 'no' to re-enter, or 'cancel'")

            await message.channel.send(embed=confirm_embed)

        elif flow['stage'] == 'confirm_manual':
            if response.lower() in ['yes', 'y']:
                # Construct movie data from flow state
                movie_data = {
                    "id": f"manual_{int(time.time())}", # Unique ID for manual entries
                    "title": flow.get('title', 'Unknown Movie'),
                    "original_title": flow.get('title', 'Unknown Movie'),
                    "year": flow['year'],
                    "director": flow['director'] or "Unknown",
                    "actors": flow['actors'],
                    "genre": flow['genre'],
                    "plot": f"Added manually by {message.author.display_name}.",
                    "poster": None,
                    "imdb_rating": None,
                    "imdb_id": None,
                    "tmdb_id": None,
                    "source": "manual"
                }
                await self._add_movie_from_flow(user_id, movie_data, message.author, flow.get('original_channel'))

            elif response.lower() in ['no', 'n']:
                # Restart the manual flow from the beginning
                await message.channel.send("Okay, let's re-enter the movie details.")
                # Reset relevant fields in the flow state
                flow['stage'] = 'year'
                flow['year'] = None
                flow['director'] = None
                flow['actors'] = []
                flow['genre'] = []
                # Keep the flow state updated in the dict
                self.add_movie_flows[user_id] = flow
                await message.channel.send(
                    f"📽️ What year was '{flow.get('title', 'this movie')}' released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
            else:
                 await message.channel.send("Please respond with 'yes', 'no', or 'cancel'.")
                 return # Stay in this stage

        # Clean up the flow if completed
        if user_id in self.add_movie_flows and flow['stage'] not in ['year', 'director', 'actors', 'genre', 'confirm_manual']:
             del self.add_movie_flows[user_id]


    async def _add_movie_from_flow(self, user_id: int, movie_data: Dict[str, Any], author: discord.User, original_channel: Optional[discord.TextChannel]):
        """Helper to add the movie to Algolia and send confirmation after text flow."""
        try:
            # Check if movie already exists in Algolia by title (fuzzy match)
            # Use a dedicated method for checking existence more robustly
            existing_movie = await self._check_movie_exists(movie_data['title'])

            if existing_movie:
                 await self.add_movie_flows[user_id]['channel'].send(
                    f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                 if original_channel and not isinstance(original_channel, discord.DMChannel):
                      try:
                         await original_channel.send(
                            f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                      except Exception as e:
                          logger.warning(f"Could not send exists message to original channel: {e}")
                 del self.add_movie_flows[user_id]
                 return


            movie_obj = await self.add_movie_to_algolia(movie_data, str(author.id))

            # Create embed for movie confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_obj['title']} ({movie_obj['year'] if movie_obj['year'] else 'N/A'})",
                description=movie_obj.get("plot", "No plot available."),
                color=0x00ff00
            )

            if movie_obj.get("director"):
                embed.add_field(name="Director", value=movie_obj["director"], inline=True)

            if movie_obj.get("actors"):
                embed.add_field(name="Starring", value=", ".join(movie_obj["actors"][:5]), inline=False) # Show more actors

            if movie_obj.get("genre"):
                embed.add_field(name="Genre", value=", ".join(movie_obj["genre"]), inline=True)

            if movie_obj.get("poster"):
                 embed.set_thumbnail(url=movie_obj["poster"])

            embed.set_footer(text=f"Added by {author.display_name}")

            # Send confirmation to DM channel first
            await self.add_movie_flows[user_id]['channel'].send("✅ Movie added to the voting queue!", embed=embed)

            # If original channel was different (i.e., a server channel), send confirmation there too
            if original_channel and original_channel != self.add_movie_flows[user_id]['channel']:
                 try:
                    await original_channel.send(f"✅ Movie '{movie_obj['title']}' added to the voting queue!")
                    # Optionally send the embed to the original channel as well
                    # await original_channel.send(embed=embed)
                 except Exception as e:
                     logger.warning(f"Could not send add confirmation to original channel: {e}")


        except Exception as e:
            logger.error(f"Error adding movie in text flow: {e}")
            await self.add_movie_flows[user_id]['channel'].send(f"❌ An error occurred while adding the movie: {str(e)}")
            if original_channel and not isinstance(original_channel, discord.DMChannel):
                 try:
                    await original_channel.send(f"❌ An error occurred while adding the movie '{movie_data.get('title', 'Unknown Movie')}': {str(e)}")
                 except Exception as e:
                      logger.warning(f"Could not send add error to original channel: {e}")

        finally:
            # Clean up the flow regardless of success or failure
            if user_id in self.add_movie_flows:
                 del self.add_movie_flows[user_id]


    async def _handle_vote_command(self, channel: Union[discord.TextChannel, discord.DMChannel], author: discord.User, title: str):
        """Handle a text-based vote command."""
        try:
            # Find the movie in Algolia
            movie = await self.find_movie_by_title(title)

            if not movie:
                await channel.send(
                    f"❌ Could not find '{title}' in the voting queue. Use `movies` to see available movies or `/movies` in a server.")
                return

            # Record the vote
            success, result = await self.vote_for_movie(movie["objectID"], str(author.id))

            if not success:
                if isinstance(result, str) and result == "Already voted":
                     await channel.send(f"❌ You have already voted for '{movie['title']}'!")
                else:
                    logger.error(f"Vote error for manual command: {result}")
                    await channel.send(f"❌ An error occurred while voting.")
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
            logger.error(f"Error in manual vote command: {e}")
            await channel.send(f"❌ An error occurred: {str(e)}")

    async def _handle_movies_command(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Handle a text-based movies command."""
        try:
            movies = await self.get_all_movies()

            if not movies:
                await channel.send("No movies have been added yet! Use `add [title]` to add one or `/add` in a server.")
                return

            # Algolia sorting via customRanking is preferred, but ensure sorted locally too
            movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting",
                description=f"Here are the movies currently in the queue ({len(movies)} total):",
                color=0x03a9f4,
                timestamp=datetime.datetime.now(datetime.timezone.utc) # Use timezone-aware datetime
            )

            # Add each movie to the embed
            for i, movie in enumerate(movies[:15]):  # Limit to top 15 for text commands readability
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                # Truncate plot for cleaner display in text commands
                plot = movie.get("plot", "No description available.")
                if len(plot) > 150:
                    plot = plot[:150] + "..."

                embed.add_field(
                    name=f"{medal} {title}{year} - {votes} votes",
                    value=plot,
                    inline=False
                )

            if len(movies) > 15:
                embed.set_footer(text=f"Showing top 15 out of {len(movies)} movies. Use `search [query]` or `/search` in a server to find more.")
            else:
                 embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server")


            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual movies command: {e}")
            await channel.send("An error occurred while getting the movies. Please try again.")

    async def _handle_top_command(self, channel: Union[discord.TextChannel, discord.DMChannel], count: int = 5):
        """Handle a text-based top command."""
        try:
            # Limit count to reasonable values for text commands
            count = max(1, min(10, count))

            # Get top voted movies using Algolia search sorted by votes
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
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."

                # Create field for each movie
                movie_details = [
                    f"**Votes**: {movie.get('votes', 0)}",
                    f"**Year**: {movie.get('year', 'N/A')}",
                ]

                if movie.get("imdbRating") is not None: # Check explicitly for None
                    movie_details.append(f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10")

                if movie.get("director"):
                    movie_details.append(f"**Director**: {movie['director']}")

                if movie.get("genre"):
                     movie_details.append(f"**Genre**: {', '.join(movie['genre'])}")

                embed.add_field(
                    name=f"{medal} {movie.get('title', 'Unknown')}",
                    value="\n".join(movie_details),
                    inline=False
                )

            # Add instructions on how to vote
            embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server!")

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in manual top command: {e}")
            await channel.send(f"❌ An error occurred: {str(e)}")


    # --- Slash Command Handlers ---

    # Note: cmd_add_slash is defined inside _register_commands to use the modal

    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        """Vote for a movie in the queue."""
        await interaction.response.defer(thinking=True)

        try:
            # Find the movie in Algolia
            movie = await self.find_movie_by_title(title)

            if not movie:
                await interaction.followup.send(
                    f"❌ Could not find '{title}' in the voting queue. Use `/movies` to see available movies.")
                return

            # Record the vote
            success, result = await self.vote_for_movie(movie["objectID"], str(interaction.user.id))

            if not success:
                if isinstance(result, str) and result == "Already voted":
                     await interaction.followup.send(f"❌ You have already voted for '{movie['title']}'!")
                else:
                    logger.error(f"Vote error for slash command: {result}")
                    await interaction.followup.send(f"❌ An error occurred while voting.")
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
            logger.error(f"Error in /vote command: {e}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def cmd_movies(self, interaction: discord.Interaction):
        """List all movies in the voting queue."""
        await interaction.response.defer()

        try:
            movies = await self.get_all_movies()

            if not movies:
                await interaction.followup.send("No movies have been added yet! Use `/add` to add one.")
                return

            # Algolia sorting via customRanking is preferred, but ensure sorted locally too
            movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)


            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting",
                description=f"Here are the movies currently in the queue ({len(movies)} total):",
                color=0x03a9f4,
                timestamp=datetime.datetime.now(datetime.timezone.utc) # Use timezone-aware datetime
            )

            # Add each movie to the embed
            for i, movie in enumerate(movies[:20]):  # Limit to top 20 for slash commands
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                # Truncate plot for cleaner display
                plot = movie.get("plot", "No description available.")
                if len(plot) > 150:
                    plot = plot[:150] + "..."

                embed.add_field(
                    name=f"{medal} {title}{year} - {votes} votes",
                    value=plot,
                    inline=False
                )

            if len(movies) > 20:
                embed.set_footer(text=f"Showing top 20 out of {len(movies)} movies. Use /search to find more.")
            else:
                 embed.set_footer(text="Use /vote to vote for a movie!")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /movies command: {e}")
            await interaction.followup.send("An error occurred while getting the movies. Please try again.")

    async def cmd_search(self, interaction: discord.Interaction, query: str):
        """Search for movies in the database."""
        await interaction.response.defer()

        try:
            # Search in Algolia leveraging searchableAttributes
            search_results = self.movies_index.search(query, {
                "hitsPerPage": 10, # Increase hits for slash command
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "poster", "votes", "plot", "imdbRating"
                ],
                 # Highlight matched attributes for better search results
                "attributesToHighlight": [
                     "title", "originalTitle", "director", "actors", "year", "plot"
                ],
                 "attributesToSnippet": [ # Add snippets for plot
                    "plot:20" # Snippet 20 words around the match
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
                # Use highlighted versions if available, fall back to raw data
                title_display = movie.get("_highlightResult", {}).get("title", {}).get("value", movie.get("title", "Unknown"))
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                movie_details = []
                if movie.get("director"):
                    director_display = movie.get("_highlightResult", {}).get("director", {}).get("value", movie["director"])
                    movie_details.append(f"**Director**: {director_display}")
                if movie.get("actors") and len(movie["actors"]) > 0:
                    actors_display = movie.get("_highlightResult", {}).get("actors", {}).get("value", ", ".join(movie["actors"][:5])) # Show more actors
                    movie_details.append(f"**Starring**: {actors_display}")

                if movie.get("imdbRating") is not None: # Check explicitly for None
                     movie_details.append(f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10")

                movie_details.append(f"**Votes**: {votes}")

                # Use snippet or truncate plot
                plot_display = movie.get("_snippetResult", {}).get("plot", {}).get("value", movie.get("plot", "No description available."))
                # Basic truncation if snippet isn't helpful or too long
                if len(plot_display) > 200:
                     plot_display = plot_display[:200] + "..."


                embed.add_field(
                    name=f"{i + 1}. {title_display}{year}", # Title highlighting is not straightforward in embed field names
                    value="\n".join(movie_details) + f"\n**Plot**: {plot_display}",
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


    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms (using attribute search as proxy)."""
        await interaction.response.defer()

        try:
            # First try to find the reference movie in Algolia
            reference_movie = await self.find_movie_by_title(query)

            if not reference_movie:
                await interaction.followup.send(f"Could not find a movie matching '{query}' in the database to find related titles.")
                return

            # Build a search query for related movies based on attributes
            # This is a proxy for Algolia Recommend's Related Products/Looking Similar,
            # which require specific index/model setup.
            related_query_parts = []
            if reference_movie.get("genre"):
                related_query_parts.extend(reference_movie["genre"])
            if reference_movie.get("director") and reference_movie.get("director") != "Unknown":
                related_query_parts.append(reference_movie["director"])
            if reference_movie.get("actors"):
                # Use the first few actors as keywords
                related_query_parts.extend(reference_movie["actors"][:3])

            related_query = " ".join(related_query_parts)

            if not related_query:
                 await interaction.followup.send(f"Couldn't generate a related search query based on the details of '{reference_movie['title']}'. Genre, Director, or Actors data might be missing.")
                 return

            # Search for related movies in Algolia
            related_results = self.movies_index.search(related_query, {
                "hitsPerPage": 5,
                # Exclude the original movie from related results
                "filters": f"NOT objectID:{reference_movie['objectID']}",
                 "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "poster", "votes", "plot", "imdbRating"
                ],
                 # Highlight common attributes for better display
                 "attributesToHighlight": [
                     "director", "actors", "genre"
                 ],
                # Rank based on relevance and then votes/year (assuming these are in customRanking)
                # Relying on Algolia's inherent relevance ranking + customRanking
            })

            if related_results["nbHits"] == 0:
                await interaction.followup.send(f"Couldn't find any movies clearly related to '{reference_movie['title']}' based on genre, director, or actors.")
                return

            # Create an embed for related movies
            embed = discord.Embed(
                title=f"🎬 Movies Related to '{reference_movie.get('title', 'Unknown')}'",
                description=f"Based on attributes like genre, director, and actors:",
                color=0x03a9f4
            )

            # Add the reference movie details
            ref_year = f" ({reference_movie.get('year')})" if reference_movie.get("year") else ""
            ref_genre = ", ".join(reference_movie.get("genre", [])) or "N/A"
            ref_director = reference_movie.get("director", "Unknown")

            embed.add_field(
                name=f"📌 Reference Movie: {reference_movie.get('title', 'Unknown')}{ref_year}",
                value=f"**Genre**: {ref_genre}\n"
                      f"**Director**: {ref_director}\n"
                      f"**Votes**: {reference_movie.get('votes', 0)}",
                inline=False
            )

            # Add a separator
            embed.add_field(name="Related Movies", value="─────────────", inline=False)

            # Add related movies
            for i, movie in enumerate(related_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)

                # Find common elements for display using highlighted results if possible
                relation_points = []

                # Check for common genre (use highlighted value)
                genre_highlight = movie.get("_highlightResult", {}).get("genre", [])
                common_genres_display = [h['value'] for h in genre_highlight if h.get('matchedWords')]
                if common_genres_display:
                     relation_points.append(f"**Common Genres**: {', '.join(common_genres_display)}")

                # Check for same director (use highlighted value)
                director_highlight = movie.get("_highlightResult", {}).get("director", {})
                if director_highlight.get('matchedWords'):
                     relation_points.append(f"**Same Director**: {director_highlight['value']}")


                # Check for common actors (use highlighted value)
                actors_highlight = movie.get("_highlightResult", {}).get("actors", [])
                common_actors_display = [h['value'] for h in actors_highlight if h.get('matchedWords')]
                if common_actors_display:
                    relation_points.append(f"**Common Actors**: {', '.join(common_actors_display)}")


                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {movie.get('year', 'N/A')}",
                     f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10" if movie.get("imdbRating") is not None else "",
                ]
                movie_details = [detail for detail in movie_details if detail] # Remove empty strings

                embed.add_field(
                    name=f"{i+1}. {title}{year}",
                    value="\n".join(relation_points + movie_details) or "Details not available.",
                    inline=False
                )

            # Add thumbnail from reference movie if available
            if reference_movie.get("poster"):
                embed.set_thumbnail(url=reference_movie["poster"])

            embed.set_footer(text="Related search based on movie attributes. Use /vote to vote!")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /related command: {e}")
            await interaction.followup.send(f"❌ An error occurred while finding related movies: {str(e)}")

    async def cmd_top(self, interaction: discord.Interaction, count: int = 5):
        """Show the top voted movies."""
        await interaction.response.defer(thinking=True)

        try:
            # Limit count to reasonable values for slash command
            count = max(1, min(20, count))

            # Get top voted movies using Algolia search sorted by votes
            top_movies = await self.get_top_movies(count)

            if not top_movies:
                await interaction.followup.send("❌ No movies have been voted for yet!")
                return

            # Create embed for top movies
            embed = discord.Embed(
                title=f"🏆 Top {len(top_movies)} Voted Movies",
                description="Here are the most popular movies for our next movie night!",
                color=0x00ff00
            )

            for i, movie in enumerate(top_movies):
                # Get medal emoji for top 3
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."

                # Create field for each movie
                movie_details = [
                    f"**Votes**: {movie.get('votes', 0)}",
                    f"**Year**: {movie.get('year', 'N/A')}",
                ]

                if movie.get("imdbRating") is not None: # Check explicitly for None
                    movie_details.append(f"**Rating**: ⭐ {movie.get('imdbRating', 'N/A')}/10")

                if movie.get("director"):
                    movie_details.append(f"**Director**: {movie['director']}")

                if movie.get("genre"):
                     movie_details.append(f"**Genre**: {', '.join(movie['genre'])}")

                embed.add_field(
                    name=f"{medal} {movie.get('title', 'Unknown')}",
                    value="\n".join(movie_details),
                    inline=False
                )

            # Add instructions on how to vote
            embed.set_footer(text="Use /vote to vote for a movie!")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /top command: {e}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")


    async def cmd_help(self, interaction: discord.Interaction):
        """Show help for Paradiso commands."""
        embed = discord.Embed(
            title="Paradiso Bot Help",
            description="Here are the commands you can use with the Paradiso movie voting bot:",
            color=0x03a9f4
        )

        commands = [
            {
                "name": "/add [title]",
                "description": "Add a movie to the voting queue using a pop-up form (Recommended!)"
            },
            {
                "name": "/vote [title]",
                "description": "Vote for a movie in the queue"
            },
             {
                "name": "/movies",
                "description": "List all movies in the voting queue"
            },
            {
                "name": "/search [query]",
                "description": "Search for movies by title, actor, director, year, etc."
            },
            {
                "name": "/related [query]",
                "description": "Find movies related to a movie in the database (based on genre, director, actors)"
            },
            {
                "name": "/top [count]",
                "description": "Show the top voted movies (default: top 5, max: 20)"
            },
             {
                "name": "/help",
                "description": "Show this help message"
            }
        ]

        for cmd in commands:
            embed.add_field(name=cmd["name"], value=cmd["description"], inline=False)

        embed.set_footer(text="Happy voting! 🎬")

        await interaction.response.send_message(embed=embed)


    # --- Helper Methods (Algolia Interactions) ---

    async def _check_movie_exists(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Checks if a movie with a similar title already exists in Algolia.
        Uses a more restrictive search than find_movie_by_title to avoid false positives.
        """
        try:
            # Search with the title, potentially using less fuzzy settings if needed
            # Or prioritize exact matches highly
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5,
                 # Configure advanced syntax or query rules if needed for stricter matching
                 # For now, rely on default Algolia relevance + checking top hits
                 "attributesToRetrieve": ["objectID", "title"],
                 "attributesToHighlight": ["title"]
            })

            if search_result["nbHits"] == 0:
                return None

            # Check if any of the top results is a very close match
            for hit in search_result["hits"]:
                # Check for exact title match (case-insensitive)
                if hit.get("title", "").lower() == title.lower():
                     logger.info(f"Exact match found for title '{title}' in Algolia: {hit['objectID']}")
                     return hit
                # Check for very high relevance on title (e.g., typo tolerance 0 or 1)
                # This requires checking highlight results
                title_highlight = hit.get("_highlightResult", {}).get("title", {})
                if title_highlight.get('matchLevel') in ['full', 'partial']: # Adjust matchLevel based on needed strictness
                     logger.info(f"Close match found for title '{title}' in Algolia: {hit['objectID']} (Match Level: {title_highlight.get('matchLevel')})")
                     return hit # Consider top hit with good relevance a potential match


            return None # No sufficiently close match found among top hits

        except Exception as e:
            logger.error(f"Error checking existence for title '{title}' in Algolia: {e}")
            return None


    async def add_movie_to_algolia(self, movie_data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Add a movie to Algolia movies index."""
        try:
            # Ensure data types are correct for Algolia
            movie_obj = {
                "objectID": movie_data.get("id", f"manual_{int(time.time())}"), # Use provided ID or generate for manual
                "title": movie_data.get("title", "Unknown"),
                "originalTitle": movie_data.get("original_title", movie_data.get("title", "Unknown")),
                # Algolia recommends string for year if filtering/faceting, or number for ranges.
                # Sticking to number/int based on the prompt's example structure.
                "year": int(movie_data["year"]) if movie_data.get("year") is not None else None,
                "director": movie_data.get("director", "Unknown"),
                "actors": movie_data.get("actors", []),
                "genre": movie_data.get("genre", []),
                "plot": movie_data.get("plot", "No plot available."),
                "poster": movie_data.get("poster"), # This will be None for manual adds
                # Use float for rating if available
                "imdbRating": float(movie_data["imdb_rating"]) if movie_data.get("imdb_rating") is not None and self._is_float(movie_data["imdb_rating"]) else None,
                "imdbID": movie_data.get("imdb_id"), # This will be None for manual adds
                "tmdbID": movie_data.get("tmdb_id"), # This will be None for manual adds
                "votes": 0, # Starts at 0
                "addedDate": int(time.time()),
                "addedBy": self.generate_user_token(user_id),
                "source": movie_data.get("source", "manual"), # Should always be 'manual' in this version
                 "voted": False # Attribute for faceting, initially False
            }

            # Use add_object for potentially new records. If objectID exists, it updates.
            # Using save_object is also fine and explicitly handles add/update.
            self.movies_index.save_object(movie_obj)
            logger.info(f"Added/Updated movie in Algolia: {movie_obj.get('title')} ({movie_obj.get('objectID')})")

            # You might want to wait for the indexation task to complete
            # update_result = self.movies_index.save_object(movie_obj, {'autoGenerateObjectIDIfNotExist': False}) # Assuming objectID is always provided/generated manually
            # self.movies_index.wait_task(update_result['taskID'])

            return movie_obj
        except Exception as e:
            logger.error(f"Error adding movie to Algolia: {e}")
            # Re-raise the exception so calling function can handle it
            raise

    async def vote_for_movie(self, movie_id: str, user_id: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
        """Vote for a movie in Algolia."""
        try:
            user_token = self.generate_user_token(user_id)

            # Check if user already voted for this movie using the votes index
            search_result = self.votes_index.search("", {
                "filters": f"userToken:{user_token} AND movieId:{movie_id}"
            })

            if search_result["nbHits"] > 0:
                logger.info(f"User {user_id} ({user_token[:8]}...) already voted for movie {movie_id}.")
                # Return the existing movie object if available, or minimal info
                existing_movie = await self.get_movie_by_id(movie_id)
                return False, existing_movie if existing_movie else "Already voted" # User already voted


            # Record the vote in the votes index
            vote_obj = {
                # Unique ID combining user, movie, timestamp, and random element
                "objectID": f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}",
                "userToken": user_token,
                "movieId": movie_id,
                "timestamp": int(time.time())
            }
            # Use add_object as each vote is a new unique record
            self.votes_index.add_object(vote_obj)
            logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

            # Increment the movie's vote count in the movies index
            # Use partial_update_object for atomic increment
            update_result = self.movies_index.partial_update_object({
                "objectID": movie_id,
                "votes": {
                    "_operation": "Increment",
                    "value": 1
                },
                 # Optionally set a voted flag on the movie if you need to facet by it per-user
                 # This would require personalized ranking/faceting setup, complex for a simple bot.
                 # Keeping the 'voted' attribute in the object structure but not using it here for simplicity.
            })
            logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_result['taskID']}") # Note: taskID in older client

            # Wait for the update task to complete to ensure we get the latest movie data
            # This can add latency, consider removing if immediate consistency isn't critical
            try:
                self.movies_index.wait_task(update_result['taskID'])
            except Exception as e:
                 logger.warning(f"Failed to wait for Algolia task {update_result['taskID']}: {e}. Fetching potentially stale movie data.")


            # Fetch the updated movie object to return the new vote count
            updated_movie = await self.get_movie_by_id(movie_id)
            if updated_movie:
                 logger.info(f"Successfully voted for movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
                 return True, updated_movie
            else:
                 logger.warning(f"Vote recorded for {movie_id}, but failed to fetch updated movie object.")
                 # Fallback: attempt to fetch the movie object again or return partial data
                 # Returning partial data is safer if fetch fails.
                 movie_before_vote = await self.find_movie_by_title(movie_id) # Try finding by ID or title again
                 fallback_votes = movie_before_vote.get('votes', 0) + 1 if movie_before_vote else 'Unknown'
                 fallback_title = movie_before_vote.get('title', 'Unknown Movie')
                 fallback_poster = movie_before_vote.get('poster')
                 return True, {"objectID": movie_id, "votes": fallback_votes, 'title': fallback_title, 'poster': fallback_poster}


        except Exception as e:
            logger.error(f"Error voting for movie {movie_id} by user {user_id}: {e}")
            return False, str(e)


    async def get_movie_by_id(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Get a movie by its ID from Algolia movies index."""
        try:
            # Use get_object to retrieve a specific record by objectID
            return self.movies_index.get_object(movie_id)
        except Exception as e:
            # Algolia client raises exceptions for not found or other errors
            logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}")
            return None

    async def find_movie_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a movie by title in Algolia movies index using search.
        Prioritizes strong matches but returns the top hit if no exact match.
        """
        try:
            # Use Algolia search with the configured searchableAttributes
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5, # Get a few hits to check for best match
                "attributesToRetrieve": [
                    "objectID", "title", "originalTitle", "year", "director",
                    "actors", "genre", "plot", "poster", "votes", "imdbRating",
                    "imdbID", "tmdbID" # Include IDs although they'll be null for manual entries
                ],
                 "attributesToHighlight": ["title", "originalTitle"], # Highlight titles to check match quality
                 "typoTolerance": "strict" # Use strict typo tolerance for finding a *specific* movie by title
            })

            if search_result["nbHits"] == 0:
                return None

            # Check top hits for a strong title match
            for hit in search_result["hits"]:
                 title_highlight = hit.get("_highlightResult", {}).get("title", {})
                 original_title_highlight = hit.get("_highlightResult", {}).get("originalTitle", {})

                 # If the title or original title has a 'full' match level, consider it a good match
                 if title_highlight.get('matchLevel') == 'full' or original_title_highlight.get('matchLevel') == 'full':
                      logger.info(f"Found strong title match for '{title}': {hit['title']} ({hit['objectID']})")
                      return hit

                 # Check for exact string match (case-insensitive) as a fallback
                 if hit.get("title", "").lower() == title.lower() or hit.get("originalTitle", "").lower() == title.lower():
                      logger.info(f"Found exact string match for '{title}': {hit['title']} ({hit['objectID']})")
                      return hit


            # If no strong/exact match, return the very top hit, as it's the most relevant according to Algolia
            # This is a trade-off between finding *the* movie vs. finding *a* relevant movie.
            logger.info(f"No strong/exact title match for '{title}', returning top relevant hit: {search_result['hits'][0].get('title')} ({search_result['hits'][0].get('objectID')})")
            return search_result["hits"][0]

        except Exception as e:
            logger.error(f"Error finding movie by title '{title}' in Algolia: {e}")
            return None


    async def get_top_movies(self, count: int = 5) -> List[Dict[str, Any]]:
        """Get the top voted movies from Algolia movies index."""
        try:
            # Search with an empty query and filter for votes > 0
            # Algolia's customRanking should handle sorting by votes desc
            search_result = self.movies_index.search("", {
                "filters": "votes > 0", # Only include movies with at least one vote
                "hitsPerPage": count,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "plot", "poster", "votes", "imdbRating"
                ],
                # Sorting is ideally handled by customRanking in index settings.
                # If not configured or as a fallback:
                # "sortCriteria": ["votes:desc"] # This param might behave differently based on search vs browse
            })

            # Ensure sorting locally based on fetched data in case customRanking isn't perfectly applied or fast enough
            top_movies = sorted(search_result["hits"], key=lambda m: m.get("votes", 0), reverse=True)

            return top_movies

        except Exception as e:
            logger.error(f"Error getting top {count} movies from Algolia: {e}")
            return []

    async def get_all_movies(self) -> List[Dict[str, Any]]:
        """Get all movies from Algolia movies index."""
        try:
            # Use browse_objects to retrieve all records. Handle pagination for large indices.
            # The browse method is for iterating through all objects.
            all_movies = []
            # Setting hitsPerPage higher to reduce iterations, max is 1000 per browse call
            for hit in self.movies_index.browse_objects({'hitsPerPage': 1000}):
                 all_movies.append(hit)

            logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse.")
            # Optionally sort here if needed, but relying on Algolia for query-based sorting is better.
            # For a simple list display, sorting might be done client-side or by a basic Algolia query.
            # Sorting here for consistency with the display commands.
            all_movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

            return all_movies

        except Exception as e:
            logger.error(f"Error getting all movies from Algolia: {e}")
            return []

    def generate_user_token(self, user_id: str) -> str:
        """Generate a consistent, non-reversible user token for Algolia from Discord user ID."""
        # Algolia recommends using a non-guessable user identifier for security and analytics.
        # Hashing the Discord user ID provides a consistent but non-reversible token.
        # Using sha256 for a reasonably strong hash.
        return hashlib.sha256(user_id.encode()).hexdigest()

    def _is_float(self, value: Any) -> bool:
        """Helper to check if a value can be converted to a float."""
        if value is None:
             return False
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False


def main():
    """Run the bot."""
    # Load environment variables
    load_dotenv()
    discord_token = os.getenv('DISCORD_TOKEN')
    algolia_app_id = os.getenv('ALGOLIA_APP_ID')
    # Use the SECURED BOT API key generated by the setup process
    # This key should have specific permissions limited to the necessary operations
    # on the movies and votes indices (e.g., addObject, deleteObject, getObject, search, browse, partialUpdateObject).
    # DO NOT use your Admin API Key here.
    algolia_api_key = os.getenv('ALGOLIA_BOT_SECURED_KEY') # Updated key name as recommended for production bots
    algolia_movies_index = os.getenv('ALGOLIA_MOVIES_INDEX')
    algolia_votes_index = os.getenv('ALGOLIA_VOTES_INDEX')
    algolia_actors_index = os.getenv('ALGOLIA_ACTORS_INDEX', 'paradiso_actors') # Keep name, default if missing

    # Check if essential environment variables are set
    if not all([discord_token, algolia_app_id, algolia_api_key,
                algolia_movies_index, algolia_votes_index, algolia_actors_index]):
        logger.error("Missing essential Algolia or Discord environment variables.")
        logger.error("Please ensure DISCORD_TOKEN, ALGOLIA_APP_ID, ALGOLIA_BOT_SECURED_KEY,")
        logger.error("ALGOLIA_MOVIES_INDEX, ALGOLIA_VOTES_INDEX, and ALGOLIA_ACTORS_INDEX are set in your .env file.")
        logger.error("Run the setup script if you haven't already.")
        exit(1)

    # The keep_alive function is typically for hosting environments like Repl.it
    # that require a web server to stay alive. If you are hosting differently,
    # you might not need this or may need a different approach.
    # try:
    #     keep_alive() # Make sure keep_alive.py exists and works for your hosting
    #     logger.info("keep_alive started.")
    # except Exception as e:
    #      logger.warning(f"Could not start keep_alive: {e}. If running on a platform like Repl.it, the bot may stop.")


    logger.info(f"Starting with token: {discord_token[:5]}...{discord_token[-5:]}")
    logger.info(f"Using Algolia app ID: {algolia_app_id}")
    logger.info(f"Using Algolia movies index: {algolia_movies_index}")
    logger.info(f"Using Algolia votes index: {algolia_votes_index}")
    logger.info(f"Using Algolia actors index: {algolia_actors_index} (Note: This index is not actively used in this pure Algolia version)")


    # Create and run the bot
    bot = ParadisoBot(
        discord_token=discord_token,
        algolia_app_id=algolia_app_id,
        algolia_api_key=algolia_api_key,
        algolia_movies_index=algolia_movies_index,
        algolia_votes_index=algolia_votes_index,
        algolia_actors_index=algolia_actors_index
    )

    # Listeners like on_message and on_ready are handled by the decorator @self.client.event
    # Command handlers are registered via self.tree.command(name="...").
    # Modal submission is handled by the on_submit method of the Modal class itself.
    # Reaction handling (if any were needed) would need to be manually added if not using commands.Bot.
    # Since we removed the API confirmation reaction flow, we don't need on_reaction_add.

    bot.run()


if __name__ == "__main__":
    # Add a check for environment variables before even trying to load them
    # A basic check for .env presence is also helpful.
    if not os.path.exists(".env") and not os.path.exists(".env.bot") and not os.environ.get('DISCORD_TOKEN'):
         logger.error("No .env or .env.bot file found, and DISCORD_TOKEN is not set in environment variables.")
         logger.error("Please create a .env file or set environment variables.")
         exit(1)

    main()