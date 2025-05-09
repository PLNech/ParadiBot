Okay, you are absolutely correct about the `AttributeError` and the import path. `SearchClient.Index` is not a valid type hint in `algoliasearch<4.0.0`. We will change the type hints to `object` (a common fallback when the specific type isn't easily accessible) and fix the import paths to match your `utils.algolia` structure.

Here is the updated code for each file:

**1. `main_bot.py`**

```python
#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia Pure v6 - Modular)

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
- FIX: Corrected Algolia index type hint and utils import paths.

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
from discord import app_commands
from discord.ui import Modal # Only Modal needed here for the class reference
from dotenv import load_dotenv
from algoliasearch.search_client import SearchClient # Algolia client initialized here

# Import utilities - Corrected import path
from utils.algolia import (
    add_movie_to_algolia, vote_for_movie, get_movie_by_id,
    find_movie_by_title, search_movies_for_vote, get_top_movies, get_all_movies,
    generate_user_token, _is_float, _check_movie_exists # Import helpers and interaction functions
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
        StreamHandler() # Use StreamHandler for console output
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

        # Algolia Index names - stored for passing to utils
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
        intents.members = True # Ensure this is enabled in the Discord dev portal too
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # Initialize Algolia client and indices
        # Pass index objects to utility functions that need them
        self.algolia_client = SearchClient.create(algolia_app_id, algolia_api_key)
        self.movies_index = self.algolia_client.init_index(algolia_movies_index)
        self.votes_index = self.algolia_client.init_index(algolia_votes_index)
        # self.actors_index = self.algolia_client.init_index(algolia_actors_index) # Not currently used


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

                        # Check if the last message was sent less than 60 seconds ago by the bot
                        if last_bot_message and (datetime.datetime.utcnow() - last_bot_message.created_at.replace(tzinfo=datetime.timezone.utc)).total_seconds() < 60:
                             logger.info("Skipping welcome message to avoid spam.")
                        else:
                             await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                             logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        logger.error(f"Error checking last message/sending welcome in #paradiso: {e}", exc_info=True)
                        # Attempt to send anyway as a fallback
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
                # await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID)) # Sync for a specific guild
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
                    # Simple command parsing (no complex filter syntax here)
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
            # Pass the bot instance to the modal so it can access shared resources like Algolia indices
            await interaction.response.send_modal(MovieAddModal(self, movie_title=title or ""))
            # The modal's on_submit handles the followup response.


        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="related", description="Find related movies based on a movie in the database")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="info", description="Get detailed info for a movie")(self.cmd_info)
        self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)


    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            # Assumes keep_alive() runs a web server in a separate thread/process
            # from keep_alive import keep_alive # Import if keep_alive.py exists
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

            # Perform search using the movies_index instance
            search_results = self.movies_index.search(query, {
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

            # Use the helper function to send the embed
            await send_search_results_embed(channel, query, search_results["hits"], search_results["nbHits"])

        except Exception as e:
            logger.error(f"Error in manual search command: {e}", exc_info=True)
            await channel.send(f"An error occurred during search: {str(e)}")

    async def _handle_info_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query: str):
        """Handle a text-based info command."""
        try:
            # Use the helper function to find the movie by title, passing the movies_index
            movie = await find_movie_by_title(self.movies_index, query)

            if not movie:
                await channel.send(f"Could not find a movie matching '{query}'. Use `search [query]` to find movies.")
                return

            # Use the helper function to send the detailed embed
            await send_detailed_movie_embed(channel, movie)

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
            # First, search for the movie in Algolia using the movies_index instance
            search_results = self.movies_index.search(title, {
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
                 try: await flow['original_channel'].send(f"Movie addition for '{flow.get('title', 'a movie')}' was cancelled.")
                 except Exception as e: logger.warning(f"Could not send cancel message to original channel: {e}", exc_info=True)
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
            # Check if movie already exists in Algolia by title - Pass index to helper
            existing_movie = await _check_movie_exists(self.movies_index, movie_data['title'])

            if existing_movie:
                 await self.add_movie_flows[user_id]['channel'].send(
                    f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                 if original_channel and not isinstance(original_channel, discord.DMChannel):
                      try: await original_channel.send(f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                      except Exception as e: logger.warning(f"Could not send exists message to original channel: {e}", exc_info=True)
                 del self.add_movie_flows[user_id]
                 return

            # Add movie to Algolia - Pass index to helper
            add_movie_to_algolia(self.movies_index, movie_data)
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
            # Find potential movies for voting - Pass index to helper
            search_results = search_movies_for_vote(self.movies_index, title)

            if search_results["nbHits"] == 0:
                await channel.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                movie_to_vote = hits[0]
                await channel.send(f"Found '{movie_to_vote['title']}'. Recording your vote...")
                # Pass indices to vote util function
                success, result = await vote_for_movie(self.movies_index, self.votes_index, movie_to_vote["objectID"], str(author.id))

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
            top_movies = await get_top_movies(self.movies_index, 10) # Pass index, Get top 10

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
            top_movies = await get_top_movies(self.movies_index, count) # Pass index

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

                # Pass indices to vote util function
                success, result = await vote_for_movie(self.movies_index, self.votes_index, chosen_movie["objectID"], str(user_id))

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
            # Find potential movies (up to 5 for selection buttons) - Pass index to helper
            search_results = search_movies_for_vote(self.movies_index, title)

            if search_results["nbHits"] == 0:
                await interaction.followup.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `/movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                movie_to_vote = hits[0]
                # Pass indices to vote util function
                success, result = await vote_for_movie(self.movies_index, self.votes_index, movie_to_vote["objectID"], str(user_id))

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
            # Fetch all movies (or a large enough number for pagination) - Pass index to helper
            all_movies = await get_all_movies(self.movies_index)

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
            # Parse the query string for filters - Use helper function
            main_query, filter_string = parse_algolia_filters(query)
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

            # Search in Algolia - Use the movies_index instance
            search_results = self.movies_index.search(main_query, search_params)

            # Use interaction.followup and helper function to send the embed
            await send_search_results_embed(interaction.followup, query, search_results["hits"], search_results["nbHits"])

        except Exception as e:
            logger.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred during search: {str(e)}")


    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms (using attribute search as proxy) with optional filters."""
        await interaction.response.defer()

        try:
            # First parse the query string for filters - Use helper function
            main_query, filter_string = parse_algolia_filters(query)
            logger.info(f"Parsed Related: Query='{main_query}', Filters='{filter_string}'")

            # Find the reference movie - Pass movies_index to helper
            reference_movie = await find_movie_by_title(self.movies_index, main_query)

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

            # Perform the related search - Use the movies_index instance
            related_results = self.movies_index.search(related_query, related_search_params)

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
                if common_actors_display: relation_points.append(f"**Common Actors**: {', '.join(common_actors_display)}")


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

    async def cmd_top(self, interaction: discord.Interaction, count: int = 5):
        """Show the top voted movies."""
        await interaction.response.defer(thinking=True)

        try:
            count = max(1, min(20, count))
            top_movies = await get_top_movies(self.movies_index, count) # Pass index to helper

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
             # Find the movie by title - Pass movies_index to helper
             movie = await find_movie_by_title(self.movies_index, query)

             if not movie:
                 await interaction.followup.send(f"Could not find a movie matching '{query}'. Use `/search [query]` to find movies.")
                 return

             # Use interaction.followup and helper function to send the embed
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

    # No need to manually add listeners for events handled by @client.event


    bot.run()


if __name__ == "__main__":
    # Check for .env file presence or DISCORD_TOKEN environment variable
    if not os.path.exists(".env") and not os.environ.get('DISCORD_TOKEN'):
         logger.error("No .env file found, and DISCORD_TOKEN is not set in environment variables.")
         logger.error("Please create a .env file or set environment variables.")
         exit(1)
    main()
```

**2. `utils/__init__.py`** (No changes needed)

```python
# This file makes the 'utils' directory a Python package.
# It can be left empty or used to import common utilities directly.
```

**3. `utils/algolia.py`** (Renamed from `algolia_utils.py`, fixed type hints)

```python
import hashlib
import time
import random
import logging
from typing import List, Dict, Any, Optional, Union, Tuple

from algoliasearch.search_client import SearchClient # Needs client import here

logger = logging.getLogger("paradiso_bot") # Use the same logger


# Helper function moved from bot class
def generate_user_token(user_id: str) -> str:
    """Generate a consistent, non-reversible user token for Algolia from Discord user ID."""
    return hashlib.sha256(user_id.encode()).hexdigest()

# Helper function moved from bot class
def _is_float(value: Any) -> bool:
    """Helper to check if a value can be converted to a float."""
    if value is None:
         return False
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


# Algolia interaction methods - now take index clients as arguments
# --- FIX: Changed type hint from SearchClient.Index to object ---
async def _check_movie_exists(movies_index: object, title: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a movie with a similar title already exists in Algolia.
    Uses search and checks for strong matches.
    """
    if not title: return None
    try:
        # Use passed index (which is the object returned by init_index)
        search_result = movies_index.search(title, {
            "hitsPerPage": 5,
             "attributesToRetrieve": ["objectID", "title"],
             "attributesToHighlight": ["title"],
             "typoTolerance": "strict"
        })

        if search_result["nbHits"] == 0:
            return None

        for hit in search_result["hits"]:
             title_highlight = hit.get("_highlightResult", {}).get("title", {})
             if title_highlight.get('matchLevel') == 'full':
                  logger.info(f"Existing movie check: Found full title match for '{title}': {hit['objectID']}")
                  return hit
             if hit.get("title", "").lower() == title.lower():
                  logger.info(f"Existing movie check: Found exact string match for '{title}': {hit['objectID']}")
                  return hit

        logger.info(f"Existing movie check: No strong title match for '{title}' among top hits.")
        return None

    except Exception as e:
        logger.error(f"Error checking existence for title '{title}' in Algolia: {e}", exc_info=True)
        return None


# --- FIX: Changed type hint from SearchClient.Index to object ---
def add_movie_to_algolia(movies_index: object, movie_data: Dict[str, Any]) -> None:
    """Add a movie to Algolia movies index."""
    try:
        # Use passed index (which is the object returned by init_index)
        # This is synchronous for algoliasearch<4.0.0
        movies_index.save_object(movie_data)
        logger.info(f"Added movie to Algolia: {movie_data.get('title')} ({movie_data.get('objectID')})")
    except Exception as e:
        logger.error(f"Error adding movie to Algolia: {e}", exc_info=True)
        raise # Re-raise the exception


# --- FIX: Changed type hint from SearchClient.Index to object ---
async def vote_for_movie(movies_index: object, votes_index: object, movie_id: str, user_id: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """Vote for a movie in Algolia."""
    try:
        user_token = generate_user_token(user_id)

        # Check if user already voted for this movie using the votes index - Use passed index
        search_result = votes_index.search("", {
            "filters": f"userToken:{user_token} AND movieId:{movie_id}"
        })

        if search_result["nbHits"] > 0:
            logger.info(f"User {user_id} ({user_token[:8]}...) already voted for movie {movie_id}.")
            existing_movie = await get_movie_by_id(movies_index, movie_id) # Use helper, pass index
            return False, existing_movie if existing_movie else "Already voted"


        # Record the vote in the votes index - Use passed index
        vote_obj = {
            "objectID": f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}",
            "userToken": user_token,
            "movieId": movie_id,
            "timestamp": int(time.time())
        }
        votes_index.add_object(vote_obj)
        logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

        # Increment the movie's vote count in the movies index - Use passed index
        update_result = movies_index.partial_update_object({
            "objectID": movie_id,
            "votes": {
                "_operation": "Increment",
                "value": 1
            },
        })
        # Note: taskID in older client
        logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_result['taskID']}")

        # Wait for the update task - Use passed index
        try:
            movies_index.wait_task(update_result['taskID'])
            logger.info(f"Algolia task {update_result['taskID']} completed.")
        except Exception as e:
             logger.warning(f"Failed to wait for Algolia task {update_result['taskID']}: {e}. Fetching potentially stale movie data.", exc_info=True)


        # Fetch the updated movie object - Use helper, pass index
        updated_movie = await get_movie_by_id(movies_index, movie_id)
        if updated_movie:
             logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
             return True, updated_movie
        else:
             logger.error(f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting. Attempting fallback.", exc_info=True)
             # Fallback: Get latest known data and increment votes locally
             try:
                  movie_before_vote = await get_movie_by_id(movies_index, movie_id) # Try fetching again, pass index
                  fallback_votes = movie_before_vote.get('votes', 0) + 1 if movie_before_vote else 'Unknown'
                  fallback_title = movie_before_vote.get('title', 'Unknown Movie')
                  fallback_image = movie_before_vote.get('image')
                  logger.warning(f"Returning fallback info for movie {movie_id} vote confirmation.")
                  return True, {"objectID": movie_id, "votes": fallback_votes, 'title': fallback_title, 'image': fallback_image}
             except Exception:
                  logger.error(f"Failed to fetch movie {movie_id} even with fallback.", exc_info=True)
                  return True, {"objectID": movie_id, "votes": 'Unknown', 'title': 'Unknown Movie', 'image': None}


    except Exception as e:
        logger.error(f"FATAL error voting for movie {movie_id} by user {user_id}: {e}", exc_info=True)
        return False, str(e)


# --- FIX: Changed type hint from SearchClient.Index to object ---
async def get_movie_by_id(movies_index: object, movie_id: str) -> Optional[Dict[str, Any]]:
    """Get a movie by its ID from Algolia movies index."""
    try:
        return movies_index.get_object(movie_id) # Use passed index
    except Exception as e:
        logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}", exc_info=True)
        return None

