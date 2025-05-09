#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia Pure v5 - Modular)

A Discord bot for the Paradiso movie voting system, using Algolia for all data storage,
search, recommendations (via attribute search), and vote handling.

Structure:
- main_bot.py: Bot setup, event handlers, command registration, flow state management.
- utils/: Helper functions and UI components.

Refinements included:
- Uses Discord Modals for /add.
- Implements interactive selection for /vote using buttons.
- Implements pagination and varied display for /movies using buttons.
- Adds /info command.
- Handles list attributes and uses schema names (image, rating).
- Adds support for parsed filter syntax in search commands.
- Fixes /add modal timeout.
- Corrects on_interaction listener registration.
- Keeps text-based DM flow for 'add' mention/DM command (search first).
- Keeps text-based DM flow for 'vote' mention/DM command (text selection).

Requirements:
  - Python 3.9+
  - discord.py>=2.0
  - python-dotenv
  - algoliasearch<4.0.0
  - hashlib (standard library)
"""

import os
import json
import random
import logging
import time
import datetime
import re
import hashlib
from typing import List, Dict, Any, Optional, Union, Tuple

import discord
from algoliasearch.recommend_client import RecommendClient
from discord import app_commands
from discord.ui import Modal # Only Modal needed here for the class reference
from dotenv import load_dotenv
from algoliasearch.search_client import SearchClient # Algolia client initialized here

# Import utilities
from utils.algolia_utils import (
    add_movie_to_algolia, vote_for_movie, get_movie_by_id,
    find_movie_by_title, search_movies_for_vote, get_top_movies, get_all_movies,
    generate_user_token, _is_float, _check_movie_exists
)
from utils.ui_modals import MovieAddModal
from utils.ui_views import VoteSelectionView, MoviesPaginationView
from utils.parser import parse_algolia_filters
from utils.embed_formatters import send_search_results_embed, send_detailed_movie_embed


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
    """Paradiso Discord bot for movie voting (Algolia Pure)."""

    def __init__(
            self,
            discord_token: str,
            algolia_app_id: str,
            algolia_api_key: str, # This should be your SECURED bot key
            algolia_movies_index: str,
            algolia_votes_index: str,
            algolia_actors_index: str # Still keeping this name as per prompt, but not used
    ):
        """Initialize the bot with required configuration."""
        self.discord_token = discord_token
        self.algolia_app_id = algolia_app_id
        self.algolia_api_key = algolia_api_key

        # Algolia Index names - stored for passing to API calls
        self.algolia_movies_index_name = algolia_movies_index
        self.algolia_votes_index_name = algolia_votes_index
        self.algolia_actors_index_name = algolia_actors_index

        # Track users in movie-adding flow (for text-based DM flow)
        self.add_movie_flows = {} # Dict to store user_id: flow_state (for DM text flow)

        # Track vote selection messages (using buttons)
        self.vote_messages = {} # Dict to store message_id: {'user_id': ..., 'choices': [...]}

        # Track movies pagination messages
        self.movies_pagination_state = {} # Dict message_id: {'user_id': ..., 'all_movies': [...], 'current_page': ..., 'movies_per_page': ..., 'detailed_count': ...}


        # Initialize Discord client
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # Initialize Algolia client (no more index objects)
        self.algolia_client = SearchClient.create(algolia_app_id, algolia_api_key)
        self.recommend_client = RecommendClient.create(algolia_app_id, algolia_api_key)

        # Set up event handlers using decorators
        self._setup_event_handlers()
        # Register slash commands
        self._register_commands()

    def _setup_event_handlers(self):
        """Set up Discord event handlers using @self.client.event decorators."""

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
                    try:
                        messages = [msg async for msg in paradiso_channel.history(limit=5)]
                        last_bot_message = next((msg for msg in messages if msg.author == self.client.user), None)

                        if last_bot_message and (datetime.datetime.utcnow() - last_bot_message.created_at.replace(tzinfo=datetime.timezone.utc)).total_seconds() < 60:
                             logger.info("Skipping welcome message to avoid spam.")
                        else:
                             await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                             logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        logger.error(f"Error checking last message/sending welcome in #paradiso: {e}", exc_info=True)
                        try:
                             await paradiso_channel.send(
                                    "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                             logger.info(f"Sent welcome message to #paradiso channel in {guild.name} (fallback)")
                        except Exception as send_e:
                             logger.error(f"Failed to send fallback welcome message: {send_e}", exc_info=True)

            # Sync commands
            try:
                # Sync globally for simplicity, or specify guild IDs for faster updates during development
                await self.tree.sync()
                logger.info("Commands synced successfully")
            except Exception as e:
                logger.error(f"Error syncing commands: {e}", exc_info=True)


        @self.client.event
        async def on_message(message):
            """Handle incoming messages for text commands or add movie flow."""
            if message.author == self.client.user or message.is_command():
                return

            logger.info(f"Message received from {message.author} ({message.author.id}) in {message.channel}: {message.content}")

            user_id = message.author.id

            # --- Handle Add Movie Flow (Text-based DM flow) ---
            if user_id in self.add_movie_flows:
                if isinstance(message.channel, discord.DMChannel) and message.channel.id == self.add_movie_flows[user_id]['channel'].id:
                    await self._handle_add_movie_flow(message)
                    return

            # --- Handle Vote Selection Response (Text-based DM flow) ---
            # Check for this BEFORE processing general text commands
            if user_id in self.pending_votes:
                 flow_state = self.pending_votes[user_id]
                 if isinstance(message.channel, discord.DMChannel) and message.channel.id == flow_state['channel'].id:
                      await self._handle_vote_selection_response(message, flow_state)
                      return

            # --- Handle Manual Commands (DMs and mentions) ---
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
                            await message.channel.send("Please provide a search term. Example: `search The Matrix`")
                    elif content.startswith('add '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
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
                            parts = content.split(' ', 1)
                            count = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 5
                        except ValueError:
                            await message.channel.send("Invalid number for top count. Please use a number, e.g., `top 10`.")
                            return
                        count = max(1, min(10, count)) # Limit count for text commands
                        await self._handle_top_command(message.channel, count)
                    elif content.startswith('info '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._handle_info_command(message.channel, query)
                        else:
                            await message.channel.send("Please provide a movie title or search term. Example: `info The Matrix`")
                    else:
                         await self._send_help_message(message.channel)
                elif isinstance(message.channel, discord.DMChannel) and not content:
                    await self._send_help_message(message.channel)


        @self.client.event
        async def on_interaction(interaction: discord.Interaction):
            """Handle button interactions (Vote Selection, Movies Pagination)."""
            # discord.py automatically routes interactions to active Views attached to messages.
            # The on_interaction methods within the View classes will be called if the
            # interaction is related to that view.
            # This central on_interaction can be used for logging or fallback handling.
            if interaction.type == discord.InteractionType.component and interaction.data and interaction.data['component_type'] == 2: # Button
                 logger.info(f"Button interaction received: User {interaction.user.id}, Custom ID: {interaction.data.get('custom_id')}, Message ID: {interaction.message.id if interaction.message else 'N/A'}")
                 # The logic for handling specific buttons is in the View classes.
                 # We don't need to explicitly call view.on_interaction or the button handlers here.
                 # discord.py does it automatically if the view is linked to the message.

            # You could add logging for other interaction types here if interested (e.g. Modal submit interactions also come here)
            # logger.debug(f"Received interaction type: {interaction.type}")


    def _register_commands(self):
        """Register Discord slash commands."""
        @self.tree.command(name="add", description="Add a movie to the voting queue with structured input")
        @app_commands.describe(title="Optional: Provide a title to pre-fill the form")
        async def cmd_add_slash(interaction: discord.Interaction, title: Optional[str] = None):
            """Slash command to add a movie, prompting with a modal."""
            # Send the modal. This is the immediate response to the interaction.
            await interaction.response.send_modal(MovieAddModal(self, movie_title=title or ""))
            # The modal's on_submit handles the followup response.

        @self.tree.command(name="recommend", description="Get movie recommendations based on a movie you like")
        @app_commands.describe(
            movie_title="Title of the movie you want recommendations for",
            model="Recommendation model (related or similar)"
        )
        @app_commands.choices(model=[
            app_commands.Choice(name="Related by attributes", value="related"),
            app_commands.Choice(name="Visually similar", value="similar")
        ])
        async def cmd_recommend(interaction: discord.Interaction, movie_title: str, model: str = "related"):
            await self.cmd_recommend(interaction, movie_title, model)

        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="related", description="Find related movies based on a movie in the database")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="info", description="Get detailed info for a movie")(self.cmd_info)
        # self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)


    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            # keep_alive() # Platform-specific, uncomment if needed
            self.client.run(self.discord_token)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token. Please check your DISCORD_TOKEN environment variable.")
        except Exception as e:
            logger.error(f"Error running the bot: {e}", exc_info=True)


    # --- Text Command Handlers (for DMs and mentions) ---

    async def _send_help_message(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Send help information to the channel."""
        embed = discord.Embed(
            title="👋 Hello from Paradiso Bot!",
            description="I'm here to help you manage movie voting for your movie nights!\n\n"
                        "Use slash commands (`/`) in servers for a better experience!\n"
                        "Or mention me or DM me to use text commands:",
            color=0x03a9f4
        )

        embed.add_field(
            name="Text Commands (Mention or DM)",
            value="`add [movie title]` - Start adding a movie (I'll search first, then DM for details if needed)\n"
                  "`vote [movie title]` - Vote for a movie (handles ambiguity)\n"
                  "`movies` - See all movies in the queue (limited list)\n"
                  "`search [query]` - Search for movies (simple query)\n"
                  "`related [query]` - Find related movies (simple query)\n"
                  "`top [count]` - Show top voted movies (limited list)\n"
                  "`info [query]` - Get detailed info for a movie\n"
                  "`help` - Show this help message",
            inline=False
        )

        embed.add_field(
            name="Slash Commands (In Server)",
            value="`/add [title]` - Add a movie using a pop-up form (Recommended!)\n"
                  "`/vote [title]` - Vote for a movie (handles ambiguity via buttons)\n"
                  "`/movies` - See all movies (paginated list)\n"
                  "`/search [query]` - Search movies (supports filters like `year:>2000`)\n"
                  "`/related [query]` - Find related movies (supports filters like `genre:Action`)\n"
                  "`/top [count]` - Show top voted movies (max 20)\n"
                  "`/info [query]` - Get detailed info for a movie\n"
                  "`/help` - Show this help message",
            inline=False
        )

        embed.add_field(
             name="Search Filters (for /search and /related)",
             value="You can filter searches using `key:value`. Examples:\n"
                   "`/search matrix year:1999`\n"
                   "`/search action genre:Comedy director:Nolan`\n"
                   "`/search year>2010 votes:>5`\n"
                   "Use quotes for multi-word values: `/search actor:\"Tom Hanks\"`\n"
                   "Supported keys: `year`, `director`, `actor`, `genre`, `votes`, `rating`.",
             inline=False
        )

        embed.set_footer(text="Happy voting! 🎬")
        await channel.send(embed=embed)

    async def _handle_search_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query_string: str):
        """Handle a text-based search command."""
        try:
            # Text command search does NOT support advanced filters for simplicity
            query = query_string.strip()
            if not query:
                 await channel.send("Please provide a search term.")
                 return

            search_results = self.algolia_client.search(self.algolia_movies_index_name, query, {
                "hitsPerPage": 5,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                 "attributesToHighlight": [
                     "title", "originalTitle", "director", "actors", "year", "plot", "genre"
                ],
                "attributesToSnippet": [
                    "plot:15"
                ]
            })

            await send_search_results_embed(channel, query, search_results["hits"], search_results["nbHits"]) # Use helper

        except Exception as e:
            logger.error(f"Error in manual search command: {e}", exc_info=True)
            await channel.send(f"An error occurred during search: {str(e)}")

    async def _handle_info_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query: str):
        """Handle a text-based info command."""
        try:
            movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name, query)

            if not movie:
                await channel.send(f"Could not find a movie matching '{query}'. Use `search [query]` to find movies.")
                return

            await send_detailed_movie_embed(channel, movie) # Use helper

        except Exception as e:
            logger.error(f"Error in manual info command: {e}", exc_info=True)
            await channel.send(f"An error occurred while fetching movie info: {str(e)}")


    async def _start_add_movie_flow(self, message: discord.Message, title: str):
        """Start the interactive text-based flow to add a movie in DMs (after initial search)."""
        user_id = message.author.id

        if user_id in self.add_movie_flows:
            await message.channel.send("You are already in the process of adding a movie. Please complete or type 'cancel'.")
            if not isinstance(message.channel, discord.DMChannel):
                dm_channel = await message.author.create_dm()
                await message.channel.send(f"📬 You are already in the process of adding a movie. Please check your DMs ({dm_channel.mention}).")
            return

        try:
            # First, search for the movie in Algolia
            search_results = self.algolia_client.search(self.algolia_movies_index_name, title, {
                "hitsPerPage": 3,
                 "attributesToRetrieve": ["objectID", "title", "year", "director", "actors", "genre", "votes"] # Include votes
            })

            if search_results["nbHits"] > 0:
                 embed = discord.Embed(
                     title=f"Movies Found Matching '{title}'",
                     description="The following movies are already in the queue or are potential matches.\n\n"
                                 "If your movie is listed, use `/vote [title]` in a server to vote.\n"
                                 "If your movie is *not* listed, or if you want to add a new entry anyway, reply 'add new' to proceed with manual entry.",
                     color=0xffa500
                 )
                 for i, hit in enumerate(search_results["hits"]):
                      year = f" ({hit.get('year')})" if hit.get('year') is not None else ""
                      embed.add_field(name=f"{i+1}. {hit.get('title', 'Unknown')}{year}", value=f"Votes: {hit.get('votes', 0)}", inline=False)

                 dm_channel = await message.author.create_dm()
                 self.add_movie_flows[user_id] = {
                     'title': title,
                     'stage': 'await_add_new_confirmation',
                     'channel': dm_channel,
                      'original_channel': message.channel
                 }

                 await dm_channel.send(embed=embed)
                 if not isinstance(message.channel, discord.DMChannel):
                      await message.channel.send(f"📬 Found potential matches for '{title}'. Please check your DMs ({dm_channel.mention}) to see if your movie is listed or proceed with manual entry.")

            else:
                # No results found, proceed directly to manual input flow
                dm_channel = await message.author.create_dm()
                self.add_movie_flows[user_id] = {
                    'title': title,
                    'year': None, 'director': None, 'actors': [], 'genre': [],
                    'stage': 'year',
                    'channel': dm_channel,
                    'original_channel': message.channel
                }

                await dm_channel.send(
                    f"📽️ No exact matches found for '{title}'. Let's add it manually!\n\nWhat year was it released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
                if not isinstance(message.channel, discord.DMChannel):
                     await message.channel.send(f"📬 No matches found for '{title}'. Please check your DMs ({dm_channel.mention}) to provide details for manual entry.")


        except Exception as e:
            logger.error(f"Error during initial search in text add flow: {e}", exc_info=True)
            await message.channel.send(f"An error occurred while searching. Please try again.")
            if user_id in self.add_movie_flows:
                 del self.add_movie_flows[user_id]


    async def _handle_add_movie_flow(self, message: discord.Message):
        """Handle responses in the text-based add movie flow (in DMs)."""
        user_id = message.author.id
        flow = self.add_movie_flows.get(user_id)

        if not flow or message.channel.id != flow['channel'].id:
             logger.warning(f"Received message from user {user_id} in incorrect channel or no active flow.")
             return

        response = message.content.strip()

        if response.lower() == 'cancel':
            await message.channel.send("Movie addition cancelled.")
            if 'original_channel' in flow and flow['original_channel'] and not isinstance(flow['original_channel'], discord.DMChannel):
                 try:
                    await flow['original_channel'].send(f"Movie addition for '{flow.get('title', 'a movie')}' was cancelled.")
                 except Exception as e:
                     logger.warning(f"Could not send cancel message to original channel: {e}", exc_info=True)
            del self.add_movie_flows[user_id]
            return

        if flow['stage'] == 'await_add_new_confirmation':
             if response.lower() == 'add new':
                 await message.channel.send("Okay, let's proceed with manual entry.")
                 flow['stage'] = 'year'
                 self.add_movie_flows[user_id] = flow
                 await message.channel.send(
                    f"📽️ What year was '{flow.get('title', 'this movie')}' released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
             else:
                 await message.channel.send("Understood. If you want to add a movie not in the list, please reply 'add new'. Otherwise, use the vote command or 'cancel'.")
                 return

        elif flow['stage'] == 'year':
            if response.lower() == 'unknown':
                flow['year'] = None
            else:
                try:
                    year = int(response)
                    if not 1850 <= year <= datetime.datetime.now().year + 5:
                        await message.channel.send("Please enter a valid 4-digit year (e.g., 2023) or 'unknown'.")
                        return
                    flow['year'] = year
                except ValueError:
                    await message.channel.send("Please enter a valid year (e.g., 2023) or 'unknown'.")
                    return

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

            flow['stage'] = 'confirm_manual'

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
                movie_data = {
                    "objectID": f"manual_{int(time.time())}",
                    "title": flow.get('title', 'Unknown Movie'),
                    "originalTitle": flow.get('title', 'Unknown Movie'),
                    "year": flow['year'],
                    "director": flow['director'] or "Unknown",
                    "actors": flow['actors'],
                    "genre": flow['genre'],
                    "plot": f"Added manually by {message.author.display_name}.",
                    "image": None,
                    "rating": None,
                    "imdbID": None,
                    "tmdbID": None,
                    "source": "manual",
                    "votes": 0,
                    "addedDate": int(time.time()),
                    "addedBy": generate_user_token(str(message.author.id)), # Use helper
                    "voted": False
                }
                # Pass indices to algolia util function
                await self._add_movie_from_flow(user_id, movie_data, message.author, flow.get('original_channel'))

            elif response.lower() in ['no', 'n']:
                await message.channel.send("Okay, let's re-enter the movie details.")
                flow['stage'] = 'year'
                flow['year'] = None; flow['director'] = None; flow['actors'] = []; flow['genre'] = []
                self.add_movie_flows[user_id] = flow
                await message.channel.send(
                    f"📽️ What year was '{flow.get('title', 'this movie')}' released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
            else:
                 await message.channel.send("Please respond with 'yes', 'no', or 'cancel'.")
                 return

        if user_id in self.add_movie_flows and flow['stage'] not in ['year', 'director', 'actors', 'genre', 'confirm_manual', 'await_add_new_confirmation']:
             del self.add_movie_flows[user_id]


    async def _add_movie_from_flow(self, user_id: int, movie_data: Dict[str, Any], author: discord.User, original_channel: Optional[discord.TextChannel]):
        """Helper to add the movie to Algolia and send confirmation after text flow."""
        try:
            # Check if movie already exists in Algolia by title
            existing_movie = await _check_movie_exists(self.algolia_client, self.algolia_movies_index_name, movie_data['title'])

            if existing_movie:
                 await self.add_movie_flows[user_id]['channel'].send(
                    f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                 if original_channel and not isinstance(original_channel, discord.DMChannel):
                      try: await original_channel.send(f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                      except Exception as e: logger.warning(f"Could not send exists message to original channel: {e}", exc_info=True)
                 del self.add_movie_flows[user_id]
                 return

            # Add movie to Algolia
            add_movie_to_algolia(self.algolia_client, self.algolia_movies_index_name, movie_data)
            logger.info(f"Added movie in text flow: {movie_data.get('title')} ({movie_data.get('objectID')})")

            # Create embed for movie confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_data['title']} ({movie_data['year'] if movie_data['year'] is not None else 'N/A'})",
                description=movie_data.get("plot", "No plot available."),
                color=0x00ff00
            )

            if movie_data.get("director") and movie_data["director"] != "Unknown": embed.add_field(name="Director", value=movie_data["director"], inline=True)
            if movie_data.get("actors"): embed.add_field(name="Starring", value=", ".join(movie_data["actors"][:5]), inline=False)
            if movie_data.get("genre"): embed.add_field(name="Genre", value=", ".join(movie_data["genre"]), inline=True)
            if movie_data.get("image"): embed.set_thumbnail(url=movie_data["image"]) # Use 'image'

            embed.set_footer(text=f"Added by {author.display_name}")

            await self.add_movie_flows[user_id]['channel'].send("✅ Movie added to the voting queue!", embed=embed)
            if original_channel and original_channel != self.add_movie_flows[user_id]['channel'] and not isinstance(original_channel, discord.DMChannel):
                 try: await original_channel.send(f"✅ Movie '{movie_data['title']}' added to the voting queue!")
                 except Exception as e: logger.warning(f"Could not send add confirmation to original channel: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error adding movie in text flow: {e}", exc_info=True)
            await self.add_movie_flows[user_id]['channel'].send(f"❌ An error occurred while adding the movie: {str(e)}")
            if original_channel and not isinstance(original_channel, discord.DMChannel):
                 try: await original_channel.send(f"❌ An error occurred while adding the movie '{movie_data.get('title', 'Unknown Movie')}': {str(e)}")
                 except Exception as e: logger.warning(f"Could not send add error to original channel: {e}", exc_info=True)

        finally:
            if user_id in self.add_movie_flows:
                 del self.add_movie_flows[user_id]


    async def _handle_vote_command(self, channel: Union[discord.TextChannel, discord.DMChannel], author: discord.User, title: str):
        """Handle a text-based vote command."""
        try:
            # Find potential movies for voting
            search_results = search_movies_for_vote(self.algolia_client, self.algolia_movies_index_name, title)

            if search_results["nbHits"] == 0:
                await channel.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                movie_to_vote = hits[0]
                await channel.send(f"Found '{movie_to_vote['title']}'. Recording your vote...")
                # Pass client and index names to vote util function
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name, 
                                                      self.algolia_votes_index_name, movie_to_vote["objectID"], str(author.id))

                if success:
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"): embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {author.display_name}")
                    await channel.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted": await channel.send(f"❌ You have already voted for '{movie_to_vote['title']}'!")
                    else:
                        logger.error(f"Error recording vote for single match (text cmd): {result}")
                        await channel.send(f"❌ An error occurred while recording your vote.")

            else:
                # Multiple matches, present choices in an embed and ask user to reply with a number (in DM)
                choices = hits[:5] # Limit to top 5 choices for text command

                embed = discord.Embed(
                    title=f"Multiple movies found for '{title}'",
                    description="Please reply with the number of the movie you want to vote for (1-5), or type '0' or 'cancel' to cancel.",
                    color=0xffa500
                )

                for i, movie in enumerate(choices):
                     year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                     votes = movie.get('votes', 0)
                     embed.add_field(
                          name=f"{i+1}. {movie.get('title', 'Unknown')}{year}",
                          value=f"Votes: {votes}",
                          inline=False
                     )

                # Store the state for the next message from this user
                # This is for the text-based DM response selection flow
                dm_channel = await author.create_dm() # Send selection prompt to DM
                self.pending_votes[author.id] = {
                    'channel': dm_channel,
                    'choices': choices,
                    'timestamp': time.time()
                }

                await dm_channel.send(embed=embed)
                if not isinstance(channel, discord.DMChannel):
                    await channel.send(f"Found multiple matches for '{title}'. Please check your DMs ({dm_channel.mention}) to select the movie you want to vote for.")


        except Exception as e:
            logger.error(f"Error in manual vote command for title '{title}': {e}", exc_info=True)
            await channel.send(f"❌ An error occurred while searching for the movie: {str(e)}")


    async def _handle_movies_command(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Handle a text-based movies command (limited list, no pagination)."""
        try:
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, 10) # Get top 10

            if not top_movies:
                await channel.send("No movies have been voted for yet! Use `add [title]` to add one or `/add` in a server.")
                return

            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting (Top 10)",
                description=f"Here are the current top voted movies:",
                color=0x03a9f4,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )

            for i, movie in enumerate(top_movies):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                votes = movie.get("votes", 0)
                rating = movie.get("rating")

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {year.strip() or 'N/A'}",
                     f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                ]
                plot = movie.get("plot", "No description available.")
                if plot and not plot.startswith("Added manually by "):
                     if len(plot) > 100: plot = plot[:100] + "..."
                     movie_details.append(f"**Plot**: {plot}")
                elif plot == "No description available.":
                     movie_details.append(f"**Plot**: No description available.")

                embed.add_field(
                    name=f"{medal} {title}{year} - {votes} votes",
                    value="\n".join(movie_details),
                    inline=False
                )

            embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server. Use /movies in a server for full list.")

            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual movies command: {e}", exc_info=True)
            await channel.send("An error occurred while getting the movies. Please try again.")

    async def _handle_top_command(self, channel: Union[discord.TextChannel, discord.DMChannel], count: int = 5):
        """Handle a text-based top command."""
        try:
            count = max(1, min(10, count))
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, count)

            if not top_movies:
                await channel.send("❌ No movies have been voted for yet!")
                return

            embed = discord.Embed(
                title=f"🏆 Top {len(top_movies)} Voted Movies",
                description="Here are the most popular movies for our next movie night!",
                color=0x00ff00
            )

            for i, movie in enumerate(top_movies):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
                movie_details = [ f"**Votes**: {movie.get('votes', 0)}", f"**Year**: {movie.get('year', 'N/A')}", ]
                rating = movie.get("rating")
                if rating is not None: movie_details.append(f"**Rating**: ⭐ {rating}/10")
                if movie.get("director") and movie["director"] != "Unknown": movie_details.append(f"**Director**: {movie['director']}")
                if movie.get("genre"): movie_details.append(f"**Genre**: {', '.join(movie['genre'])}")

                embed.add_field(
                    name=f"{medal} {movie.get('title', 'Unknown')}",
                    value="\n".join(movie_details),
                    inline=False
                )

            embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server!")

            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual top command: {e}", exc_info=True)
            await channel.send(f"❌ An error occurred: {str(e)}")


    # --- Vote Selection Response Handler (for text-based DM flow) ---
    async def _handle_vote_selection_response(self, message: discord.Message, flow_state: Dict[str, Any]):
        """Handles a user's numerical response during the text-based vote selection flow (in DMs)."""
        user_id = message.author.id
        response = message.content.strip()

        if time.time() - flow_state.get('timestamp', 0) > 300:
             await message.channel.send("Your previous vote selection session timed out. Please use the vote command again.")
             del self.pending_votes[user_id]
             return

        if response.lower() == 'cancel' or response == '0':
             await message.channel.send("Vote selection cancelled.")
             del self.pending_votes[user_id]
             return

        try:
            selection = int(response)
            choices = flow_state['choices']

            if 1 <= selection <= len(choices):
                chosen_movie = choices[selection - 1]
                await message.channel.send(f"Okay, you selected '{chosen_movie['title']}'. Recording your vote...")

                # Pass client and index names to vote util function
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name, 
                                                      self.algolia_votes_index_name, chosen_movie["objectID"], str(user_id))

                if success:
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"): embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {message.author.display_name}")
                    await message.channel.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted": await message.channel.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                    else:
                        logger.error(f"Error recording vote during text selection: {result}")
                        await message.channel.send(f"❌ An error occurred while recording your vote.")

                del self.pending_votes[user_id]

            else:
                await message.channel.send(f"Invalid selection. Please enter a number between 1 and {len(choices)}, or 0 to cancel.")

        except ValueError:
            await message.channel.send(f"Invalid input. Please enter a number corresponding to your choice, or 0 to cancel.")
        except Exception as e:
            logger.error(f"Error during text vote selection response handling for user {user_id}: {e}", exc_info=True)
            await message.channel.send("An unexpected error occurred. Please try voting again.")
            if user_id in self.pending_votes:
                 del self.pending_votes[user_id]


    # --- Slash Command Handlers ---

    # cmd_add_slash handled via Modal class now

    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        """Vote for a movie in the queue, handles ambiguity via buttons."""
        await interaction.response.defer(thinking=True)

        user_id = interaction.user.id

        try:
            # Find potential movies (up to 5 for selection buttons)
            search_results = search_movies_for_vote(self.algolia_client, self.algolia_movies_index_name, title)

            if search_results["nbHits"] == 0:
                await interaction.followup.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `/movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                movie_to_vote = hits[0]
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name, 
                                                      self.algolia_votes_index_name, movie_to_vote["objectID"], str(user_id))

                if success:
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"): embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {interaction.user.display_name}")
                    await interaction.followup.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted": await interaction.followup.send(f"❌ You have already voted for '{movie_to_vote['title']}'!")
                    else:
                        logger.error(f"Error recording vote for single match (slash cmd): {result}")
                        await interaction.followup.send(f"❌ An error occurred while recording your vote.")

            else:
                # Multiple matches, start interactive selection flow using buttons
                choices = hits[:5] # Limit to top 5 for button UI

                embed = discord.Embed(
                    title=f"Multiple movies found for '{title}'",
                    description="Please select the movie you want to vote for:",
                    color=0xffa500
                )

                choice_list = []
                for i, movie in enumerate(choices):
                     year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                     votes = movie.get('votes', 0)
                     choice_list.append(f"{i+1}. {movie.get('title', 'Unknown')}{year} (Votes: {votes})")

                embed.add_field(name="Choices", value="\n".join(choice_list), inline=False)
                embed.set_footer(text="Select a number below or press Cancel.")

                # Create the View with buttons - Pass bot_instance and relevant data
                view = VoteSelectionView(self, user_id, choices)

                # Send the message with embed and view
                message = await interaction.followup.send(embed=embed, view=view)

                # Store the message ID and state for the view's timeout handling
                self.vote_messages[message.id] = {'user_id': user_id, 'choices': choices}
                view.message = message # Link the view to the message


        except Exception as e:
            logger.error(f"Error in /vote command for title '{title}': {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while searching for the movie: {str(e)}")


    async def cmd_movies(self, interaction: discord.Interaction):
        """List all movies in the voting queue with pagination."""
        await interaction.response.defer()

        try:
            # Fetch all movies (or a large enough number for pagination)
            all_movies = await get_all_movies(self.algolia_client, self.algolia_movies_index_name)

            if not all_movies:
                await interaction.followup.send("No movies have been added yet! Use `/add` to add one.")
                return

            movies_per_page = 10 # Define how many movies per page
            detailed_count = 5 # Define how many detailed entries per page

            # Create and send the initial page embed and view
            # Pass bot_instance and relevant data
            view = MoviesPaginationView(self, interaction.user.id, all_movies, movies_per_page, detailed_count)

            # Render the first page and get the embed
            embed = await self._get_movies_page_embed(all_movies, view.current_page, movies_per_page, detailed_count, view.total_pages)

            # Update buttons state for the first page
            await view.update_buttons()

            # Send the initial message with the view
            message = await interaction.followup.send(embed=embed, view=view)

            # Store state for pagination handler
            self.movies_pagination_state[message.id] = {
                'user_id': interaction.user.id,
                'all_movies': all_movies,
                'current_page': view.current_page,
                'movies_per_page': movies_per_page,
                'detailed_count': detailed_count
            }
            view.message = message # Link the view to the message

        except Exception as e:
            logger.error(f"Error in /movies command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while getting the movies. Please try again.")

    # Helper to get the embed for a specific page of movies
    async def _get_movies_page_embed(self, all_movies: List[Dict[str, Any]], current_page: int, movies_per_page: int, detailed_count: int, total_pages: int) -> discord.Embed:
         """Creates an embed for a specific page of the movie list."""
         start_index = current_page * movies_per_page
         end_index = start_index + movies_per_page
         page_movies = all_movies[start_index:end_index]

         embed = discord.Embed(
            title=f"🎬 Paradiso Movie Night Voting (Page {current_page + 1}/{total_pages})",
            description=f"Showing movies {start_index + 1}-{min(end_index, len(all_movies))} out of {len(all_movies)}:",
            color=0x03a9f4,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
         )

         for i, movie in enumerate(page_movies):
             global_index = start_index + i
             title = movie.get("title", "Unknown")
             year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
             votes = movie.get("votes", 0)
             rating = movie.get("rating")

             # Medals only for the overall top 3
             medal = "🥇" if global_index == 0 else "🥈" if global_index == 1 else "🥉" if global_index == 2 else f"{global_index + 1}."


             if i < detailed_count:
                 movie_details = [
                      f"**Votes**: {votes}",
                      f"**Year**: {year.strip() or 'N/A'}",
                      f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                 ]
                 plot = movie.get("plot", "No description available.")
                 if plot and not plot.startswith("Added manually by "):
                      if len(plot) > 150: plot = plot[:150] + "..."
                      movie_details.append(f"**Plot**: {plot}")
                 elif plot == "No description available.":
                      movie_details.append(f"**Plot**: No description available.")


                 embed.add_field(
                     name=f"{medal} {title}{year}",
                     value="\n".join(movie_details),
                     inline=False
                 )

                 if i == 0 and movie.get("image"): embed.set_thumbnail(url=movie["image"])

             else:
                  details_line = f"Votes: {votes} | Year: {year.strip() or 'N/A'} | Rating: {f'⭐ {rating}/10' if rating is not None else 'N/A'}"
                  embed.add_field(
                     name=f"{medal} {title}{year}",
                     value=details_line,
                     inline=False
                 )

         embed.set_footer(text=f"Use /vote to vote for a movie! | Page {current_page + 1}/{total_pages}")
         return embed


    async def cmd_search(self, interaction: discord.Interaction, query: str):
        """Search for movies in the database with optional filters."""
        await interaction.response.defer()

        try:
            # Parse the query string for filters
            main_query, filter_string = parse_algolia_filters(query) # Use helper
            logger.info(f"Parsed Search: Query='{main_query}', Filters='{filter_string}'")

            search_params = {
                "hitsPerPage": 10,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                 "attributesToHighlight": [
                     "title", "originalTitle", "director", "actors", "year", "plot", "genre"
                ],
                 "attributesToSnippet": [ "plot:20" ]
            }

            if filter_string: search_params["filters"] = filter_string

            # Search in Algolia - Use client.search with index name
            search_results = self.algolia_client.search(self.algolia_movies_index_name, main_query, search_params)

            # Use interaction.followup and helper function
            await send_search_results_embed(interaction.followup, query, search_results["hits"], search_results["nbHits"])

        except Exception as e:
            logger.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred during search: {str(e)}")


    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms (using attribute search as proxy) with optional filters."""
        await interaction.response.defer()

        try:
            main_query, filter_string = parse_algolia_filters(query) # Use helper
            logger.info(f"Parsed Related: Query='{main_query}', Filters='{filter_string}'")

            # Find the reference movie
            reference_movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name, main_query)

            if not reference_movie:
                await interaction.followup.send(f"Could not find a movie matching '{main_query}' in the database to find related titles.")
                return

            related_query_parts = []
            if reference_movie.get("genre"): related_query_parts.extend(reference_movie["genre"])
            if reference_movie.get("director") and reference_movie.get("director") != "Unknown": related_query_parts.append(reference_movie["director"])
            if reference_movie.get("actors"): related_query_parts.extend(reference_movie["actors"][:3])

            if not related_query_parts:
                 related_query = reference_movie.get("title", main_query)
                 logger.info(f"No rich attributes for related search for '{reference_movie.get('title')}', using title as query.")
            else:
                 related_query = " ".join(related_query_parts)
                 logger.info(f"Generated related query for '{reference_movie.get('title')}': {related_query}")


            related_search_params = {
                "hitsPerPage": 5,
                 "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                 "attributesToHighlight": [ "director", "actors", "genre" ],
            }

            combined_filters = f"NOT objectID:{reference_movie['objectID']}"
            if filter_string: combined_filters = f"({combined_filters}) AND ({filter_string})"
            related_search_params["filters"] = combined_filters

            # Perform the related search - Use client with index name
            related_results = self.algolia_client.search(self.algolia_movies_index_name, related_query, related_search_params)

            if related_results["nbHits"] == 0:
                await interaction.followup.send(f"Couldn't find any movies clearly related to '{reference_movie['title']}' based on its attributes and your filters.")
                return

            embed = discord.Embed(
                title=f"🎬 Movies Related to '{reference_movie.get('title', 'Unknown')}'",
                description=f"Based on attributes like genre, director, and actors:",
                color=0x03a9f4
            )

            ref_year = f" ({reference_movie.get('year')})" if reference_movie.get('year') is not None else ""
            ref_genre = ", ".join(reference_movie.get("genre", [])) or "N/A"
            ref_director = reference_movie.get("director", "Unknown")
            ref_rating = reference_movie.get("rating")

            embed.add_field(
                name=f"📌 Reference Movie: {reference_movie.get('title', 'Unknown')}{ref_year}",
                value=f"**Genre**: {ref_genre}\n"
                      f"**Director**: {ref_director}\n"
                      f"**Rating**: ⭐ {ref_rating}/10" if ref_rating is not None else "Rating: N/A\n"
                      f"**Votes**: {reference_movie.get('votes', 0)}",
                inline=False
            )

            if related_results["hits"]: embed.add_field(name="Related Movies", value="─────────────", inline=False)

            for i, movie in enumerate(related_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                votes = movie.get("votes", 0)
                rating = movie.get("rating")

                relation_points = []
                genre_highlight = movie.get("_highlightResult", {}).get("genre", [])
                common_genres_display = [h['value'] for h in genre_highlight if h.get('matchedWords')]
                if common_genres_display: relation_points.append(f"**Common Genres**: {', '.join(common_genres_display)}")

                director_highlight = movie.get("_highlightResult", {}).get("director", {})
                if director_highlight.get('matchedWords'): relation_points.append(f"**Same Director**: {director_highlight['value']}")

                actors_highlight = movie.get("_highlightResult", {}).get("actors", [])
                common_actors_display = [h['value'] for h in actors_highlight if h.get('matchedWords')]
                if common_actors_display: common_actors_display.append(f"**Common Actors**: {', '.join(common_actors_display)}")


                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {movie.get('year', 'N/A')}",
                     f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A",
                ]
                movie_details = [detail for detail in movie_details if detail]

                embed.add_field(
                    name=f"{i+1}. {title}{year}",
                    value="\n".join(relation_points + movie_details) or "Details not available.",
                    inline=False
                )

            if reference_movie.get("image"): embed.set_thumbnail(url=reference_movie["image"])

            embed.set_footer(text="Related search based on movie attributes. Use /vote to vote!")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /related command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while finding related movies: {str(e)}")

    async def cmd_recommend(self, interaction: discord.Interaction, movie_title: str, model: str = "related"):
        """Get movie recommendations using Algolia Recommend API.

        Args:
            interaction: interaction API.
            movie_title: Title of the reference movie
            model: Recommendation model to use (related or similar)
        """
        await interaction.response.defer(thinking=True)

        try:
            # Find the reference movie
            reference_movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name,
                                                        movie_title)

            if not reference_movie:
                await interaction.followup.send(
                    f"Could not find a movie matching '{movie_title}' to find recommendations.")
                return

            # Set up recommendations params
            recommend_params = {
                "indexName": self.algolia_movies_index_name,
                "threshold": 0,  # Return all recommendations
                "maxRecommendations": 5  # Limit to 5 recommendations
            }

            if model.lower() == "similar":
                # Visual similarity recommendations (if image exists)
                if not reference_movie.get("image"):
                    await interaction.followup.send(
                        f"Movie '{reference_movie['title']}' doesn't have an image for visual similarity search.")
                    return

                # Use "Looking Similar" model with image URL
                recommendations = self.recommend_client.get_looking_similar_objects({
                    **recommend_params,
                    "objectID": reference_movie["objectID"]
                })
            else:
                # Use "Related Products" model for semantic relationship
                recommendations = self.recommend_client.get_related_products({
                    **recommend_params,
                    "objectID": reference_movie["objectID"]
                })

            # Create embed for results
            embed = discord.Embed(
                title=f"🎬 Recommended Movies Based on '{reference_movie.get('title', 'Unknown')}'",
                description=f"Here are movies you might enjoy if you liked this one:",
                color=0x03a9f4
            )

            # Add reference movie details
            ref_year = f" ({reference_movie.get('year')})" if reference_movie.get('year') is not None else ""
            ref_genre = ", ".join(reference_movie.get("genre", [])) or "N/A"
            ref_director = reference_movie.get("director", "Unknown")
            ref_rating = reference_movie.get("rating")

            embed.add_field(
                name=f"📌 Reference Movie: {reference_movie.get('title', 'Unknown')}{ref_year}",
                value=f"**Genre**: {ref_genre}\n"
                      f"**Director**: {ref_director}\n"
                      f"**Rating**: ⭐ {ref_rating}/10" if ref_rating is not None else "Rating: N/A\n"
                                                                                      f"**Votes**: {reference_movie.get('votes', 0)}",
                inline=False
            )

            # Add separator
            embed.add_field(name="Recommended Movies", value="─────────────", inline=False)

            # Process recommendations
            hits = recommendations.get("results", [])
            if not hits:
                embed.add_field(
                    name="No Recommendations Found",
                    value="Could not find any similar movies in the database.",
                    inline=False
                )
            else:
                for i, hit in enumerate(hits):
                    title = hit.get("title", "Unknown")
                    year = f" ({hit.get('year')})" if hit.get('year') is not None else ""
                    votes = hit.get("votes", 0)
                    rating = hit.get("rating")

                    movie_details = [
                        f"**Votes**: {votes}",
                        f"**Year**: {hit.get('year', 'N/A')}",
                        f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A",
                    ]

                    if hit.get("genre"):
                        movie_details.append(f"**Genre**: {', '.join(hit.get('genre', []))}")

                    embed.add_field(
                        name=f"{i + 1}. {title}{year}",
                        value="\n".join(movie_details) or "Details not available.",
                        inline=False
                    )

            # Set thumbnail from reference movie
            if reference_movie.get("image"):
                embed.set_thumbnail(url=reference_movie["image"])

            # Set footer based on model
            model_name = "visual similarity" if model.lower() == "similar" else "content relationships"
            embed.set_footer(text=f"Recommendations based on {model_name}. Use /vote to vote!")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /recommend command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while finding recommendations: {str(e)}")

    async def cmd_top(self, interaction: discord.Interaction, count: int = 5):
        """Show the top voted movies."""
        await interaction.response.defer(thinking=True)

        try:
            count = max(1, min(20, count))
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, count)

            if not top_movies:
                await interaction.followup.send("❌ No movies have been voted for yet!")
                return

            embed = discord.Embed(
                title=f"🏆 Top {len(top_movies)} Voted Movies",
                description="Here are the most popular movies for our next movie night!",
                color=0x00ff00
            )

            for i, movie in enumerate(top_movies):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
                movie_details = [ f"**Votes**: {movie.get('votes', 0)}", f"**Year**: {movie.get('year', 'N/A')}", ]
                rating = movie.get("rating")
                if rating is not None: movie_details.append(f"**Rating**: ⭐ {rating}/10")
                if movie.get("director") and movie["director"] != "Unknown": movie_details.append(f"**Director**: {movie['director']}")
                if movie.get("genre"): movie_details.append(f"**Genre**: {', '.join(movie['genre'])}")

                embed.add_field(
                    name=f"{medal} {movie.get('title', 'Unknown')}",
                    value="\n".join(movie_details),
                    inline=False
                )

            embed.set_footer(text="Use /vote to vote for a movie!")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /top command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def cmd_info(self, interaction: discord.Interaction, query: str):
         """Get detailed info for a movie."""
         await interaction.response.defer(thinking=True)

         try:
             movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name, query)

             if not movie:
                 await interaction.followup.send(f"Could not find a movie matching '{query}'. Use `/search [query]` to find movies.")
                 return

             # Use interaction.followup and helper function
             await send_detailed_movie_embed(interaction.followup, movie)

         except Exception as e:
             logger.error(f"Error in /info command: {e}", exc_info=True)
             await interaction.followup.send(f"❌ An error occurred while fetching movie info: {str(e)}")


def main():
    """Run the bot."""
    load_dotenv()
    discord_token = os.getenv('DISCORD_TOKEN')
    algolia_app_id = os.getenv('ALGOLIA_APP_ID')
    algolia_api_key = os.getenv('ALGOLIA_BOT_SECURED_KEY')
    algolia_movies_index = os.getenv('ALGOLIA_MOVIES_INDEX')
    algolia_votes_index = os.getenv('ALGOLIA_VOTES_INDEX')
    algolia_actors_index = os.getenv('ALGOLIA_ACTORS_INDEX', 'paradiso_actors') # Keep name, default if missing

    if not all([discord_token, algolia_app_id, algolia_api_key,
                algolia_movies_index, algolia_votes_index]):
        missing = [name for name, value in {
            'DISCORD_TOKEN': discord_token,
            'ALGOLIA_APP_ID': algolia_app_id,
            'ALGOLIA_BOT_SECURED_KEY': algolia_api_key,
            'ALGOLIA_MOVIES_INDEX': algolia_movies_index,
            'ALGOLIA_VOTES_INDEX': algolia_votes_index
        }.items() if not value]
        logger.error(f"Missing essential environment variables: {', '.join(missing)}")
        logger.error("Please ensure they are set in your .env file.")
        exit(1)

    # keep_alive() # Platform-specific, uncomment if needed

    logger.info(f"Starting with token: {discord_token[:5]}...{discord_token[-5:]}")
    logger.info(f"Using Algolia app ID: {algolia_app_id}")
    logger.info(f"Using Algolia movies index: {algolia_movies_index}")
    logger.info(f"Using Algolia votes index: {algolia_votes_index}")
    logger.info(f"Using Algolia actors index: {algolia_actors_index} (Note: This index is not actively used in this version)")

    bot = ParadisoBot(
        discord_token=discord_token,
        algolia_app_id=algolia_app_id,
        algolia_api_key=algolia_api_key,
        algolia_movies_index=algolia_movies_index,
        algolia_votes_index=algolia_votes_index,
        algolia_actors_index=algolia_actors_index
    )

    # Event listeners and command handlers are registered using decorators within the bot class.
    # Button/Modal interactions are handled by methods within the View/Modal classes
    # and routed by discord.py's internal dispatch.

    bot.run()


if __name__ == "__main__":
    if not os.path.exists(".env") and not os.environ.get('DISCORD_TOKEN'):
         logger.error("No .env file found, and DISCORD_TOKEN is not set in environment variables.")
         logger.error("Please create a .env file or set environment variables.")
         exit(1)
    main()