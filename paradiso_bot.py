#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia Pure v2)

A Discord bot for the Paradiso movie voting system, using Algolia for all data storage,
search, recommendations (via attribute search), and vote handling.

Refinements:
- Uses Discord Modals for /add for structured input.
- Implements interactive selection for /vote if the search is ambiguous.
- Correctly handles list attributes (actors, genre) from Algolia hits.
- Aligns attribute names with the provided Algolia schema screenshot (image, rating).
- Addresses /add command timeout by deferring the initial response.

Requirements:
  - Python 3.9+
  - discord.py>=2.0 (for Modals, Interactions)
  - python-dotenv
  - algoliasearch<4.0.0 (as requested, using sync client)
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
from discord.ui import Modal, TextInput # Added for Modals
from dotenv import load_dotenv
# Note: algoliasearch < 4.0.0 client is typically synchronous.
# We call its methods within async functions.
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
    def __init__(self, bot_instance, interaction: discord.Interaction, movie_title: str = ""):
        super().__init__()
        self.bot_instance = bot_instance
        self.interaction = interaction # Store interaction for followup
        self.movie_title = movie_title # Store the title from the slash command (can be empty)

        # Pre-fill title from command argument if provided, make required
        self.title_input = TextInput(
            label="Movie Title",
            placeholder="e.g., The Matrix",
            default=movie_title, # Pre-fill if title was in the command
            required=True,
            max_length=200
        )
        self.add_item(self.title_input)

        # Year (required, number input placeholder)
        self.year_input = TextInput(
            label="Release Year (YYYY)",
            placeholder="e.g., 1999",
            required=True,
            max_length=4,
            min_length=4,
            # Ideally, this would enforce numbers, but TextInput is text. Validate on submit.
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
        # Use the interaction object stored during init if responding later
        # If responding directly to the modal submit, use the provided interaction
        response_interaction = interaction

        await response_interaction.response.defer(thinking=True, ephemeral=False) # Defer the response for processing

        title = self.title_input.value.strip()
        year_str = self.year_input.value.strip()
        director = self.director_input.value.strip()
        actors_str = self.actors_input.value.strip()
        genre_str = self.genre_input.value.strip()

        # Basic validation for year
        try:
            year = int(year_str)
            if not 1850 <= year <= datetime.datetime.now().year + 5: # Basic range check
                 await response_interaction.followup.send("❌ Invalid year provided. Please enter a valid 4-digit year.")
                 return
        except ValueError:
            await response_interaction.followup.send("❌ Invalid year format. Please enter a 4-digit number.")
            return

        # Process actors and genre strings
        actors = [actor.strip() for actor in actors_str.split(',') if actor.strip()] if actors_str else []
        genre = [g.strip() for g in genre_str.split(',') if g.strip()] if genre_str else []

        # Construct movie data dictionary - Using schema from screenshot
        movie_data = {
            "objectID": f"manual_{int(time.time())}", # Unique ID for manual entries
            "title": title,
            "originalTitle": title, # Assume original title is the same unless manually specified
            "year": year,
            "director": director or "Unknown", # Store "Unknown" if empty
            "actors": actors,
            "genre": genre,
            "plot": f"Added manually by {response_interaction.user.display_name}.", # Minimal plot for manual entries
            "image": None, # Use 'image' as per schema (was 'poster')
            "rating": None, # Use 'rating' as per schema (was 'imdbRating')
            "imdbID": None,
            "tmdbID": None, # Algolia does not provide these for manual adds
            "source": "manual", # Indicate source
            "votes": 0, # Starts at 0
            "addedDate": int(time.time()),
            "addedBy": self.bot_instance.generate_user_token(str(response_interaction.user.id)),
             "voted": False # Attribute for faceting, initially False
        }

        try:
            # Check if movie already exists (basic title check)
            existing_movie = await self.bot_instance._check_movie_exists(title)
            if existing_movie: # _check_movie_exists returns None or the hit
                 await response_interaction.followup.send(
                    f"❌ A movie with a similar title ('{existing_movie['title']}') is already in the voting queue.")
                 return

            # Add movie to Algolia
            # Use add_object, Algolia will handle add/update based on objectID
            self.bot_instance.movies_index.save_object(movie_data)
            logger.info(f"Added movie via modal: {movie_data.get('title')} ({movie_data.get('objectID')})")

            # Create embed for confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_data['title']} ({movie_data['year'] if movie_data['year'] else 'N/A'})",
                description=movie_data.get("plot", "No plot available."),
                color=0x00ff00
            )

            if movie_data.get("director") and movie_data["director"] != "Unknown":
                embed.add_field(name="Director", value=movie_data["director"], inline=True)

            if movie_data.get("actors"):
                embed.add_field(name="Starring", value=", ".join(movie_data["actors"][:5]), inline=False)

            if movie_data.get("genre"):
                embed.add_field(name="Genre", value=", ".join(movie_data["genre"]), inline=True)

            if movie_data.get("image"): # Use 'image'
                 embed.set_thumbnail(url=movie_data["image"])

            embed.set_footer(text=f"Added by {response_interaction.user.display_name}")

            await response_interaction.followup.send("✅ Movie added to the voting queue!", embed=embed)

        except Exception as e:
            logger.error(f"Error adding movie via modal: {e}")
            await response_interaction.followup.send(f"❌ An error occurred while adding the movie: {str(e)}")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handles errors during modal interaction."""
        logger.error(f"Error in MovieAddModal for user {interaction.user.id}: {error}", exc_info=True)
        try:
             # Try to send a message back to the user about the error
            await interaction.followup.send(f"❌ An unexpected error occurred with the modal. Please try again later. Error: {error}", ephemeral=True)
        except Exception as e:
             logger.error(f"Failed to send error message to user {interaction.user.id} after modal error: {e}")


class ParadisoBot:
    """Paradiso Discord bot for movie voting (Algolia Pure v2)."""

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
        self.add_movie_flows = {} # Dict to store user_id: flow_state (for DM text flow)

        # Track users in vote selection flow (for ambiguous vote titles)
        self.pending_votes = {} # Dict to store user_id: {'interaction': interaction, 'choices': [movie_obj, ...], 'timestamp': time.time()}

        # Initialize Discord client
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # Initialize Algolia client (synchronous client based on algoliasearch<4.0.0)
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
                         await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                         logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        logger.error(f"Error checking last message in #paradiso: {e}")
                        await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                        logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")

            # Sync commands
            try:
                # Sync globally for simplicity, or specify guild IDs for faster updates during development
                await self.tree.sync()
                # await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID)) # Sync for a specific guild
                logger.info("Commands synced successfully")
            except Exception as e:
                logger.error(f"Error syncing commands: {e}")


        @self.client.event
        async def on_message(message):
            """Handle incoming messages for text commands, add movie flow, or vote selection."""
            # Don't respond to our own messages or slash commands
            if message.author == self.client.user or message.is_command():
                return

            # Log message
            logger.info(f"Message received from {message.author} ({message.author.id}) in {message.channel}: {message.content}")

            user_id = message.author.id

            # --- Handle Vote Selection Response ---
            if user_id in self.pending_votes:
                 flow_state = self.pending_votes[user_id]
                 # Check if the message is in the expected channel (typically DM)
                 if message.channel.id == flow_state['channel'].id:
                      await self._handle_vote_selection_response(message, flow_state)
                      return # Stop processing if handled as vote selection

            # --- Handle Add Movie Flow (Text-based DM flow) ---
            if user_id in self.add_movie_flows:
                # Ensure the message is in the correct DM channel for the flow
                if message.channel.id == self.add_movie_flows[user_id]['channel'].id:
                    await self._handle_add_movie_flow(message)
                    return # Stop processing if handled as add movie flow

            # --- Handle Manual Commands (DMs and mentions) ---
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
                        # Handle text-based vote command, potentially starting selection flow
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
                    count = max(1, min(10, count)) # Limit count to 1-10 for text commands
                    await self._handle_top_command(message.channel, count)

                # Default response for unhandled messages in DMs or mentions
                elif isinstance(message.channel, discord.DMChannel) or (self.client.user.mentioned_in(message) and not content):
                     await self._send_help_message(message.channel)


    def _register_commands(self):
        """Register Discord slash commands."""
        # Use the Modal for the /add command
        @self.tree.command(name="add", description="Add a movie to the voting queue with structured input")
        @app_commands.describe(title="Optional: Provide a title to pre-fill the modal")
        async def cmd_add_slash(interaction: discord.Interaction, title: Optional[str] = None):
            """Slash command to add a movie, prompting with a modal."""
            # Defer the interaction immediately to avoid timeout
            await interaction.response.defer(thinking=True, ephemeral=True) # Ephemeral thinking for a cleaner look

            # Send the modal *after* deferring
            # The modal will handle the follow-up response on submit
            try:
                await interaction.followup.send_modal(MovieAddModal(self, interaction, movie_title=title or ""))
                # No need to send a separate followup message here, the modal handles the interaction.
            except Exception as e:
                 logger.error(f"Error sending modal for /add: {e}")
                 # If modal sending fails for some reason, send an error followup
                 await interaction.followup.send("❌ Failed to open the add movie form. Please try again.", ephemeral=True)


        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="related", description="Find related movies based on a movie in the database")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="help", description="Show help for Paradiso commands")(self.cmd_help)


    def run(self):
        """Run the Discord bot."""
        try:
            logger.info("Starting Paradiso bot...")
            # keep_alive() # Platform-specific, uncomment if needed
            self.client.run(self.discord_token)
        except discord.errors.LoginFailure:
            logger.error("Invalid Discord token. Please check your DISCORD_TOKEN environment variable.")
        except Exception as e:
            logger.error(f"Error running the bot: {e}")

    # --- Manual Command Handlers (for DMs and mentions) ---
    # _send_help_message, _handle_search_command, _start_add_movie_flow,
    # _handle_add_movie_flow, _add_movie_from_flow, _handle_vote_command,
    # _handle_movies_command, _handle_top_command - (See full script provided previously, minor adjustments for schema)

    # --- Vote Selection Flow Handlers ---
    async def _handle_vote_selection_response(self, message: discord.Message, flow_state: Dict[str, Any]):
        """Handles a user's numerical response during the vote selection flow."""
        user_id = message.author.id
        response = message.content.strip()

        # Clean up old flows (e.g., if bot restarted or user took too long)
        if time.time() - flow_state.get('timestamp', 0) > 300: # 5 minutes timeout
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

                # Now proceed with the actual voting logic
                success, result = await self.vote_for_movie(chosen_movie["objectID"], str(user_id))

                if success:
                    updated_movie = result # result is the updated movie object
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"): # Use 'image'
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {message.author.display_name}")
                    await message.channel.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted":
                        await message.channel.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                    else:
                        logger.error(f"Error recording vote during selection: {result}")
                        await message.channel.send(f"❌ An error occurred while recording your vote.")

                # Clean up the pending vote state
                del self.pending_votes[user_id]

            else:
                await message.channel.send(f"Invalid selection. Please enter a number between 1 and {len(choices)}, or 0 to cancel.")

        except ValueError:
            await message.channel.send(f"Invalid input. Please enter a number corresponding to your choice, or 0 to cancel.")
        except Exception as e:
            logger.error(f"Error during vote selection response handling for user {user_id}: {e}")
            await message.channel.send("An unexpected error occurred. Please try voting again.")
            if user_id in self.pending_votes:
                 del self.pending_votes[user_id]


    # --- Slash Command Handlers ---

    # cmd_add_slash handled via Modal class now

    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        """Vote for a movie in the queue, handles ambiguity."""
        await interaction.response.defer(thinking=True)

        user_id = interaction.user.id

        # Clear any old pending vote state for this user
        if user_id in self.pending_votes:
            del self.pending_votes[user_id]

        try:
            # Find potential movies
            search_results = await self.search_movies_for_vote(title) # New helper for voting search

            if search_results["nbHits"] == 0:
                await interaction.followup.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `/movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                # Found exactly one movie, vote directly
                movie_to_vote = hits[0]
                await interaction.followup.send(f"Found '{movie_to_vote['title']}'. Recording your vote...")
                success, result = await self.vote_for_movie(movie_to_vote["objectID"], str(user_id))

                if success:
                    updated_movie = result # result is the updated movie object
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"): # Use 'image'
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {interaction.user.display_name}")
                    await interaction.followup.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted":
                         await interaction.followup.send(f"❌ You have already voted for '{movie_to_vote['title']}'!")
                    else:
                        logger.error(f"Error recording vote for single match: {result}")
                        await interaction.followup.send(f"❌ An error occurred while recording your vote.")

            else:
                # Multiple matches, start interactive selection flow
                # Limit choices to top 5
                choices = hits[:5]
                user_dm_channel = await interaction.user.create_dm()

                # Store the state
                self.pending_votes[user_id] = {
                    'interaction': interaction, # Store interaction to follow up if needed (less critical for DM flow)
                    'choices': choices,
                    'timestamp': time.time(),
                    'channel': user_dm_channel # Store the DM channel ID
                }

                # Prepare message for DM
                embed = discord.Embed(
                    title=f"Multiple movies found for '{title}'",
                    description="Please reply with the number of the movie you want to vote for (1-5), or type '0' or 'cancel' to cancel.",
                    color=0xffa500 # Orange color for selection
                )

                for i, movie in enumerate(choices):
                     year = f" ({movie.get('year')})" if movie.get('year') else ""
                     votes = movie.get('votes', 0)
                     embed.add_field(
                          name=f"{i+1}. {movie.get('title', 'Unknown')}{year}",
                          value=f"Votes: {votes}",
                          inline=False
                     )

                # Send the selection message to the user's DMs
                await user_dm_channel.send(embed=embed)

                # Inform the user in the original channel to check their DMs
                await interaction.followup.send(f"Found multiple matches for '{title}'. Please check your DMs ({user_dm_channel.mention}) to select the movie you want to vote for.")

        except Exception as e:
            logger.error(f"Error in /vote command for title '{title}': {e}")
            await interaction.followup.send(f"❌ An error occurred while searching for the movie: {str(e)}")


    async def cmd_movies(self, interaction: discord.Interaction):
        """List all movies in the voting queue."""
        await interaction.response.defer()

        try:
            movies = await self.get_all_movies() # get_all_movies already sorts by votes desc

            if not movies:
                await interaction.followup.send("No movies have been added yet! Use `/add` to add one.")
                return

            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting",
                description=f"Here are the movies currently in the queue ({len(movies)} total):",
                color=0x03a9f4,
                timestamp=datetime.datetime.now(datetime.timezone.utc) # Use timezone-aware datetime
            )

            # Add each movie to the embed
            # Limit to top 20 for slash commands for better Discord embed rendering
            for i, movie in enumerate(movies[:20]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)
                rating = movie.get("rating") # Use 'rating' from schema

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i + 1}."

                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {year.strip() or 'N/A'}",
                     f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                ]

                # Truncate plot for cleaner display
                plot = movie.get("plot", "No description available.")
                if len(plot) > 150:
                    plot = plot[:150] + "..."
                movie_details.append(f"**Plot**: {plot}")

                embed.add_field(
                    name=f"{medal} {title}{year}",
                    value="\n".join(movie_details),
                    inline=False
                )

            if len(movies) > 20:
                embed.set_footer(text=f"Showing top 20 out of {len(movies)} movies. Use /search to find more.")
            else:
                 embed.set_footer(text="Use /vote to vote for a movie!")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /movies command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while getting the movies. Please try again.")

    async def cmd_search(self, interaction: discord.Interaction, query: str):
        """Search for movies in the database."""
        await interaction.response.defer()

        try:
            # Search in Algolia leveraging searchableAttributes
            search_results = self.movies_index.search(query, {
                "hitsPerPage": 10,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating" # Use 'image' and 'rating'
                ],
                 # Highlight matched attributes for better search results
                "attributesToHighlight": [
                     "title", "originalTitle", "director", "actors", "year", "plot", "genre" # Also highlight genre
                ],
                 "attributesToSnippet": [
                    "plot:20"
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
                rating = movie.get("rating") # Use 'rating'

                movie_details = []
                if movie.get("director"):
                    director_display = movie.get("_highlightResult", {}).get("director", {}).get("value", movie["director"])
                    movie_details.append(f"**Director**: {director_display}")

                if movie.get("actors") and len(movie["actors"]) > 0: # Check if list exists and has items
                    # Correctly handle actors as a list of strings
                    actors_display = movie.get("_highlightResult", {}).get("actors", []) # Get the list of highlighted actor strings
                    if actors_display:
                         # Join the highlighted strings. Value includes <em> tags.
                         actors_str = ", ".join([h['value'] for h in actors_display])
                    else:
                         # Fallback to joining raw actor names, limit to 5
                         actors_str = ", ".join(movie["actors"][:5])
                    movie_details.append(f"**Starring**: {actors_str}")

                if movie.get("genre") and len(movie["genre"]) > 0: # Check if list exists and has items
                    # Correctly handle genre as a list of strings
                    genre_display = movie.get("_highlightResult", {}).get("genre", []) # Get the list of highlighted genre strings
                    if genre_display:
                         genre_str = ", ".join([h['value'] for h in genre_display])
                    else:
                         genre_str = ", ".join(movie["genre"])
                    movie_details.append(f"**Genre**: {genre_str}")


                if rating is not None: # Check explicitly for None
                     movie_details.append(f"**Rating**: ⭐ {rating}/10")

                movie_details.append(f"**Votes**: {votes}")

                # Use snippet or truncate plot
                plot_display = movie.get("_snippetResult", {}).get("plot", {}).get("value", movie.get("plot", "No description available."))
                # Basic truncation if snippet isn't helpful or too long
                if len(plot_display) > 200:
                     plot_display = plot_display[:200] + "..."


                embed.add_field(
                    name=f"{i + 1}. {title_display}{year}",
                    value="\n".join(movie_details) + f"\n**Plot**: {plot_display}",
                    inline=False
                )

            # Add instructions
            embed.set_footer(text="Use /vote [title] to vote for a movie")

            # Add thumbnail from first result if available (use 'image')
            if search_results["hits"][0].get("image"):
                embed.set_thumbnail(url=search_results["hits"][0]["image"])

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send(f"An error occurred during search: {str(e)}")


    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms (using attribute search as proxy)."""
        await interaction.response.defer()

        try:
            # First try to find the reference movie in Algolia (using the updated find helper)
            reference_movie = await self.find_movie_by_title(query)

            if not reference_movie:
                await interaction.followup.send(f"Could not find a movie matching '{query}' in the database to find related titles.")
                return

            # Build a search query for related movies based on attributes
            # This is a proxy for Algolia Recommend.
            related_query_parts = []
            # Include genres, director (if not "Unknown"), and top actors
            if reference_movie.get("genre"):
                related_query_parts.extend(reference_movie["genre"])
            if reference_movie.get("director") and reference_movie.get("director") != "Unknown":
                related_query_parts.append(reference_movie["director"])
            if reference_movie.get("actors"):
                related_query_parts.extend(reference_movie["actors"][:3])

            # If the reference movie has minimal data, use its title as a fallback query
            if not related_query_parts:
                 related_query = reference_movie.get("title", query)
                 logger.info(f"No rich attributes for related search for '{reference_movie.get('title')}', using title as query.")
            else:
                 related_query = " ".join(related_query_parts)
                 logger.info(f"Generated related query for '{reference_movie.get('title')}': {related_query}")


            # Search for related movies in Algolia
            related_results = self.movies_index.search(related_query, {
                "hitsPerPage": 5, # Show top 5 related
                # Exclude the original movie from related results
                "filters": f"NOT objectID:{reference_movie['objectID']}",
                 "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating" # Use 'image' and 'rating'
                ],
                 # Highlight common attributes for better display
                 "attributesToHighlight": [
                     "director", "actors", "genre"
                 ],
                # Algolia's inherent relevance + customRanking handles sorting.
                # No explicit sortCriteria needed here typically if customRanking is set.
            })

            if related_results["nbHits"] == 0:
                await interaction.followup.send(f"Couldn't find any movies clearly related to '{reference_movie['title']}' based on its attributes.")
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
            ref_rating = reference_movie.get("rating") # Use 'rating'

            embed.add_field(
                name=f"📌 Reference Movie: {reference_movie.get('title', 'Unknown')}{ref_year}",
                value=f"**Genre**: {ref_genre}\n"
                      f"**Director**: {ref_director}\n"
                      f"**Rating**: ⭐ {ref_rating}/10" if ref_rating is not None else "Rating: N/A\n"
                      f"**Votes**: {reference_movie.get('votes', 0)}",
                inline=False
            )

            # Add a separator
            if related_results["hits"]:
                 embed.add_field(name="Related Movies", value="─────────────", inline=False)

            # Add related movies
            for i, movie in enumerate(related_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get("year") else ""
                votes = movie.get("votes", 0)
                rating = movie.get("rating") # Use 'rating'

                # Find common elements for display using highlighted results
                relation_points = []

                genre_highlight = movie.get("_highlightResult", {}).get("genre", [])
                common_genres_display = [h['value'] for h in genre_highlight if h.get('matchedWords')]
                if common_genres_display:
                     relation_points.append(f"**Common Genres**: {', '.join(common_genres_display)}")

                director_highlight = movie.get("_highlightResult", {}).get("director", {})
                if director_highlight.get('matchedWords'):
                     relation_points.append(f"**Same Director**: {director_highlight['value']}")

                actors_highlight = movie.get("_highlightResult", {}).get("actors", [])
                common_actors_display = [h['value'] for h in actors_highlight if h.get('matchedWords')]
                if common_actors_display:
                    relation_points.append(f"**Common Actors**: {', '.join(common_actors_display)}")


                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {movie.get('year', 'N/A')}",
                     f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A",
                ]
                movie_details = [detail for detail in movie_details if detail] # Remove empty strings

                embed.add_field(
                    name=f"{i+1}. {title}{year}",
                    value="\n".join(relation_points + movie_details) or "Details not available.",
                    inline=False
                )

            # Add thumbnail from reference movie if available (use 'image')
            if reference_movie.get("image"):
                embed.set_thumbnail(url=reference_movie["image"])

            embed.set_footer(text="Related search based on movie attributes. Use /vote to vote!")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /related command: {e}", exc_info=True)
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
                rating = movie.get("rating") # Use 'rating'
                if rating is not None:
                    movie_details.append(f"**Rating**: ⭐ {rating}/10")

                if movie.get("director") and movie["director"] != "Unknown":
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
            logger.error(f"Error in /top command: {e}", exc_info=True)
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
                "description": "Vote for a movie in the queue (handles ambiguous titles via DM)"
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
        Uses search and checks for strong matches.
        """
        try:
            # Search with the title, prioritize exact/full matches
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5, # Check top 5
                 "attributesToRetrieve": ["objectID", "title"],
                 "attributesToHighlight": ["title"],
                 "typoTolerance": "strict" # Use strict for existence check
            })

            if search_result["nbHits"] == 0:
                return None

            # Check if any of the top results is a very close match on title
            for hit in search_result["hits"]:
                 title_highlight = hit.get("_highlightResult", {}).get("title", {})
                 # Consider a "full" match level a strong indicator of existence
                 if title_highlight.get('matchLevel') == 'full':
                      logger.info(f"Existing movie check: Found full title match for '{title}': {hit['objectID']}")
                      return hit
                 # Also check for case-insensitive exact string match as fallback
                 if hit.get("title", "").lower() == title.lower():
                      logger.info(f"Existing movie check: Found exact string match for '{title}': {hit['objectID']}")
                      return hit


            # If no strong match in the top hits, assume it doesn't exist for the purpose of adding
            logger.info(f"Existing movie check: No strong title match for '{title}' among top hits.")
            return None

        except Exception as e:
            logger.error(f"Error checking existence for title '{title}' in Algolia: {e}", exc_info=True)
            # Return None in case of error, allowing the add process to potentially continue
            return None


    async def search_movies_for_vote(self, title: str) -> Dict[str, Any]:
        """
        Searches for movies by title for the voting command.
        Returns search results (up to ~5 hits) allowing for ambiguity.
        """
        try:
            # Use Algolia search, allowing some typo tolerance for finding potential matches
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5, # Get up to 5 relevant hits
                "attributesToRetrieve": [
                    "objectID", "title", "year", "votes", "image" # Get necessary info for selection
                ],
                 "typoTolerance": True # Allow fuzzy matching for voting search
            })

            logger.info(f"Vote search for '{title}' found {search_result['nbHits']} hits.")
            return search_result

        except Exception as e:
            logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
            # Return empty results on error
            return {"hits": [], "nbHits": 0}


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
                # Return the existing movie object if possible
                existing_movie = await self.get_movie_by_id(movie_id)
                return False, existing_movie if existing_movie else "Already voted"


            # Record the vote in the votes index
            vote_obj = {
                "objectID": f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}", # Unique ID
                "userToken": user_token,
                "movieId": movie_id,
                "timestamp": int(time.time())
            }
            self.votes_index.add_object(vote_obj)
            logger.info(f"Recorded vote for movie {movie_id} by user {user_id}.")

            # Increment the movie's vote count in the movies index
            update_result = self.movies_index.partial_update_object({
                "objectID": movie_id,
                "votes": {
                    "_operation": "Increment",
                    "value": 1
                },
                 # No need to set 'voted' here for the movie object itself,
                 # as personalization/faceting would handle per-user 'voted' status.
            })
            logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_result['taskID']}")

            # Wait for the update task to complete to ensure we get the latest movie data
            try:
                self.movies_index.wait_task(update_result['taskID'])
                logger.info(f"Algolia task {update_result['taskID']} completed.")
            except Exception as e:
                 logger.warning(f"Failed to wait for Algolia task {update_result['taskID']}: {e}. Fetching potentially stale movie data.")


            # Fetch the updated movie object to return the new vote count and details
            updated_movie = await self.get_movie_by_id(movie_id)
            if updated_movie:
                 logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
                 return True, updated_movie
            else:
                 logger.error(f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting.")
                 # Fallback: Fetch the movie object again without strict waiting, or return minimal info
                 # Returning minimal info is safest if fetching fails consistently.
                 try:
                      movie_before_vote = await self.get_movie_by_id(movie_id) # Try fetching again
                      fallback_votes = movie_before_vote.get('votes', 0) + 1 if movie_before_vote else 'Unknown'
                      fallback_title = movie_before_vote.get('title', 'Unknown Movie')
                      fallback_image = movie_before_vote.get('image')
                      logger.warning(f"Returning fallback info for movie {movie_id} vote confirmation.")
                      return True, {"objectID": movie_id, "votes": fallback_votes, 'title': fallback_title, 'image': fallback_image}
                 except Exception:
                      logger.error(f"Failed to fetch movie {movie_id} even with fallback.", exc_info=True)
                      return True, {"objectID": movie_id, "votes": 'Unknown', 'title': 'Unknown Movie', 'image': None} # Absolute minimal fallback


        except Exception as e:
            logger.error(f"FATAL error voting for movie {movie_id} by user {user_id}: {e}", exc_info=True)
            return False, str(e)


    async def get_movie_by_id(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Get a movie by its ID from Algolia movies index."""
        try:
            # Use get_object to retrieve a specific record by objectID
            return self.movies_index.get_object(movie_id)
        except Exception as e:
            # Algolia client raises exceptions for not found or other errors
            logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}", exc_info=True)
            return None

    async def find_movie_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a movie by title in Algolia movies index using search.
        Prioritizes strong matches but returns the top hit if no exact match.
        Used for commands like /related where a single reference movie is needed.
        """
        try:
            # Use Algolia search with the configured searchableAttributes
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5, # Get a few hits to check for best match
                "attributesToRetrieve": [
                    "objectID", "title", "originalTitle", "year", "director",
                    "actors", "genre", "plot", "image", "votes", "rating", # Use 'image', 'rating'
                    "imdbID", "tmdbID"
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
            logger.info(f"No strong/exact title match for '{title}', returning top relevant hit: {search_result['hits'][0].get('title')} ({search_result['hits'][0].get('objectID')})")
            return search_result["hits"][0]

        except Exception as e:
            logger.error(f"Error finding movie by title '{title}' in Algolia: {e}", exc_info=True)
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
                    "actors", "genre", "image", "votes", "plot", "rating" # Use 'image', 'rating'
                ],
                # Sorting is ideally handled by customRanking in index settings.
                # If not configured or as a fallback:
                # "sortCriteria": ["votes:desc"] # This param might behave differently based on search vs browse
            })

            # Ensure sorting locally based on fetched data in case customRanking isn't perfectly applied or fast enough
            top_movies = sorted(search_result["hits"], key=lambda m: m.get("votes", 0), reverse=True)

            return top_movies

        except Exception as e:
            logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
            return []

    async def get_all_movies(self) -> List[Dict[str, Any]]:
        """Get all movies from Algolia movies index."""
        try:
            # Use browse_objects to retrieve all records. Handle pagination for large indices.
            all_movies = []
            # Setting hitsPerPage higher to reduce iterations, max is 1000 per browse call
            # Browse can take time for many records, consider adding a limit or pagination in Discord if needed.
            for hit in self.movies_index.browse_objects({'hitsPerPage': 1000}):
                 all_movies.append(hit)

            logger.info(f"Fetched {len(all_movies)} movies from Algolia using browse.")
            # Sort manually after fetching for consistency with display commands.
            all_movies.sort(key=lambda m: (m.get("votes", 0), m.get("title", "")), reverse=True)

            return all_movies

        except Exception as e:
            logger.error(f"Error getting all movies from Algolia: {e}", exc_info=True)
            return []

    def generate_user_token(self, user_id: str) -> str:
        """Generate a consistent, non-reversible user token for Algolia from Discord user ID."""
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
    # Use the SECURED BOT API key
    algolia_api_key = os.getenv('ALGOLIA_BOT_SECURED_KEY')
    algolia_movies_index = os.getenv('ALGOLIA_MOVIES_INDEX')
    algolia_votes_index = os.getenv('ALGOLIA_VOTES_INDEX')
    algolia_actors_index = os.getenv('ALGOLIA_ACTORS_INDEX', 'paradiso_actors') # Keep name, default if missing

    # Check if essential environment variables are set
    if not all([discord_token, algolia_app_id, algolia_api_key,
                algolia_movies_index, algolia_votes_index]): # actors_index is not strictly essential for this version
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


    # Create and run the bot
    bot = ParadisoBot(
        discord_token=discord_token,
        algolia_app_id=algolia_app_id,
        algolia_api_key=algolia_api_key,
        algolia_movies_index=algolia_movies_index,
        algolia_votes_index=algolia_votes_index,
        algolia_actors_index=algolia_actors_index
    )

    # on_message listener is set up via decorator inside __init__
    # Slash command handlers are registered via self.tree.command
    # Modal submission is handled by the on_submit method of the Modal class

    bot.run()


if __name__ == "__main__":
    if not os.path.exists(".env") and not os.path.exists(".env.bot") and not os.environ.get('DISCORD_TOKEN'):
         logger.error("No .env or .env.bot file found, and DISCORD_TOKEN is not set in environment variables.")
         logger.error("Please create a .env file or set environment variables.")
         exit(1)

    main()