# --- FIX: Changed type hint from SearchClient.Index to object ---
async def find_movie_by_title(movies_index: object, title: str) -> Optional[Dict[str, Any]]:
    """
    Find a movie by title in Algolia movies index using search.
    Prioritizes strong matches. Used for commands like /info, /related,
    and add pre-check where a single reference movie is needed.
    """
    if not title: return None
    try:
        search_result = movies_index.search(title, { # Use passed index
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
             "attributesToHighlight": ["title", "originalTitle"],
             "typoTolerance": "strict"
        })

        if search_result["nbHits"] == 0:
            return None

        for hit in search_result["hits"]:
             title_highlight = hit.get("_highlightResult", {}).get("title", {})
             original_title_highlight = hit.get("_highlightResult", {}).get("originalTitle", {})

             if title_highlight.get('matchLevel') == 'full' or original_title_highlight.get('matchLevel') == 'full':
                  logger.info(f"Found strong title match for '{title}': {hit['title']} ({hit['objectID']})")
                  return hit

             if hit.get("title", "").lower() == title.lower() or hit.get("originalTitle", "").lower() == title.lower():
                  logger.info(f"Found exact string match for '{title}': {hit['title']} ({hit['objectID']})")
                  return hit

        logger.info(f"No strong/exact title match for '{title}', returning top relevant hit: {search_result['hits'][0].get('title')} ({search_result['hits'][0].get('objectID')})")
        return search_result["hits"][0]

    except Exception as e:
        logger.error(f"Error finding movie by title '{title}' in Algolia: {e}", exc_info=True)
        return None

# --- FIX: Changed type hint from SearchClient.Index to object ---
def search_movies_for_vote(movies_index: object, title: str) -> Dict[str, Any]:
    """
    Searches for movies by title for the voting command.
    Returns search results (up to ~5 hits) allowing for ambiguity.
    """
    if not title: return {"hits": [], "nbHits": 0}
    try:
        search_result = movies_index.search(title, { # Use passed index
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "year", "votes", "image"
            ],
             "typoTolerance": True
        })

        logger.info(f"Vote search for '{title}' found {search_result['nbHits']} hits.")
        return search_result

    except Exception as e:
        logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
        return {"hits": [], "nbHits": 0}

# --- FIX: Changed type hint from SearchClient.Index to object ---
async def get_top_movies(movies_index: object, count: int = 5) -> List[Dict[str, Any]]:
    """Get the top voted movies from Algolia movies index."""
    try:
        search_result = movies_index.search("", { # Use passed index
            "filters": "votes > 0",
            "hitsPerPage": count,
            "attributesToRetrieve": [
                "objectID", "title", "year", "director",
                "actors", "genre", "image", "votes", "plot", "rating"
            ],
            # Rely on customRanking including "desc(votes)"
        })

        top_movies = sorted(search_result["hits"], key=lambda m: m.get("votes", 0), reverse=True)

        return top_movies

    except Exception as e:
        logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
        return []

# --- FIX: Changed type hint from SearchClient.Index to object ---
async def get_all_movies(movies_index: object) -> List[Dict[str, Any]]:
    """Get all movies from Algolia movies index."""
    try:
        all_movies = []
        for hit in movies_index.browse_objects({'hitsPerPage': 1000}): # Use passed index
             all_movies.append(hit)

        logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse.")
        all_movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

        return all_movies

    except Exception as e:
        logger.error(f"Error getting all movies from Algolia: {e}", exc_info=True)
        return []

```

**4. `utils/ui_modals.py`**

```python
import discord
from discord.ui import Modal, TextInput
import time
import datetime
import logging # Import logging
from typing import List, Dict, Any, Optional # Import necessary types

# Import necessary Algolia interaction functions - Corrected import path
from .algolia import generate_user_token, _check_movie_exists, add_movie_to_algolia


logger = logging.getLogger("paradiso_bot") # Use the same logger


class MovieAddModal(Modal, title="Add Movie Details"):
    """Modal for structured movie input via slash command."""
    def __init__(self, bot_instance, movie_title: str = ""):
        super().__init__()
        self.bot_instance = bot_instance # Store bot instance to access Algolia indices etc.

        self.title_input = TextInput( label="Movie Title", placeholder="e.g., The Matrix", default=movie_title, required=True, max_length=200 )
        self.add_item(self.title_input)

        self.year_input = TextInput( label="Release Year (YYYY)", placeholder="e.g., 1999", required=True, max_length=4, min_length=4, )
        self.add_item(self.year_input)

        self.director_input = TextInput( label="Director", placeholder="e.g., Lana Wachowski, Lilly Wachowski", required=False, max_length=200 )
        self.add_item(self.director_input)

        self.actors_input = TextInput( label="Main Actors (comma-separated)", placeholder="e.g., Keanu Reeves, Laurence Fishburne...", required=False, style=discord.TextStyle.paragraph )
        self.add_item(self.actors_input)

        self.genre_input = TextInput( label="Genres (comma-separated)", placeholder="e.g., Sci-Fi, Action", required=False, style=discord.TextStyle.paragraph )
        self.add_item(self.genre_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handles the modal submission."""
        await interaction.response.defer(thinking=True, ephemeral=False) # Defer response

        title = self.title_input.value.strip()
        year_str = self.year_input.value.strip()
        director = self.director_input.value.strip()
        actors_str = self.actors_input.value.strip()
        genre_str = self.genre_input.value.strip()

        try:
            year = int(year_str)
            if not 1850 <= year <= datetime.datetime.now().year + 5:
                 await interaction.followup.send("❌ Invalid year provided. Please enter a valid 4-digit year.")
                 return
        except ValueError:
            await interaction.followup.send("❌ Invalid year format. Please enter a 4-digit number.")
            return

        actors = [actor.strip() for actor in actors_str.split(',') if actor.strip()] if actors_str else []
        genre = [g.strip() for g in genre_str.split(',') if g.strip()] if genre_str else []

        movie_data = {
            "objectID": f"manual_{int(time.time())}",
            "title": title,
            "originalTitle": title,
            "year": year,
            "director": director or "Unknown",
            "actors": actors,
            "genre": genre,
            "plot": f"Added manually by {interaction.user.display_name}.",
            "image": None, # Use 'image'
            "rating": None, # Use 'rating'
            "imdbID": None,
            "tmdbID": None,
            "source": "manual",
            "votes": 0,
            "addedDate": int(time.time()),
            "addedBy": generate_user_token(str(interaction.user.id)), # Use helper
            "voted": False
        }

        try:
            # Check if movie already exists - Pass movies_index from bot instance to helper
            existing_movie = await _check_movie_exists(self.bot_instance.movies_index, movie_data['title'])
            if existing_movie:
                 await interaction.followup.send(
                    f"❌ A movie with a similar title ('{existing_movie['title']}') is already in the voting queue.")
                 return

            # Add movie to Algolia - Pass movies_index from bot instance to helper
            add_movie_to_algolia(self.bot_instance.movies_index, movie_data)
            logger.info(f"Added movie via modal: {movie_data.get('title')} ({movie_data.get('objectID')})")

            # Create confirmation embed
            embed = discord.Embed(
                title=f"🎬 Added: {movie_data['title']} ({movie_data['year'] if movie_data['year'] is not None else 'N/A'})",
                description=movie_data.get("plot", "No plot available."),
                color=0x00ff00
            )
            if movie_data.get("director") and movie_data["director"] != "Unknown": embed.add_field(name="Director", value=movie_data["director"], inline=True)
            if movie_data.get("actors"): embed.add_field(name="Starring", value=", ".join(movie_data["actors"][:5]), inline=False)
            if movie_data.get("genre"): embed.add_field(name="Genre", value=", ".join(movie_data["genre"]), inline=True)
            if movie_data.get("image"): embed.set_thumbnail(url=movie_data["image"])
            embed.set_footer(text=f"Added by {interaction.user.display_name}")

            await interaction.followup.send("✅ Movie added to the voting queue!", embed=embed)

        except Exception as e:
            logger.error(f"Error adding movie via modal: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while adding the movie: {str(e)}")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handles errors during modal interaction."""
        logger.error(f"Error in MovieAddModal for user {interaction.user.id}: {error}", exc_info=True)
        try:
            await interaction.followup.send(f"❌ An unexpected error occurred with the form. Please try again later. Error: {error}", ephemeral=True)
        except Exception as e:
             logger.error(f"Failed to send error message to user {interaction.user.id} after modal error: {e}", exc_info=True)
```

**5. `utils/ui_views.py`**

```python
import discord
from discord.ui import Button, View
import time
import datetime
import logging # Import logging
from typing import List, Dict, Any, Optional, Union, Tuple

# Import necessary Algolia interaction functions - Corrected import path
from .algolia import vote_for_movie, get_all_movies # Need vote_for_movie and get_all_movies here


logger = logging.getLogger("paradiso_bot") # Use the same logger

# --- View for Vote Selection (Buttons) ---
class VoteSelectionView(View):
    def __init__(self, bot_instance, user_id: int, choices: List[Dict[str, Any]], timeout=300):
        super().__init__(timeout=timeout)
        self.bot_instance = bot_instance # Store bot instance to access Algolia indices etc.
        self.user_id = user_id
        self.choices = choices # List of movie objects to choose from

        # Add buttons for each choice (up to 5)
        for i in range(len(choices)):
            # Create a button with label 1-5
            button = Button(label=str(i + 1), style=discord.ButtonStyle.primary, custom_id=f"vote_select_{i}")
            self.add_item(button)

        # Add a cancel button
        cancel_button = Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="vote_select_cancel")
        self.add_item(cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the user who invoked the command can use these buttons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This selection is not for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        """Called when the view times out."""
        # Disable buttons on timeout
        for item in self.children:
            item.disabled = True
        # Remove the message from vote_messages state
        if self.message:
             self.bot_instance.vote_messages.pop(self.message.id, None) # Access state via bot instance
             try:
                 await self.message.edit(content="Vote selection timed out.", view=self)
             except Exception:
                  pass
        logger.info(f"Vote selection timed out for user {self.user_id}")

    # Using decorated methods to handle specific button presses
    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, custom_id="vote_select_0")
    async def handle_selection_1(self, interaction: discord.Interaction, button: Button):
        await self._handle_selection(interaction, 0)

    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, custom_id="vote_select_1")
    async def handle_selection_2(self, interaction: discord.Interaction, button: Button):
        await self._handle_selection(interaction, 1)

    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, custom_id="vote_select_2")
    async def handle_selection_3(self, interaction: discord.Interaction, button: Button):
        await self._handle_selection(interaction, 2)

    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, custom_id="vote_select_3")
    async def handle_selection_4(self, interaction: discord.Interaction, button: Button):
        await self._handle_selection(interaction, 3)

    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, custom_id="vote_select_4")
    async def handle_selection_5(self, interaction: discord.Interaction, button: Button):
        await self._handle_selection(interaction, 4)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="vote_select_cancel")
    async def handle_selection_cancel(self, interaction: discord.Interaction, button: Button):
        # Defer response early
        await interaction.response.defer(thinking=True)

        # Remove the message from state immediately
        if self.message:
             self.bot_instance.vote_messages.pop(self.message.id, None)

        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        try:
             await interaction.edit_original_response(view=self)
        except Exception:
             pass

        await interaction.followup.send("Vote selection cancelled.")
        self.stop() # Stop the view


    async def _handle_selection(self, interaction: discord.Interaction, index: int):
        """Common handler for vote selection buttons."""
        await interaction.response.defer(thinking=True) # Defer response

        if self.message:
             self.bot_instance.vote_messages.pop(self.message.id, None)

        for item in self.children:
            item.disabled = True
        try:
             await interaction.edit_original_response(view=self)
        except Exception:
             pass

        try:
            chosen_movie = self.choices[index]

            # Call the vote_for_movie helper, passing Algolia indices from the bot instance
            success, result = await vote_for_movie(
                self.bot_instance.movies_index,
                self.bot_instance.votes_index,
                chosen_movie["objectID"],
                str(self.user_id)
            )

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
                if isinstance(result, str) and result == "Already voted":
                    await interaction.followup.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                else:
                    logger.error(f"Error recording vote during selection: {result}")
                    await interaction.followup.send(f"❌ An error occurred while recording your vote.")

            self.stop()

        except IndexError:
             logger.warning(f"Vote selection index {index} out of bounds for choices len {len(self.choices)} for user {self.user_id}")
             await interaction.followup.send("Invalid selection. Please try the vote command again.", ephemeral=True)
             self.stop()
        except Exception as e:
            logger.error(f"Error processing vote selection button for user {self.user_id}: {e}", exc_info=True)
            await interaction.followup.send("An unexpected error occurred. Please try voting again.")
            self.stop()


# --- View for Movies Pagination (Buttons) ---
class MoviesPaginationView(View):
    def __init__(self, bot_instance, user_id: int, movies: List[Dict[str, Any]], movies_per_page: int = 10, detailed_count: int = 5, timeout=600):
        super().__init__(timeout=timeout)
        self.bot_instance = bot_instance # Store bot instance to access methods like _get_movies_page_embed
        self.user_id = user_id
        self.all_movies = movies # Store the full list of movies
        self.movies_per_page = movies_per_page
        self.detailed_count = detailed_count
        self.current_page = 0
        self.total_pages = (len(movies) + movies_per_page - 1) // movies_per_page

        # Add navigation buttons
        if self.total_pages > 1:
            self.add_item(Button(label="⏪ First", style=discord.ButtonStyle.secondary, custom_id="page_first", disabled=True))
            self.add_item(Button(label="◀️ Previous", style=discord.ButtonStyle.secondary, custom_id="page_prev", disabled=True))
            self.add_item(Button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="page_next"))
            self.add_item(Button(label="⏩ Last", style=discord.ButtonStyle.secondary, custom_id="page_last"))


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the user who invoked the command can use these buttons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This pagination is not for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        """Called when the view times out."""
        for item in self.children:
            item.disabled = True
        if self.message:
             self.bot_instance.movies_pagination_state.pop(self.message.id, None) # Access state via bot instance
             try:
                 await self.message.edit(content="Movie list pagination timed out.", view=self)
             except Exception:
                  pass
        logger.info(f"Movies pagination timed out for user {self.user_id}")

    # Use decorated methods for specific button presses
    @discord.ui.button(label="⏪ First", style=discord.ButtonStyle.secondary, custom_id="page_first")
    async def go_first_page(self, interaction: discord.Interaction, button: Button):
        if self.current_page != 0:
            self.current_page = 0
            await self.render_page(interaction)

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, custom_id="page_prev")
    async def go_previous_page(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page = max(0, self.current_page - 1)
            await self.render_page(interaction)

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="page_next")
    async def go_next_page(self, interaction: discord.Interaction, button: Button):
        if self.current_page < self.total_pages - 1:
            self.current_page = min(self.total_pages - 1, self.current_page + 1)
            await self.render_page(interaction)

    @discord.ui.button(label="⏩ Last", style=discord.ButtonStyle.secondary, custom_id="page_last")
    async def go_last_page(self, interaction: discord.Interaction, button: Button):
        if self.current_page != self.total_pages - 1:
            self.current_page = self.total_pages - 1
            await self.render_page(interaction)

    async def render_page(self, interaction: discord.Interaction):
        """Render the current page and update the message."""
        await interaction.response.defer()

        # Use the helper method from the bot instance to get the embed
        # This requires the bot instance to have a method like _get_movies_page_embed
        # Or, move the embed creation logic into this View class.
        # Let's move the embed creation logic into this class for better encapsulation.
        # We need to re-create the embed based on the current state.

        start_index = self.current_page * self.movies_per_page
        end_index = start_index + self.movies_per_page
        page_movies = self.all_movies[start_index:end_index]

        embed = discord.Embed(
            title=f"🎬 Paradiso Movie Night Voting (Page {self.current_page + 1}/{self.total_pages})",
            description=f"Showing movies {start_index + 1}-{min(end_index, len(self.all_movies))} out of {len(self.all_movies)}:",
            color=0x03a9f4,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        for i, movie in enumerate(page_movies):
            global_index = start_index + i
            title = movie.get("title", "Unknown")
            year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
            votes = movie.get("votes", 0)
            rating = movie.get("rating")

            medal = "🥇" if global_index == 0 else "🥈" if global_index == 1 else "🥉" if global_index == 2 else f"{global_index + 1}."

            if i < self.detailed_count:
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

        embed.set_footer(text=f"Use /vote to vote for a movie! | Page {self.current_page + 1}/{self.total_pages}")

        await self.update_buttons() # Update button states
        await interaction.edit_original_response(embed=embed, view=self)

```

**6. `utils/parser.py`** (No changes needed)

```python
import re
from typing import Tuple, Any # Import Any for _is_float

def _is_float(value: Any) -> bool:
    """Helper to check if a value can be converted to a float."""
    if value is None:
         return False
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def parse_algolia_filters(query_string: str) -> Tuple[str, str]:
     """
     Parses a query string to extract key:value filters for Algolia.
     Returns the remaining query text and the constructed filters string.

     Syntax supported:
     key:value (exact match)
     key:"multi word value" (exact match with spaces)
     key:value1 TO value2 (range)
     key:>value, key:<value, key>=value (numerical range)

     Supported keys (maps to Algolia attributes):
     year -> year (numeric)
     director -> director (string/facet)
     actor -> actors (list of strings/facet)
     genre -> genre (list of strings/facet)
     votes -> votes (numeric)
     rating -> rating (numeric)
     """
     parts = []
     filters = []

     tokens = []
     in_quotes = False
     current_token = []
     for char in query_string.strip():
          if char == '"':
               if current_token:
                    tokens.append("".join(current_token))
               current_token = []
               in_quotes = not in_quotes
          elif char.isspace() and not in_quotes:
               if current_token:
                    tokens.append("".join(current_token))
               current_token = []
          else:
               current_token.append(char)
     if current_token:
          tokens.append("".join(current_token))


     algolia_attribute_map = {
         "year": "year",
         "director": "director",
         "actor": "actors",
         "genre": "genre",
         "votes": "votes",
         "rating": "rating"
     }

     for token in tokens:
         if ':' in token and token != ':':
             try:
                 key, value_part = token.split(':', 1)
                 mapped_key = algolia_attribute_map.get(key.lower())

                 if mapped_key:
                     value_part = value_part.strip()

                     if mapped_key in ["year", "votes", "rating"]:
                          num_match = re.match(r'([<>]=?|=)\s*(\d+(\.\d+)?)$', value_part)
                          if num_match:
                               operator = num_match.group(1)
                               number = num_match.group(2)
                               filters.append(f'{mapped_key}{operator}{number}')
                               continue

                          range_match = re.match(r'(\d+(\.\d+)?)\s+TO\s+(\d+(\.\d+)?)$', value_part, re.IGNORECASE)
                          if range_match:
                               val1 = range_match.group(1)
                               val2 = range_match.group(3)
                               filters.append(f'{mapped_key}:{val1} TO {val2}')
                               continue


                     if value_part.startswith('"') and value_part.endswith('"'):
                         value = value_part
                     else:
                         value = f'"{value_part}"'

                     filters.append(f'{mapped_key}:{value}')
                     continue

             except ValueError:
                 pass


         parts.append(token)

     main_query = " ".join(parts).strip()
     filter_string = " AND ".join(filters).strip()

     return main_query, filter_string

```

**7. `utils/embed_formatters.py`** (No changes needed)

```python
import discord
import datetime
from typing import List, Dict, Any, Optional, Union


async def send_search_results_embed(target: Union[discord.TextChannel, discord.DMChannel, discord.Webhook], query: str, hits: List[Dict[str, Any]], nb_hits: int):
     """Helper to send search results embed to a channel or followup webhook."""
     if nb_hits == 0:
         await target.send(f"No movies found matching '{query}'.")
         return

     embed = discord.Embed(
         title=f"🔍 Search Results for '{query}'",
         description=f"Found {nb_hits} results:",
         color=0x03a9f4
     )

     for i, movie in enumerate(hits):
         title_display = movie.get("_highlightResult", {}).get("title", {}).get("value", movie.get("title", "Unknown"))
         year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
         votes = movie.get("votes", 0)
         rating = movie.get("rating")

         movie_details = []
         if movie.get("director") and movie["director"] != "Unknown":
             director_display = movie.get("_highlightResult", {}).get("director", {}).get("value", movie["director"])
             movie_details.append(f"**Director**: {director_display}")

         if movie.get("actors") and len(movie["actors"]) > 0: # Check if list exists and has items
             actors_display = movie.get("_highlightResult", {}).get("actors", [])
             if actors_display: actors_str = ", ".join([h['value'] for h in actors_display])
             else: actors_str = ", ".join(movie["actors"][:5])
             movie_details.append(f"**Starring**: {actors_str}")

         if movie.get("genre") and len(movie["genre"]) > 0: # Check if list exists and has items
             genre_display = movie.get("_highlightResult", {}).get("genre", [])
             if genre_display: genre_str = ", ".join([h['value'] for h in genre_display])
             else: genre_str = ", ".join(movie["genre"])
             movie_details.append(f"**Genre**: {genre_str}")

         if rating is not None:
              movie_details.append(f"**Rating**: ⭐ {rating}/10")

         movie_details.append(f"**Votes**: {votes}")

         plot_display = movie.get("_snippetResult", {}).get("plot", {}).get("value", movie.get("plot", "No description available."))
         if len(plot_display) > 200:
              plot_display = plot_display[:200] + "..."

         embed.add_field(
             name=f"{i + 1}. {title_display}{year}",
             value="\n".join(movie_details) + f"\n**Plot**: {plot_display}",
             inline=False
         )

     embed.set_footer(text="Use /vote [title] to vote for a movie")

     if hits and hits[0].get("image"):
         embed.set_thumbnail(url=hits[0]["image"])

     await target.send(embed=embed)


async def send_detailed_movie_embed(target: Union[discord.TextChannel, discord.DMChannel, discord.Webhook], movie: Dict[str, Any]):
    """Helper to send a detailed embed for a single movie."""
    title = movie.get('title', 'Unknown Movie')
    year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
    rating = movie.get('rating')

    embed = discord.Embed(
        title=f"🎬 {title}{year}",
        color=0x1a73e8
    )

    if movie.get('originalTitle') and movie['originalTitle'] != title:
         embed.add_field(name="Original Title", value=movie['originalTitle'], inline=False)

    embed.add_field(name="Votes", value=movie.get('votes', 0), inline=True)
    if rating is not None:
         embed.add_field(name="Rating", value=f"⭐ {rating}/10", inline=True)
    else:
         embed.add_field(name="Rating", value="N/A", inline=True)

    if movie.get("director") and movie["director"] != "Unknown":
         embed.add_field(name="Director", value=movie["director"], inline=True)

    if movie.get("actors") and len(movie["actors"]) > 0:
         embed.add_field(name="Starring", value=", ".join(movie["actors"]), inline=False)

    if movie.get("genre") and len(movie["genre"]) > 0:
         embed.add_field(name="Genre", value=", ".join(movie["genre"]), inline=False)

    plot = movie.get("plot", "No plot available.")
    if plot:
         embed.add_field(name="Plot", value=plot, inline=False)

    if movie.get("image"):
         embed.set_image(url=movie["image"])

    embed.set_footer(text=f"Added: {datetime.datetime.fromtimestamp(movie.get('addedDate', 0), datetime.timezone.utc).strftime('%Y-%m-%d')} | Source: {movie.get('source', 'N/A')}")

    await target.send(embed=embed)
```

**8. `tests/test_unit.py`** (Fixed import path and mock object)

```python
import pytest
from unittest.mock import MagicMock, AsyncMock # Import AsyncMock
import hashlib # Import hashlib for generate_user_token test

# Import functions from your utils modules - Corrected import path
from utils.parser import parse_algolia_filters, _is_float
from utils.algolia import generate_user_token, find_movie_by_title, _check_movie_exists
# Removed the import of SearchClient as it's not needed for mocking generic objects


# --- Tests for utils.parser ---

def test_parse_algolia_filters_no_filters():
    query = "The Matrix movie"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "The Matrix movie"
    assert filters == ""

def test_parse_algolia_filters_single_filter():
    query = "matrix year:1999"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "matrix"
    assert filters == 'year:"1999"'

def test_parse_algolia_filters_multiple_filters():
    query = "action genre:Comedy director:Nolan year:>2000"
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "action"
    # Filters order might vary, check presence
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 3
    assert 'genre:"Comedy"' in filter_list
    assert 'director:"Nolan"' in filter_list
    assert 'year>2000' in filter_list

def test_parse_algolia_filters_quoted_value():
    query = 'search actor:"Tom Hanks"'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "search"
    assert filters == 'actors:"Tom Hanks"' # Assumes 'actor' maps to 'actors'

def test_parse_algolia_filters_quoted_value_in_middle():
    query = 'action movie genre:"Sci-Fi" director:Spielberg'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "action movie"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 2
    assert 'genre:"Sci-Fi"' in filter_list
    assert 'director:"Spielberg"' in filter_list

def test_parse_algolia_filters_numeric_range():
    query = 'movies year:1990 TO 2000 votes:>10'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "movies"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 2
    assert 'year:1990 TO 2000' in filter_list
    assert 'votes>10' in filter_list

def test_parse_algolia_filters_complex_query():
    query = 'best sci-fi actor:"Sigourney Weaver" year:<2010 genre:Horror rating:>=8.5'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "best sci-fi"
    filter_list = filters.split(" AND ")
    assert len(filter_list) == 4
    assert 'actors:"Sigourney Weaver"' in filter_list
    assert 'year<2010' in filter_list
    assert 'genre:"Horror"' in filter_list
    assert 'rating>=8.5' in filter_list

def test_parse_algolia_filters_unrecognized_key():
    query = 'movie format:DVD'
    main_query, filters = parse_algolia_filters(query)
    assert main_query == "movie format:DVD" # Unrecognized filter is treated as part of query
    assert filters == ""

def test_parse_algolia_filters_empty_string():
    query = ""
    main_query, filters = parse_algolia_filters(query)
    assert main_query == ""
    assert filters == ""

# --- Tests for utils.algolia (Pure functions) ---

def test_generate_user_token():
    user_id_1 = "discord_1234567890"
    user_id_2 = "discord_0987654321"
    token_1a = generate_user_token(user_id_1)
    token_1b = generate_user_token(user_id_1)
    token_2 = generate_user_token(user_id_2)

    assert token_1a == token_1b
    assert token_1a != token_2
    assert len(token_1a) == 64
    assert all(c in '0123456789abcdef' for c in token_1a.lower()) # Ensure case-insensitive check

def test__is_float():
    assert _is_float("123") is True
    assert _is_float("123.45") is True
    assert _is_float("-10") is True
    assert _is_float("0") is True
    assert _is_float("0.0") is True
    assert _is_float(".5") is True
    assert _is_float("-0.75") is True
    assert _is_float("1e-3") is True
    assert _is_float(123) is True
    assert _is_float(123.45) is True
    assert _is_float(0) is True
    assert _is_float(None) is False
    assert _is_float("abc") is False
    assert _is_float("12.3.4") is False
    assert _is_float("123a") is False
    assert _is_float("") is False
    assert _is_float(" ") is False
    assert _is_float([]) is False
    assert _is_float({}) is False

# --- Tests for utils.algolia (Mocked Algolia interactions) ---

# Pytest fixture to create a mock Algolia index object (using MagicMock directly)
@pytest.fixture
def mock_movies_index():
    # Create a MagicMock object to simulate the Algolia index methods used
    index_mock = MagicMock()
    # Mock the 'search' method as an AsyncMock
    index_mock.search = AsyncMock()
    index_mock.get_object = AsyncMock()
    # Add other methods used in algolia.py if needed in future tests (e.g., browse_objects, partial_update_object, wait_task)
    index_mock.browse_objects = MagicMock() # browse_objects is often synchronous iterator in older client
    index_mock.partial_update_object = MagicMock()
    index_mock.wait_task = MagicMock()
    return index_mock

@pytest.mark.asyncio
async def test_find_movie_by_title_found_exact(mock_movies_index):
    mock_hit = {"objectID": "movie_1", "title": "Exact Match Movie"}
    mock_hit_highlighted = {**mock_hit, "_highlightResult": {"title": {"value": "<em>Exact Match Movie</em>", "matchLevel": "full"}}} # Added highlight structure
    mock_movies_index.search.return_value = {
        "hits": [mock_hit_highlighted, {"objectID": "movie_2", "title": "Similar Movie"}],
        "nbHits": 2
    }

    title = "Exact Match Movie"
    found_movie = await find_movie_by_title(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
        title,
        {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
            "attributesToHighlight": ["title", "originalTitle"],
            "typoTolerance": "strict"
        }
    )
    # It should return the hit with full match level
    assert found_movie == mock_hit_highlighted

@pytest.mark.asyncio
async def test_find_movie_by_title_found_inexact_returns_top_hit(mock_movies_index):
    top_hit = {"objectID": "movie_1", "title": "The Matrix Reloaded"}
    second_hit = {"objectID": "movie_2", "title": "The Matrix Revolutions"}
    mock_movies_index.search.return_value = {
        "hits": [top_hit, second_hit],
        "nbHits": 2
    }

    title = "Matrix" # Query that is not an exact title
    found_movie = await find_movie_by_title(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
         title, # Should search with the query
         {
            "hitsPerPage": 5,
            "attributesToRetrieve": [
                "objectID", "title", "originalTitle", "year", "director",
                "actors", "genre", "plot", "image", "votes", "rating",
                "imdbID", "tmdbID"
            ],
            "attributesToHighlight": ["title", "originalTitle"],
            "typoTolerance": "strict"
        }
    )
    # It should return the first hit from the search results if no exact/full match is found
    assert found_movie == top_hit


@pytest.mark.asyncio
async def test_find_movie_by_title_not_found(mock_movies_index):
    mock_movies_index.search.return_value = {"hits": [], "nbHits": 0}
    title = "NonExistent Movie"
    found_movie = await find_movie_by_title(mock_movies_index, title)
    mock_movies_index.search.assert_called_once()
    assert found_movie is None

@pytest.mark.asyncio
async def test__check_movie_exists_found_exact(mock_movies_index):
    mock_hit = {"objectID": "movie_1", "title": "Exact Match Movie"}
    mock_hit_highlighted = {**mock_hit, "_highlightResult": {"title": {"value": "<em>Exact Match Movie</em>", "matchLevel": "full"}}} # Added highlight structure
    mock_movies_index.search.return_value = {
        "hits": [mock_hit_highlighted, {"objectID": "movie_2", "title": "Similar Movie"}],
        "nbHits": 2
    }

    title = "Exact Match Movie"
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once_with(
        title,
        {
            "hitsPerPage": 5,
            "attributesToRetrieve": ["objectID", "title"],
            "attributesToHighlight": ["title"],
            "typoTolerance": "strict"
        }
    )
    # It should return the hit object if a full match level or exact string match is found
    assert exists == mock_hit_highlighted


@pytest.mark.asyncio
async def test__check_movie_exists_not_found(mock_movies_index):
    mock_movies_index.search.return_value = {"hits": [], "nbHits": 0}
    title = "NonExistent Movie"
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once()
    assert exists is None

@pytest.mark.asyncio
async def test__check_movie_exists_found_only_partial_match(mock_movies_index):
    # Simulate a search that finds a hit, but only with a partial match level
    partial_hit = {"objectID": "movie_1", "title": "The Matrix Reloaded", "_highlightResult": {"title": {"value": "The <em>Matrix</em> Reloaded", "matchLevel": "partial"}}}
    mock_movies_index.search.return_value = {
        "hits": [partial_hit],
        "nbHits": 1
    }

    title = "Matrix" # Search term
    exists = await _check_movie_exists(mock_movies_index, title)

    mock_movies_index.search.assert_called_once()
    # It should return None because it only found a partial match and not an exact string match
    assert exists is None


# More tests can be added here following the same pattern for other async functions
# in utils.algolia like search_movies_for_vote, get_top_movies, get_all_movies.
# Testing vote_for_movie is more complex as it involves multiple mocks (movies_index, votes_index)
# and mocking partial_update_object and wait_task.
```

**How to Run the Tests:**

1.  **Verify Directory Structure:** Double-check that your directories and file names match the structure described:
    ```
    your_bot_directory/
    ├── main_bot.py
    ├── .env  # (or .env.bot)
    ├── utils/
    │   ├── algolia.py  # <-- Make sure this is named algolia.py
    │   ├── embed_formatters.py
    │   ├── __init__.py
    │   ├── parser.py
    │   ├── ui_modals.py
    │   └── ui_views.py
    └── tests/
        ├── __init__.py
        └── test_unit.py
    ```
2.  **Install Dependencies:** Make sure you have pytest and pytest-asyncio installed.
    ```bash
    pip install pytest pytest-asyncio
    ```
3.  **Navigate to Bot Directory:** Open your terminal or command prompt and navigate to the root directory of your bot (`your_bot_directory/`).
4.  **Run Pytest:** Execute the `pytest` command. Pytest will automatically discover tests in the `tests` folder.
    ```bash
    pytest
    ```

This should now correctly import your `utils.algolia` module and the type hints should no longer cause an `AttributeError`. The tests will run against the mocked Algolia index.