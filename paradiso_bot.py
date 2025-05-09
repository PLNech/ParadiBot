#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia Pure v3)

A Discord bot for the Paradiso movie voting system, using Algolia for all data storage,
search, recommendations (via attribute search), and vote handling.

Refinements:
- Uses Discord Modals for /add for structured input.
- Implements interactive selection for /vote using buttons if search is ambiguous.
- Implements pagination and varied display for /movies using buttons.
- Adds /info command for detailed movie view.
- Correctly handles list attributes (actors, genre) and uses schema names (image, rating).
- Adds support for parsed filter syntax in search commands.
- Addresses /add command timeout by deferring.
- Keeps text-based DM flow for 'add' mention/DM command, adding search first.

Requirements:
  - Python 3.9+
  - discord.py>=2.0 (for Modals, Interactions, Buttons)
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
from discord.ui import Modal, TextInput, Button, View # Added Button, View
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
    def __init__(self, bot_instance, movie_title: str = ""):
        super().__init__()
        self.bot_instance = bot_instance

        # Pre-fill title from command argument if provided, make required
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
            label="Release Year (YYYY)",
            placeholder="e.g., 1999",
            required=True,
            max_length=4,
            min_length=4,
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
            placeholder="e.g., Keanu Reeves, Laurence Fishburne...",
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
        await interaction.response.defer(thinking=True, ephemeral=False) # Defer the response for processing

        title = self.title_input.value.strip()
        year_str = self.year_input.value.strip()
        director = self.director_input.value.strip()
        actors_str = self.actors_input.value.strip()
        genre_str = self.genre_input.value.strip()

        # Basic validation for year
        try:
            year = int(year_str)
            if not 1850 <= year <= datetime.datetime.now().year + 5:
                 await interaction.followup.send("❌ Invalid year provided. Please enter a valid 4-digit year.")
                 return
        except ValueError:
            await interaction.followup.send("❌ Invalid year format. Please enter a 4-digit number.")
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
            "plot": f"Added manually by {interaction.user.display_name}.", # Minimal plot for manual entries
            "image": None, # Use 'image' as per schema
            "rating": None, # Use 'rating' as per schema
            "imdbID": None,
            "tmdbID": None, # Algolia does not provide these for manual adds
            "source": "manual", # Indicate source
            "votes": 0, # Starts at 0
            "addedDate": int(time.time()),
            "addedBy": self.bot_instance.generate_user_token(str(interaction.user.id)),
             "voted": False # Attribute for faceting, initially False - not used for per-user status without personalization
        }

        try:
            # Check if movie already exists (basic title check)
            existing_movie = await self.bot_instance._check_movie_exists(title)
            if existing_movie:
                 await interaction.followup.send(
                    f"❌ A movie with a similar title ('{existing_movie['title']}') is already in the voting queue.")
                 return

            # Add movie to Algolia
            # Use save_object, Algolia will handle add/update based on objectID
            self.bot_instance.movies_index.save_object(movie_data)
            logger.info(f"Added movie via modal: {movie_data.get('title')} ({movie_data.get('objectID')})")

            # Create embed for confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_data['title']} ({movie_data['year'] if movie_data['year'] is not None else 'N/A'})",
                description=movie_data.get("plot", "No plot available."),
                color=0x00ff00
            )

            if movie_data.get("director") and movie_data["director"] != "Unknown":
                embed.add_field(name="Director", value=movie_data["director"], inline=True)

            if movie_data.get("actors"): # Check if the list is not empty
                embed.add_field(name="Starring", value=", ".join(movie_data["actors"][:5]), inline=False)

            if movie_data.get("genre"): # Check if the list is not empty
                embed.add_field(name="Genre", value=", ".join(movie_data["genre"]), inline=True)

            if movie_data.get("image"): # Use 'image'
                 embed.set_thumbnail(url=movie_data["image"])

            embed.set_footer(text=f"Added by {interaction.user.display_name}")

            await interaction.followup.send("✅ Movie added to the voting queue!", embed=embed)

        except Exception as e:
            logger.error(f"Error adding movie via modal: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while adding the movie: {str(e)}")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handles errors during modal interaction."""
        logger.error(f"Error in MovieAddModal for user {interaction.user.id}: {error}", exc_info=True)
        try:
             # Try to send a message back to the user about the error
            await interaction.followup.send(f"❌ An unexpected error occurred with the form. Please try again later. Error: {error}", ephemeral=True)
        except Exception as e:
             logger.error(f"Failed to send error message to user {interaction.user.id} after modal error: {e}")


# --- View for Vote Selection (Buttons) ---
class VoteSelectionView(View):
    def __init__(self, bot_instance, user_id: int, choices: List[Dict[str, Any]], timeout=300):
        super().__init__(timeout=timeout)
        self.bot_instance = bot_instance
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
        # Remove the message from pending_votes state
        if self.message:
             self.bot_instance.vote_messages.pop(self.message.id, None)
             try:
                 await self.message.edit(content="Vote selection timed out.", view=self)
             except Exception:
                  pass # Ignore edit failures if message was deleted etc.
        logger.info(f"Vote selection timed out for user {self.user_id}")


    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, custom_id="vote_select_0")
    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, custom_id="vote_select_1")
    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, custom_id="vote_select_2")
    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, custom_id="vote_select_3")
    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, custom_id="vote_select_4")
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="vote_select_cancel")
    async def handle_selection(self, interaction: discord.Interaction, button: Button):
        """Handles button presses for vote selection."""
        await interaction.response.defer(thinking=True)

        # Remove the message from state immediately
        if self.message:
             self.bot_instance.vote_messages.pop(self.message.id, None)

        # Disable buttons after selection
        for item in self.children:
            item.disabled = True
        try:
             await self.message.edit(view=self)
        except Exception:
             pass # Ignore edit failures

        if button.custom_id == "vote_select_cancel":
            await interaction.followup.send("Vote selection cancelled.")
            return

        try:
            # Get the index from the custom_id (e.g., "vote_select_0" -> 0)
            index = int(button.custom_id.split("_")[-1])
            chosen_movie = self.choices[index]

            await interaction.followup.send(f"Okay, voting for '{chosen_movie['title']}'...")

            # Now proceed with the actual voting logic
            success, result = await self.bot_instance.vote_for_movie(chosen_movie["objectID"], str(self.user_id))

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
                    await interaction.followup.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                else:
                    logger.error(f"Error recording vote during selection: {result}")
                    await interaction.followup.send(f"❌ An error occurred while recording your vote.")

        except Exception as e:
            logger.error(f"Error during vote selection button press for user {self.user_id}: {e}", exc_info=True)
            await interaction.followup.send("An unexpected error occurred. Please try voting again.")


# --- View for Movies Pagination (Buttons) ---
class MoviesPaginationView(View):
    def __init__(self, bot_instance, user_id: int, movies: List[Dict[str, Any]], movies_per_page: int = 10, detailed_count: int = 5, timeout=600):
        super().__init__(timeout=timeout)
        self.bot_instance = bot_instance
        self.user_id = user_id
        self.all_movies = movies # Store the full list of movies
        self.movies_per_page = movies_per_page
        self.detailed_count = detailed_count # How many detailed entries per page (<= movies_per_page)
        self.current_page = 0
        self.total_pages = (len(movies) + movies_per_page - 1) // movies_per_page # Ceiling division

        # Add navigation buttons
        self.add_item(Button(label="⏪ First", style=discord.ButtonStyle.secondary, custom_id="page_first", disabled=True))
        self.add_item(Button(label="◀️ Previous", style=discord.ButtonStyle.secondary, custom_id="page_prev", disabled=True))
        self.add_item(Button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="page_next", disabled=(self.total_pages <= 1)))
        self.add_item(Button(label="⏩ Last", style=discord.ButtonStyle.secondary, custom_id="page_last", disabled=(self.total_pages <= 1)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the user who invoked the command can use these buttons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This pagination is not for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        """Called when the view times out."""
        # Disable buttons on timeout
        for item in self.children:
            item.disabled = True
        # Remove the message from pending_pagination state
        if self.message:
             self.bot_instance.movies_pagination_state.pop(self.message.id, None)
             try:
                 await self.message.edit(content="Movie list pagination timed out.", view=self)
             except Exception:
                  pass # Ignore edit failures

    async def update_buttons(self):
        """Enable/disable buttons based on current page."""
        self.children[0].disabled = (self.current_page == 0) # First
        self.children[1].disabled = (self.current_page == 0) # Previous
        self.children[2].disabled = (self.current_page >= self.total_pages - 1) # Next
        self.children[3].disabled = (self.current_page >= self.total_pages - 1) # Last


    async def render_page(self, interaction: discord.Interaction):
        """Render the current page and update the message."""
        await interaction.response.defer()

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
            # Global index within the full sorted list
            global_index = start_index + i

            title = movie.get("title", "Unknown")
            year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
            votes = movie.get("votes", 0)
            rating = movie.get("rating")

            medal = "🥇" if global_index == 0 else "🥈" if global_index == 1 else "🥉" if global_index == 2 else f"{global_index + 1}."

            # Detailed entry for the first few on the page
            if i < self.detailed_count:
                movie_details = [
                     f"**Votes**: {votes}",
                     f"**Year**: {year.strip() or 'N/A'}",
                     f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                ]
                # Show plot only if it's not the minimal added manually plot
                plot = movie.get("plot", "No description available.")
                if plot and not plot.startswith("Added manually by "):
                     if len(plot) > 150:
                          plot = plot[:150] + "..."
                     movie_details.append(f"**Plot**: {plot}")
                elif plot == "No description available.":
                     movie_details.append(f"**Plot**: No description available.")


                embed.add_field(
                    name=f"{medal} {title}{year}",
                    value="\n".join(movie_details),
                    inline=False
                )

                # Set thumbnail for the first movie on the page if available
                if i == 0 and movie.get("image"):
                     embed.set_thumbnail(url=movie["image"])

            # Denser entry for the rest on the page
            else:
                 details_line = f"Votes: {votes} | Year: {year.strip() or 'N/A'} | Rating: {f'⭐ {rating}/10' if rating is not None else 'N/A'}"
                 embed.add_field(
                    name=f"{medal} {title}{year}",
                    value=details_line,
                    inline=False
                )


        embed.set_footer(text=f"Use /vote to vote for a movie! | Page {self.current_page + 1}/{self.total_pages}")


        await self.update_buttons() # Update button states
        # Edit the original message
        try:
             await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
             logger.error(f"Error editing message for movies pagination for user {self.user_id}: {e}", exc_info=True)
             # Attempt to send a new message as fallback? Or just log error.

    @discord.ui.button(label="⏪ First", style=discord.ButtonStyle.secondary, custom_id="page_first")
    async def go_first_page(self, interaction: discord.Interaction, button: Button):
        self.current_page = 0
        await self.render_page(interaction)

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, custom_id="page_prev")
    async def go_previous_page(self, interaction: discord.Interaction, button: Button):
        self.current_page = max(0, self.current_page - 1)
        await self.render_page(interaction)

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="page_next")
    async def go_next_page(self, interaction: discord.Interaction, button: Button):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self.render_page(interaction)

    @discord.ui.button(label="⏩ Last", style=discord.ButtonStyle.secondary, custom_id="page_last")
    async def go_last_page(self, interaction: discord.Interaction, button: Button):
        self.current_page = self.total_pages - 1
        await self.render_page(interaction)


class ParadisoBot:
    """Paradiso Discord bot for movie voting (Algolia Pure v3)."""

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

        # Track vote selection messages (using buttons)
        self.vote_messages = {} # Dict to store message_id: {'user_id': ..., 'choices': [...]}

        # Track movies pagination messages
        self.movies_pagination_state = {} # Dict to store message_id: {'user_id': ..., 'all_movies': [...], 'current_page': ..., 'detailed_count': ...}

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
                        # Fetch recent messages to check the last bot message
                        messages = [msg async for msg in paradiso_channel.history(limit=5)]
                        last_bot_message = next((msg for msg in messages if msg.author == self.client.user), None)

                        if last_bot_message and (datetime.datetime.utcnow() - last_bot_message.created_at.replace(tzinfo=datetime.timezone.utc)).total_seconds() < 60:
                             logger.info("Skipping welcome message to avoid spam.")
                        else:
                             await paradiso_channel.send(
                                "🎬 **Paradiso Bot** is now online! Use `/help` to see available commands or mention me for text commands.")
                             logger.info(f"Sent welcome message to #paradiso channel in {guild.name}")
                    except Exception as e:
                        # Catch other potential errors during message fetch or send
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
            # Don't respond to our own messages, slash commands, or button/modal responses
            # Bot ignores its own messages by default, but explicit check is good practice.
            # message.is_command() checks for slash command invocation messages.
            # Interactions (buttons, modals) don't trigger on_message, they trigger on_interaction.
            if message.author == self.client.user or message.is_command():
                return

            # Log message
            logger.info(f"Message received from {message.author} ({message.author.id}) in {message.channel}: {message.content}")

            user_id = message.author.id

            # --- Handle Add Movie Flow (Text-based DM flow) ---
            # This flow happens ONLY in DM, initiated by 'add' mention/DM command
            if user_id in self.add_movie_flows:
                # Ensure the message is in the correct DM channel for the flow
                if isinstance(message.channel, discord.DMChannel) and message.channel.id == self.add_movie_flows[user_id]['channel'].id:
                    await self._handle_add_movie_flow(message)
                    return # Stop processing if handled as add movie flow
                else:
                     # User typed in a server channel while a DM flow is active
                     logger.warning(f"User {user_id} typed in server channel while add flow active in DM.")
                     # Optionally remind them to check DMs, but avoid spamming
                     # This is less critical as the flow state check prevents mixing contexts.


            # --- Handle Manual Commands (DMs and mentions) ---
            # Only process if in DM or if the bot is mentioned in a server channel
            # Ensure this check happens *after* checking for active flows
            if isinstance(message.channel, discord.DMChannel) or self.client.user.mentioned_in(message):
                content = message.content.lower()

                # Remove mention from the message if it exists
                if self.client.user.mentioned_in(message):
                    # Use word boundary to avoid matching partial mentions if username contains digits
                    content = re.sub(rf'<@!?{self.client.user.id}>\b', '', content).strip()


                # Process command if content is not empty after removing mention
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
                            # Start the text-based add movie flow (includes initial search)
                            await self._start_add_movie_flow(message, query)
                        else:
                            await message.channel.send("Please provide a movie title. Example: `add The Matrix`")

                    elif content.startswith('vote '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            # Handle text-based vote command, potentially starting selection flow in DMs (using text response, not buttons here)
                            await self._handle_vote_command(message.channel, message.author, query)
                        else:
                            await message.channel.send(
                                "Please provide a movie title to vote for. Example: `vote The Matrix`")

                    elif content == 'movies':
                        # Text command uses simpler list display
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

                    # Add text-based /info equivalent
                    elif content.startswith('info '):
                        query = content.split(' ', 1)[1].strip()
                        if query:
                            await self._handle_info_command(message.channel, query)
                        else:
                            await message.channel.send("Please provide a movie title or search term. Example: `info The Matrix`")

                    # Default response for unhandled messages in DMs or mentions
                    else:
                         await self._send_help_message(message.channel)
                elif isinstance(message.channel, discord.DMChannel) and not content:
                    # If it's a DM and the message is empty after removing mention (shouldn't happen unless message was just the mention?)
                    # Or maybe user just typed something bot didn't understand
                    await self._send_help_message(message.channel)


        @self.client.event
        async def on_interaction(interaction: discord.Interaction):
            """Handle button interactions."""
            # Check if the interaction is from a button click and if it's one of our views
            if interaction.type == discord.InteractionType.component and interaction.data and interaction.data['component_type'] == 2: # 2 is Button
                custom_id = interaction.data['custom_id']

                # Handle Vote Selection Buttons
                if custom_id.startswith("vote_select_"):
                     # Find the message state associated with this button click
                     message_state = self.vote_messages.get(interaction.message.id)
                     if message_state and message_state['user_id'] == interaction.user.id:
                          # Find the view associated with the message
                          # This requires re-creating the view or accessing it if stored.
                          # The `on_interaction` method isn't tied to a specific View instance.
                          # We need the View instance to call its `on_timeout` or check `interaction_check`.
                          # A simpler approach is to handle the logic directly here and manage state.

                          # Check for timeout/staleness if not removed from state yet
                          # Note: View timeout handles disabling buttons and removing state.
                          # This check is primarily if the state wasn't cleaned up correctly.
                          # If the View instance is timed out, `on_interaction` won't be called.
                          # If the state is still present but the message is old, clean it up.
                          # For simplicity, let's assume the View timeout is sufficient.

                          # Defer the interaction response immediately
                          await interaction.response.defer(thinking=True)

                          # Remove the message from state immediately as it's handled
                          self.vote_messages.pop(interaction.message.id, None)

                          # Disable buttons on the message after handling
                          # Need to fetch the view attached to the message
                          view = View() # Create a dummy view to load items
                          view._children = [Button.from_component(c) for c in interaction.message.components[0].children] # Assuming single action row
                          for item in view.children:
                               item.disabled = True
                          try:
                               await interaction.edit_original_response(view=view)
                          except Exception:
                               pass # Ignore edit failures

                          if custom_id == "vote_select_cancel":
                               await interaction.followup.send("Vote selection cancelled.")
                               return

                          try:
                               index = int(custom_id.split("_")[-1])
                               choices = message_state['choices'] # Use the stored choices
                               if 0 <= index < len(choices):
                                    chosen_movie = choices[index]

                                    # Now proceed with the actual voting logic
                                    success, result = await self.vote_for_movie(chosen_movie["objectID"], str(interaction.user.id))

                                    if success:
                                        updated_movie = result # result is the updated movie object
                                        embed = discord.Embed(
                                            title=f"✅ Vote recorded for: {updated_movie['title']}",
                                            description=f"This movie now has {updated_movie['votes']} vote(s)!",
                                            color=0x00ff00
                                        )
                                        if updated_movie.get("image"):
                                            embed.set_thumbnail(url=updated_movie["image"])
                                        embed.set_footer(text=f"Voted by {interaction.user.display_name}")
                                        await interaction.followup.send(embed=embed)
                                    else:
                                        if isinstance(result, str) and result == "Already voted":
                                            await interaction.followup.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                                        else:
                                            logger.error(f"Error recording vote during selection: {result}")
                                            await interaction.followup.send(f"❌ An error occurred while recording your vote.")

                               else:
                                    logger.warning(f"Invalid vote selection index {index} for choices len {len(choices)} for user {interaction.user.id}")
                                    await interaction.followup.send("Invalid selection. Please try the vote command again.", ephemeral=True)


                          except Exception as e:
                               logger.error(f"Error processing vote selection button for user {interaction.user.id}: {e}", exc_info=True)
                               await interaction.followup.send("An unexpected error occurred. Please try voting again.")

                     else:
                          # Interaction from a vote button for a different user or old message
                          await interaction.response.send_message("This button is not active or not for you.", ephemeral=True)

                # Handle Movies Pagination Buttons
                elif custom_id.startswith("page_"):
                     message_state = self.movies_pagination_state.get(interaction.message.id)
                     if message_state and message_state['user_id'] == interaction.user.id:
                          # Get the View instance associated with this message from the state
                          # This requires storing the view instance itself, or re-creating its state from the dict
                          # Let's store the necessary data in the dict and re-create a temporary View state
                          all_movies = message_state['all_movies']
                          current_page = message_state['current_page']
                          movies_per_page = message_state['movies_per_page']
                          detailed_count = message_state['detailed_count']

                          total_pages = (len(all_movies) + movies_per_page - 1) // movies_per_page

                          if custom_id == "page_first":
                               current_page = 0
                          elif custom_id == "page_prev":
                               current_page = max(0, current_page - 1)
                          elif custom_id == "page_next":
                               current_page = min(total_pages - 1, current_page + 1)
                          elif custom_id == "page_last":
                               current_page = total_pages - 1

                          # Update the state
                          message_state['current_page'] = current_page
                          self.movies_pagination_state[interaction.message.id] = message_state

                          # Render the new page and update the message
                          await interaction.response.defer(thinking=True) # Defer before editing
                          await self._render_movies_page(interaction, message_state) # Pass the necessary info


                     else:
                          # Interaction from a pagination button for a different user or old message
                          await interaction.response.send_message("This button is not active or not for you.", ephemeral=True)

                # If you add other button types, add elif here


    def _register_commands(self):
        """Register Discord slash commands."""
        # Use the Modal for the /add command
        @self.tree.command(name="add", description="Add a movie to the voting queue with structured input")
        @app_commands.describe(title="Optional: Provide a title to pre-fill the form")
        async def cmd_add_slash(interaction: discord.Interaction, title: Optional[str] = None):
            """Slash command to add a movie, prompting with a modal."""
            # Defer the interaction immediately to avoid timeout
            await interaction.response.send_modal(MovieAddModal(self, movie_title=title or ""))
            # No need for followup.send_modal if using response.send_modal
            # If response.send_modal still times out, try deferring first:
            # await interaction.response.defer(thinking=True, ephemeral=True)
            # await interaction.followup.send_modal(MovieAddModal(self, movie_title=title or ""))


        self.tree.command(name="vote", description="Vote for a movie in the queue")(self.cmd_vote)
        self.tree.command(name="movies", description="List all movies in the voting queue")(self.cmd_movies)
        self.tree.command(name="search", description="Search for movies in the database")(self.cmd_search)
        self.tree.command(name="related", description="Find related movies based on a movie in the database")(self.cmd_related)
        self.tree.command(name="top", description="Show the top voted movies")(self.cmd_top)
        self.tree.command(name="info", description="Get detailed info for a movie")(self.cmd_info) # New command
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
            logger.error(f"Error running the bot: {e}", exc_info=True)


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
            value="`add [movie title]` - Start adding a movie (I'll search first, then DM for details if needed)\n"
                  "`vote [movie title]` - Vote for a movie (handles ambiguity)\n"
                  "`movies` - See all movies in the queue (limited list)\n"
                  "`search [query]` - Search for movies (simple query)\n"
                  "`related [query]` - Find related movies (simple query)\n"
                  "`top [count]` - Show top voted movies (limited list)\n"
                  "`info [query]` - Get detailed info for a movie\n" # New help text
                  "`help` - Show this help message",
            inline=False
        )

        help_embed.add_field(
            name="Slash Commands (In Server)",
            value="`/add [title]` - Add a movie using a pop-up form (Recommended!)\n"
                  "`/vote [title]` - Vote for a movie (handles ambiguity via buttons)\n"
                  "`/movies` - See all movies (paginated list)\n"
                  "`/search [query]` - Search movies (supports filters like `year:>2000`)\n" # Update help text
                  "`/related [query]` - Find related movies (supports filters like `genre:Action`)\n" # Update help text
                  "`/top [count]` - Show top voted movies (max 20)\n"
                  "`/info [query]` - Get detailed info for a movie\n" # New help text
                  "`/help` - Show this help message",
            inline=False
        )

        help_embed.add_field(
             name="Search Filters (for /search and /related)",
             value="You can filter searches using `key:value`. Examples:\n"
                   "`/search matrix year:1999`\n"
                   "`/search action genre:Comedy director:Nolan`\n"
                   "`/search year>2010 votes:>5`\n"
                   "Use quotes for multi-word values: `/search actor:\"Tom Hanks\"`\n"
                   "Supported keys: `year`, `director`, `actor`, `genre`, `votes`, `rating`.",
             inline=False
        )


        help_embed.set_footer(text="Happy voting! 🎬")

        await channel.send(embed=help_embed)

    async def _handle_search_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query_string: str):
        """Handle a text-based search command."""
        try:
            # Text command search does NOT support advanced filters for simplicity
            query = query_string.strip()

            if not query:
                 await channel.send("Please provide a search term.")
                 return

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

            await self._send_search_results_embed(channel, query, search_results["hits"], search_results["nbHits"])

        except Exception as e:
            logger.error(f"Error in manual search command: {e}", exc_info=True)
            await channel.send(f"An error occurred during search: {str(e)}")

    async def _handle_info_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query: str):
        """Handle a text-based info command."""
        try:
            movie = await self.find_movie_by_title(query)

            if not movie:
                await channel.send(f"Could not find a movie matching '{query}'. Use `search [query]` to find movies.")
                return

            await self._send_detailed_movie_embed(channel, movie)

        except Exception as e:
            logger.error(f"Error in manual info command: {e}", exc_info=True)
            await channel.send(f"An error occurred while fetching movie info: {str(e)}")


    async def _start_add_movie_flow(self, message: discord.Message, title: str):
        """Start the interactive text-based flow to add a movie in DMs (after initial search)."""
        user_id = message.author.id

        # Check if a flow is already active for this user
        if user_id in self.add_movie_flows:
            await message.channel.send("You are already in the process of adding a movie. Please complete or type 'cancel'.")
            if not isinstance(message.channel, discord.DMChannel):
                dm_channel = await message.author.create_dm()
                await message.channel.send(f"📬 You are already in the process of adding a movie. Please check your DMs ({dm_channel.mention}).")
            return

        try:
            # First, search for the movie in Algolia
            search_results = self.movies_index.search(title, {
                "hitsPerPage": 3, # Show top 3 hits
                 "attributesToRetrieve": ["objectID", "title", "year", "director", "actors", "genre"]
            })

            if search_results["nbHits"] > 0:
                 # If results are found, inform the user and suggest alternatives
                 embed = discord.Embed(
                     title=f"Movies Found Matching '{title}'",
                     description="The following movies are already in the queue or are potential matches.\n\n"
                                 "If your movie is listed, use `/vote [title]` in a server to vote.\n"
                                 "If your movie is *not* listed, or if you want to add a new entry anyway, reply 'add new' to proceed with manual entry.",
                     color=0xffa500 # Orange
                 )
                 for i, hit in enumerate(search_results["hits"]):
                      year = f" ({hit.get('year')})" if hit.get('year') is not None else ""
                      embed.add_field(name=f"{i+1}. {hit.get('title', 'Unknown')}{year}", value=f"Votes: {hit.get('votes', 0)}", inline=False)

                 # Store state temporarily for the 'add new' response
                 dm_channel = await message.author.create_dm()
                 self.add_movie_flows[user_id] = {
                     'title': title,
                     'stage': 'await_add_new_confirmation', # New stage
                     'channel': dm_channel,
                      'original_channel': message.channel # Store original channel
                 }

                 await dm_channel.send(embed=embed)
                 if not isinstance(message.channel, discord.DMChannel):
                      await message.channel.send(f"📬 Found potential matches for '{title}'. Please check your DMs ({dm_channel.mention}) to see if your movie is listed or proceed with manual entry.")

            else:
                # No results found, proceed directly to manual input flow
                dm_channel = await message.author.create_dm()
                self.add_movie_flows[user_id] = {
                    'title': title,
                    'year': None,
                    'director': None,
                    'actors': [],
                    'genre': [],
                    'stage': 'year', # Start manual entry
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
            # Clean up flow state on error
            if user_id in self.add_movie_flows:
                 del self.add_movie_flows[user_id]


    async def _handle_add_movie_flow(self, message: discord.Message):
        """Handle responses in the text-based add movie flow (in DMs)."""
        user_id = message.author.id
        flow = self.add_movie_flows.get(user_id)

        # Ensure the message is in the correct DM channel for the flow
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
                     logger.warning(f"Could not send cancel message to original channel: {e}", exc_info=True)
            del self.add_movie_flows[user_id]
            return

        # Handle confirmation response after showing search results
        if flow['stage'] == 'await_add_new_confirmation':
             if response.lower() == 'add new':
                 await message.channel.send("Okay, let's proceed with manual entry.")
                 flow['stage'] = 'year' # Transition to manual entry flow
                 # Keep the flow state updated in the dict
                 self.add_movie_flows[user_id] = flow
                 await message.channel.send(
                    f"📽️ What year was '{flow.get('title', 'this movie')}' released? (Type 'unknown' if you're not sure, or 'cancel' to stop)")
             else:
                 await message.channel.send("Understood. If you want to add a movie not in the list, please reply 'add new'. Otherwise, use the vote command or 'cancel'.")
                 return # Stay in this stage until 'add new' or 'cancel'


        elif flow['stage'] == 'year':
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
                    "objectID": f"manual_{int(time.time())}", # Unique ID for manual entries
                    "title": flow.get('title', 'Unknown Movie'),
                    "originalTitle": flow.get('title', 'Unknown Movie'),
                    "year": flow['year'],
                    "director": flow['director'] or "Unknown",
                    "actors": flow['actors'],
                    "genre": flow['genre'],
                    "plot": f"Added manually by {message.author.display_name}.",
                    "image": None, # Use 'image'
                    "rating": None, # Use 'rating'
                    "imdbID": None,
                    "tmdbID": None,
                    "source": "manual",
                    "votes": 0,
                    "addedDate": int(time.time()),
                    "addedBy": self.generate_user_token(str(message.author.id)),
                    "voted": False
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
        if user_id in self.add_movie_flows and flow['stage'] not in ['year', 'director', 'actors', 'genre', 'confirm_manual', 'await_add_new_confirmation']:
             del self.add_movie_flows[user_id]


    async def _add_movie_from_flow(self, user_id: int, movie_data: Dict[str, Any], author: discord.User, original_channel: Optional[discord.TextChannel]):
        """Helper to add the movie to Algolia and send confirmation after text flow."""
        try:
            # Check if movie already exists in Algolia by title (fuzzy match)
            existing_movie = await self._check_movie_exists(movie_data['title'])

            if existing_movie:
                 await self.add_movie_flows[user_id]['channel'].send(
                    f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                 if original_channel and not isinstance(original_channel, discord.DMChannel):
                      try:
                         await original_channel.send(
                            f"❌ A movie with a similar title is already in the voting queue: '{existing_movie['title']}'")
                      except Exception as e:
                           logger.warning(f"Could not send exists message to original channel: {e}", exc_info=True)
                 del self.add_movie_flows[user_id]
                 return

            # Add movie to Algolia
            # Use save_object, Algolia will handle add/update based on objectID
            self.movies_index.save_object(movie_data)
            logger.info(f"Added movie in text flow: {movie_data.get('title')} ({movie_data.get('objectID')})")


            # Create embed for movie confirmation
            embed = discord.Embed(
                title=f"🎬 Added: {movie_data['title']} ({movie_data['year'] if movie_data['year'] is not None else 'N/A'})",
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

            embed.set_footer(text=f"Added by {author.display_name}")

            # Send confirmation to DM channel first
            await self.add_movie_flows[user_id]['channel'].send("✅ Movie added to the voting queue!", embed=embed)

            # If original channel was different (i.e., a server channel), send confirmation there too
            if original_channel and original_channel != self.add_movie_flows[user_id]['channel'] and not isinstance(original_channel, discord.DMChannel):
                 try:
                    await original_channel.send(f"✅ Movie '{movie_data['title']}' added to the voting queue!")
                    # Optionally send the embed to the original channel as well
                    # await original_channel.send(embed=embed)
                 except Exception as e:
                     logger.warning(f"Could not send add confirmation to original channel: {e}", exc_info=True)


        except Exception as e:
            logger.error(f"Error adding movie in text flow: {e}", exc_info=True)
            await self.add_movie_flows[user_id]['channel'].send(f"❌ An error occurred while adding the movie: {str(e)}")
            if original_channel and not isinstance(original_channel, discord.DMChannel):
                 try:
                    await original_channel.send(f"❌ An error occurred while adding the movie '{movie_data.get('title', 'Unknown Movie')}': {str(e)}")
                 except Exception as e:
                      logger.warning(f"Could not send add error to original channel: {e}", exc_info=True)

        finally:
            # Clean up the flow regardless of success or failure
            if user_id in self.add_movie_flows:
                 del self.add_movie_flows[user_id]


    async def _handle_vote_command(self, channel: Union[discord.TextChannel, discord.DMChannel], author: discord.User, title: str):
        """Handle a text-based vote command."""
        try:
            # Find potential movies for voting
            search_results = await self.search_movies_for_vote(title) # Use helper

            if search_results["nbHits"] == 0:
                await channel.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                # Found exactly one movie, vote directly
                movie_to_vote = hits[0]
                await channel.send(f"Found '{movie_to_vote['title']}'. Recording your vote...")
                success, result = await self.vote_for_movie(movie_to_vote["objectID"], str(author.id))

                if success:
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"):
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {author.display_name}")
                    await channel.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted":
                         await channel.send(f"❌ You have already voted for '{movie_to_vote['title']}'!")
                    else:
                        logger.error(f"Error recording vote for single match (text cmd): {result}")
                        await channel.send(f"❌ An error occurred while recording your vote.")

            else:
                # Multiple matches, present choices in an embed and ask user to reply with a number
                # This is the text-based version of the vote selection flow
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
                # We'll use the message.author.id for state tracking in on_message
                dm_channel = await author.create_dm() # Send selection prompt to DM
                self.pending_votes[author.id] = {
                    'channel': dm_channel, # Need the channel to check response context
                    'choices': choices,
                    'timestamp': time.time()
                }

                await dm_channel.send(embed=embed)
                if not isinstance(channel, discord.DMChannel):
                    await channel.send(f"Found multiple matches for '{title}'. Please check your DMs ({dm_channel.mention}) to select the movie you want to vote for.")
                # Note: The actual handling of the user's number response in DM is done in on_message.


        except Exception as e:
            logger.error(f"Error in manual vote command for title '{title}': {e}", exc_info=True)
            await channel.send(f"❌ An error occurred while searching for the movie: {str(e)}")


    async def _handle_movies_command(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        """Handle a text-based movies command (limited list, no pagination)."""
        try:
            movies = await self.get_top_movies(10) # Get top 10 for text command

            if not movies:
                await channel.send("No movies have been voted for yet! Use `add [title]` to add one or `/add` in a server.")
                return

            # Create an embed
            embed = discord.Embed(
                title="🎬 Paradiso Movie Night Voting (Top 10)",
                description=f"Here are the current top voted movies:",
                color=0x03a9f4,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )

            for i, movie in enumerate(movies):
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

                 # Show plot only if it's not the minimal added manually plot
                plot = movie.get("plot", "No description available.")
                if plot and not plot.startswith("Added manually by "):
                     if len(plot) > 100: # Shorter plot for denser list
                          plot = plot[:100] + "..."
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
            # Limit count to reasonable values for text commands
            count = max(1, min(10, count))

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
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."

                movie_details = [
                    f"**Votes**: {movie.get('votes', 0)}",
                    f"**Year**: {movie.get('year', 'N/A')}",
                ]
                rating = movie.get("rating")
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

            embed.set_footer(text="Use 'vote [title]' to vote for a movie or '/vote' in a server!")

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in manual top command: {e}", exc_info=True)
            await channel.send(f"❌ An error occurred: {str(e)}")


    # --- Vote Selection Response Handler (for text-based DM flow) ---
    # This is called by on_message if user is in a pending vote state
    async def _handle_vote_selection_response(self, message: discord.Message, flow_state: Dict[str, Any]):
        """Handles a user's numerical response during the text-based vote selection flow (in DMs)."""
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
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"):
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {message.author.display_name}")
                    await message.channel.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted":
                        await message.channel.send(f"❌ You have already voted for '{chosen_movie['title']}'!")
                    else:
                        logger.error(f"Error recording vote during text selection: {result}")
                        await message.channel.send(f"❌ An error occurred while recording your vote.")

                # Clean up the pending vote state
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
            search_results = await self.search_movies_for_vote(title) # Use helper

            if search_results["nbHits"] == 0:
                await interaction.followup.send(
                    f"❌ Could not find any movies matching '{title}' in the voting queue. Use `/movies` to see available movies.")
                return

            hits = search_results["hits"]

            if search_results["nbHits"] == 1:
                # Found exactly one movie, vote directly
                movie_to_vote = hits[0]
                # No extra message needed, just vote and send confirmation embed
                success, result = await self.vote_for_movie(movie_to_vote["objectID"], str(user_id))

                if success:
                    updated_movie = result
                    embed = discord.Embed(
                        title=f"✅ Vote recorded for: {updated_movie['title']}",
                        description=f"This movie now has {updated_movie['votes']} vote(s)!",
                        color=0x00ff00
                    )
                    if updated_movie.get("image"):
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {interaction.user.display_name}")
                    await interaction.followup.send(embed=embed)
                else:
                    if isinstance(result, str) and result == "Already voted":
                         await interaction.followup.send(f"❌ You have already voted for '{movie_to_vote['title']}'!")
                    else:
                        logger.error(f"Error recording vote for single match (slash cmd): {result}")
                        await interaction.followup.send(f"❌ An error occurred while recording your vote.")

            else:
                # Multiple matches, start interactive selection flow using buttons
                # Limit choices to top 5 for button UI
                choices = hits[:5]

                embed = discord.Embed(
                    title=f"Multiple movies found for '{title}'",
                    description="Please select the movie you want to vote for:",
                    color=0xffa500
                )

                # Add choices to embed field values
                choice_list = []
                for i, movie in enumerate(choices):
                     year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                     votes = movie.get('votes', 0)
                     choice_list.append(f"{i+1}. {movie.get('title', 'Unknown')}{year} (Votes: {votes})")

                embed.add_field(name="Choices", value="\n".join(choice_list), inline=False)
                embed.set_footer(text="Select a number below or press Cancel.")


                # Create the View with buttons
                view = VoteSelectionView(self, user_id, choices)

                # Send the message with embed and view
                message = await interaction.followup.send(embed=embed, view=view)

                # Store the message ID and state for the view's interaction_check and timeout
                self.vote_messages[message.id] = {'user_id': user_id, 'choices': choices}
                view.message = message # Link the view to the message for timeout editing


        except Exception as e:
            logger.error(f"Error in /vote command for title '{title}': {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while searching for the movie: {str(e)}")


    async def cmd_movies(self, interaction: discord.Interaction):
        """List all movies in the voting queue with pagination."""
        await interaction.response.defer()

        try:
            # Fetch all movies (or a large enough number for pagination)
            # Use get_all_movies which also sorts
            all_movies = await self.get_all_movies()

            if not all_movies:
                await interaction.followup.send("No movies have been added yet! Use `/add` to add one.")
                return

            movies_per_page = 10 # Define how many movies per page
            detailed_count = 5 # Define how many detailed entries per page

            # Create and send the initial page embed and view
            view = MoviesPaginationView(self, interaction.user.id, all_movies, movies_per_page, detailed_count)

            # Render the first page
            start_index = view.current_page * view.movies_per_page
            end_index = start_index + view.movies_per_page
            page_movies = all_movies[start_index:end_index]

            embed = discord.Embed(
                title=f"🎬 Paradiso Movie Night Voting (Page {view.current_page + 1}/{view.total_pages})",
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

                medal = "🥇" if global_index == 0 else "🥈" if global_index == 1 else "🥉" if global_index == 2 else f"{global_index + 1}."

                if i < detailed_count:
                    movie_details = [
                         f"**Votes**: {votes}",
                         f"**Year**: {year.strip() or 'N/A'}",
                         f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                    ]
                    plot = movie.get("plot", "No description available.")
                    if plot and not plot.startswith("Added manually by "):
                         if len(plot) > 150:
                              plot = plot[:150] + "..."
                         movie_details.append(f"**Plot**: {plot}")
                    elif plot == "No description available.":
                         movie_details.append(f"**Plot**: No description available.")

                    embed.add_field(
                        name=f"{medal} {title}{year}",
                        value="\n".join(movie_details),
                        inline=False
                    )

                    if i == 0 and movie.get("image"):
                         embed.set_thumbnail(url=movie["image"])
                else:
                     details_line = f"Votes: {votes} | Year: {year.strip() or 'N/A'} | Rating: {f'⭐ {rating}/10' if rating is not None else 'N/A'}"
                     embed.add_field(
                        name=f"{medal} {title}{year}",
                        value=details_line,
                        inline=False
                    )


            embed.set_footer(text=f"Use /vote to vote for a movie! | Page {view.current_page + 1}/{view.total_pages}")


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
            view.message = message # Link the view to the message for timeout


        except Exception as e:
            logger.error(f"Error in /movies command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while getting the movies. Please try again.")

    # Helper to render a specific page of movies after button click
    async def _render_movies_page(self, interaction: discord.Interaction, state: Dict[str, Any]):
         """Renders a specific page of the movie list based on stored state."""
         all_movies = state['all_movies']
         current_page = state['current_page']
         movies_per_page = state['movies_per_page']
         detailed_count = state['detailed_count']

         total_pages = (len(all_movies) + movies_per_page - 1) // movies_per_page

         start_index = current_page * movies_per_page
         end_index = start_index + movies_per_page
         page_movies = all_movies[start_index:end_index]

         embed = discord.Embed(
            title=f"🎬 Paradiso Movie Night Voting (Page {current_page + 1}/{total_pages})",
            description=f"Showing movies {start_index + 1}-{min(end_index, len(all_movies))} out of {len(all_movies)}:",
            color=0x03a9f4,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
         )

         # Re-create a View instance for this update with the correct state
         view = MoviesPaginationView(self, interaction.user.id, all_movies, movies_per_page, detailed_count, timeout=600)
         view.current_page = current_page # Set the current page
         view.message = interaction.message # Link back to the message


         for i, movie in enumerate(page_movies):
             global_index = start_index + i
             title = movie.get("title", "Unknown")
             year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
             votes = movie.get("votes", 0)
             rating = movie.get("rating")

             medal = "🥇" if global_index == 0 else "🥈" if global_index == 1 else "🥉" if global_index == 2 else f"{global_index + 1}."

             if i < detailed_count:
                 movie_details = [
                      f"**Votes**: {votes}",
                      f"**Year**: {year.strip() or 'N/A'}",
                      f"**Rating**: ⭐ {rating}/10" if rating is not None else "Rating: N/A"
                 ]
                 plot = movie.get("plot", "No description available.")
                 if plot and not plot.startswith("Added manually by "):
                      if len(plot) > 150:
                           plot = plot[:150] + "..."
                      movie_details.append(f"**Plot**: {plot}")
                 elif plot == "No description available.":
                      movie_details.append(f"**Plot**: No description available.")

                 embed.add_field(
                     name=f"{medal} {title}{year}",
                     value="\n".join(movie_details),
                     inline=False
                 )

                 if i == 0 and movie.get("image"):
                      embed.set_thumbnail(url=movie["image"])
             else:
                  details_line = f"Votes: {votes} | Year: {year.strip() or 'N/A'} | Rating: {f'⭐ {rating}/10' if rating is not None else 'N/A'}"
                  embed.add_field(
                     name=f"{medal} {title}{year}",
                     value=details_line,
                     inline=False
                 )

         embed.set_footer(text=f"Use /vote to vote for a movie! | Page {current_page + 1}/{total_pages}")

         await view.update_buttons() # Update button states based on the new page

         # Edit the original message with the new embed and view
         await interaction.edit_original_response(embed=embed, view=view)


    async def cmd_search(self, interaction: discord.Interaction, query: str):
        """Search for movies in the database with optional filters."""
        await interaction.response.defer()

        try:
            # Parse the query string for filters
            main_query, filter_string = self._parse_algolia_filters(query)
            logger.info(f"Parsed Search: Query='{main_query}', Filters='{filter_string}'")

            # Build search parameters
            search_params = {
                "hitsPerPage": 10,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                 "attributesToHighlight": [
                     "title", "originalTitle", "director", "actors", "year", "plot", "genre"
                ],
                 "attributesToSnippet": [
                    "plot:20"
                ]
            }

            if filter_string:
                 search_params["filters"] = filter_string

            # Search in Algolia
            search_results = self.movies_index.search(main_query, search_params)

            await self._send_search_results_embed(interaction.followup, query, search_results["hits"], search_results["nbHits"])

        except Exception as e:
            logger.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred during search: {str(e)}")

    async def _send_search_results_embed(self, target: Union[discord.TextChannel, discord.DMChannel, discord.Webhook], query: str, hits: List[Dict[str, Any]], nb_hits: int):
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
             if movie.get("director"):
                 director_display = movie.get("_highlightResult", {}).get("director", {}).get("value", movie["director"])
                 movie_details.append(f"**Director**: {director_display}")

             if movie.get("actors") and len(movie["actors"]) > 0:
                 actors_display = movie.get("_highlightResult", {}).get("actors", [])
                 actors_str = ", ".join([h['value'] for h in actors_display]) if actors_display else ", ".join(movie["actors"][:5])
                 movie_details.append(f"**Starring**: {actors_str}")

             if movie.get("genre") and len(movie["genre"]) > 0:
                 genre_display = movie.get("_highlightResult", {}).get("genre", [])
                 genre_str = ", ".join([h['value'] for h in genre_display]) if genre_display else ", ".join(movie["genre"])
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


    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms (using attribute search as proxy) with optional filters."""
        await interaction.response.defer()

        try:
            # First parse the query string for filters, keeping only the main query part for finding the reference movie
            main_query, filter_string = self._parse_algolia_filters(query)
            logger.info(f"Parsed Related: Query='{main_query}', Filters='{filter_string}'")


            # Find the reference movie using only the main query part
            reference_movie = await self.find_movie_by_title(main_query)

            if not reference_movie:
                await interaction.followup.send(f"Could not find a movie matching '{main_query}' in the database to find related titles.")
                return

            # Build a search query for related movies based on attributes of the reference movie
            related_query_parts = []
            if reference_movie.get("genre"):
                related_query_parts.extend(reference_movie["genre"])
            if reference_movie.get("director") and reference_movie.get("director") != "Unknown":
                related_query_parts.append(reference_movie["director"])
            if reference_movie.get("actors"):
                related_query_parts.extend(reference_movie["actors"][:3])

            # If the reference movie has minimal data, use its title as a fallback query
            if not related_query_parts:
                 related_query = reference_movie.get("title", main_query)
                 logger.info(f"No rich attributes for related search for '{reference_movie.get('title')}', using title as query.")
            else:
                 related_query = " ".join(related_query_parts)
                 logger.info(f"Generated related query for '{reference_movie.get('title')}': {related_query}")


            # Search for related movies in Algolia, applying any parsed filters AND excluding the original movie
            related_search_params = {
                "hitsPerPage": 5,
                 "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                 "attributesToHighlight": [
                     "director", "actors", "genre"
                 ],
            }

            # Combine the exclusion filter with any user-provided filters
            combined_filters = f"NOT objectID:{reference_movie['objectID']}"
            if filter_string:
                 combined_filters = f"({combined_filters}) AND ({filter_string})" # Combine with AND

            related_search_params["filters"] = combined_filters

            # Perform the related search
            related_results = self.movies_index.search(related_query, related_search_params)

            if related_results["nbHits"] == 0:
                await interaction.followup.send(f"Couldn't find any movies clearly related to '{reference_movie['title']}' based on its attributes and your filters.")
                return

            # Create an embed for related movies
            embed = discord.Embed(
                title=f"🎬 Movies Related to '{reference_movie.get('title', 'Unknown')}'",
                description=f"Based on attributes like genre, director, and actors:",
                color=0x03a9f4
            )

            # Add the reference movie details
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

            # Add a separator
            if related_results["hits"]:
                 embed.add_field(name="Related Movies", value="─────────────", inline=False)

            # Add related movies
            for i, movie in enumerate(related_results["hits"]):
                title = movie.get("title", "Unknown")
                year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
                votes = movie.get("votes", 0)
                rating = movie.get("rating")

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
                movie_details = [detail for detail in movie_details if detail]

                embed.add_field(
                    name=f"{i+1}. {title}{year}",
                    value="\n".join(relation_points + movie_details) or "Details not available.",
                    inline=False
                )

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
            count = max(1, min(20, count))

            top_movies = await self.get_top_movies(count)

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

                movie_details = [
                    f"**Votes**: {movie.get('votes', 0)}",
                    f"**Year**: {movie.get('year', 'N/A')}",
                ]
                rating = movie.get("rating")
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

            embed.set_footer(text="Use /vote to vote for a movie!")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /top command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def cmd_info(self, interaction: discord.Interaction, query: str):
         """Get detailed info for a movie."""
         await interaction.response.defer(thinking=True)

         try:
             movie = await self.find_movie_by_title(query)

             if not movie:
                 await interaction.followup.send(f"Could not find a movie matching '{query}'. Use `/search [query]` to find movies.")
                 return

             await self._send_detailed_movie_embed(interaction.followup, movie)

         except Exception as e:
             logger.error(f"Error in /info command: {e}", exc_info=True)
             await interaction.followup.send(f"❌ An error occurred while fetching movie info: {str(e)}")

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
                "description": "Vote for a movie in the queue (handles ambiguous titles via buttons)"
            },
             {
                "name": "/movies",
                "description": "List all movies in the voting queue (paginated list)"
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
                "name": "/info [query]",
                "description": "Get detailed info for a movie" # New help text
            },
             {
                "name": "/help",
                "description": "Show this help message"
            }
        ]

        for cmd in commands:
            embed.add_field(name=cmd["name"], value=cmd["description"], inline=False)

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

        await interaction.response.send_message(embed=embed)

    async def _send_detailed_movie_embed(self, target: Union[discord.TextChannel, discord.DMChannel, discord.Webhook], movie: Dict[str, Any]):
        """Helper to send a detailed embed for a single movie."""
        title = movie.get('title', 'Unknown Movie')
        year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
        rating = movie.get('rating')

        embed = discord.Embed(
            title=f"🎬 {title}{year}",
            color=0x1a73e8 # Blue color
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

        if movie.get("actors"):
             embed.add_field(name="Starring", value=", ".join(movie["actors"]), inline=False)

        if movie.get("genre"):
             embed.add_field(name="Genre", value=", ".join(movie["genre"]), inline=False)

        plot = movie.get("plot", "No plot available.")
        if plot:
             embed.add_field(name="Plot", value=plot, inline=False)

        if movie.get("image"):
             # Use set_image for a larger image display
             embed.set_image(url=movie["image"])

        embed.set_footer(text=f"Added: {datetime.datetime.fromtimestamp(movie.get('addedDate', 0), datetime.timezone.utc).strftime('%Y-%m-%d')} | Source: {movie.get('source', 'N/A')}")

        # Optional: add fields for imdbID, tmdbID if you want to link to external sites
        # if movie.get("imdbID"):
        #      embed.add_field(name="IMDb ID", value=movie["imdbID"], inline=True)
        # if movie.get("tmdbID"):
        #      embed.add_field(name="TMDB ID", value=movie["tmdbID"], inline=True)


        await target.send(embed=embed)



    # --- Helper Methods (Algolia Interactions and Filter Parsing) ---

    def _parse_algolia_filters(self, query_string: str) -> Tuple[str, str]:
         """
         Parses a query string to extract key:value filters for Algolia.
         Returns the remaining query text and the constructed filters string.

         Syntax supported:
         key:value (exact match)
         key:"multi word value" (exact match with spaces)
         key:value1 TO value2 (range)
         key:>value, key:<value, key>=value (numerical range)

         Supported keys (map to Algolia attributes):
         year -> year (numeric)
         director -> director (string)
         actor -> actors (list of strings)
         genre -> genre (list of strings)
         votes -> votes (numeric)
         rating -> rating (numeric)
         """
         parts = []
         filters = []
         # Regex to find words or quoted phrases, potentially followed by : and a value/quoted value
         # This regex is tricky; a simpler approach might be better or more robust parsing.
         # Let's use a simpler split and check for key: patterns.
         words = re.findall(r'(?:"(?:[^"]*)")|\S+', query_string) # Find words or quoted phrases

         algolia_attribute_map = {
             "year": "year",
             "director": "director",
             "actor": "actors",
             "genre": "genre",
             "votes": "votes",
             "rating": "rating"
         }

         for word in words:
             # Remove quotes if present
             cleaned_word = word.strip('"')

             # Check if it looks like a filter (contains ':')
             if ':' in cleaned_word:
                 key, value_part = cleaned_word.split(':', 1)
                 mapped_key = algolia_attribute_map.get(key.lower())

                 if mapped_key:
                     # Handle numerical ranges (>value, <value, =value, value1 TO value2)
                     if re.match(r'[<>]=?\s*\d+(\.\d+)?$', value_part.strip()):
                          filters.append(f"{mapped_key}{value_part.strip()}") # e.g., "year:>2000"
                     elif re.match(r'\d+(\.\d+)?\s+TO\s+\d+(\.\d+)?$', value_part.strip(), re.IGNORECASE):
                          filters.append(f"{mapped_key}:{value_part.strip()}") # e.g., "year:1990 TO 2000"
                     else:
                         # Handle exact or contains filters (string attributes or list items)
                         # Algolia filter syntax: `attribute:value` for exact, or `attribute:"partial value"` for string search
                         # For list attributes (actors, genre), `attribute:value` filters if *any* item is value.
                         # Using simple exact filter `attribute:value` for now.
                         filters.append(f'{mapped_key}:"{value_part.strip()}"') # Wrap value in quotes for robustness

                 else:
                     # Not a recognized filter key, treat as part of the query
                     parts.append(word) # Use original word (with quotes if any)

             else:
                 # Not a filter, treat as part of the query
                 parts.append(word)

         main_query = " ".join(parts).strip()
         filter_string = " AND ".join(filters).strip()

         return main_query, filter_string


    async def _check_movie_exists(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Checks if a movie with a similar title already exists in Algolia.
        Uses search and checks for strong matches.
        """
        try:
            # Search with the title, prioritize exact/full matches
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5,
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

            logger.info(f"Existing movie check: No strong title match for '{title}' among top hits.")
            return None

        except Exception as e:
            logger.error(f"Error checking existence for title '{title}' in Algolia: {e}", exc_info=True)
            return None


    async def search_movies_for_vote(self, title: str) -> Dict[str, Any]:
        """
        Searches for movies by title for the voting command.
        Returns search results (up to ~5 hits) allowing for ambiguity.
        """
        try:
            # Use Algolia search, allowing some typo tolerance for finding potential matches
            # No filter parsing needed here, just the title
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5, # Get up to 5 relevant hits for selection
                "attributesToRetrieve": [
                    "objectID", "title", "year", "votes", "image" # Get necessary info for selection
                ],
                 "typoTolerance": True # Allow fuzzy matching for voting search
            })

            logger.info(f"Vote search for '{title}' found {search_result['nbHits']} hits.")
            return search_result

        except Exception as e:
            logger.error(f"Error searching for movies for vote '{title}' in Algolia: {e}", exc_info=True)
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
                existing_movie = await self.get_movie_by_id(movie_id) # Fetch current state
                return False, existing_movie if existing_movie else "Already voted"


            # Record the vote in the votes index
            vote_obj = {
                "objectID": f"vote_{user_token[:8]}_{movie_id}_{int(time.time())}_{random.randint(0, 9999):04d}",
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
            })
            logger.info(f"Sent increment task for movie {movie_id}. Task ID: {update_result['taskID']}")

            # Wait for the update task
            try:
                self.movies_index.wait_task(update_result['taskID'])
                logger.info(f"Algolia task {update_result['taskID']} completed.")
            except Exception as e:
                 logger.warning(f"Failed to wait for Algolia task {update_result['taskID']}: {e}. Fetching potentially stale movie data.", exc_info=True)


            # Fetch the updated movie object
            updated_movie = await self.get_movie_by_id(movie_id)
            if updated_movie:
                 logger.info(f"Fetched updated movie {movie_id}. New vote count: {updated_movie.get('votes', 0)}")
                 return True, updated_movie
            else:
                 logger.error(f"Vote recorded for {movie_id}, but failed to fetch updated movie object after waiting. Attempting fallback.", exc_info=True)
                 # Fallback: Get latest known data and increment votes locally
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
            return self.movies_index.get_object(movie_id)
        except Exception as e:
            logger.error(f"Error getting movie by ID {movie_id} from Algolia: {e}", exc_info=True)
            return None

    async def find_movie_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a movie by title in Algolia movies index using search.
        Prioritizes strong matches. Used for commands like /info, /related,
        and add pre-check where a single reference movie is needed.
        """
        if not title: return None
        try:
            search_result = self.movies_index.search(title, {
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


    async def get_top_movies(self, count: int = 5) -> List[Dict[str, Any]]:
        """Get the top voted movies from Algolia movies index."""
        try:
            search_result = self.movies_index.search("", {
                "filters": "votes > 0",
                "hitsPerPage": count,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director",
                    "actors", "genre", "image", "votes", "plot", "rating"
                ],
                # Rely on customRanking including "desc(votes)"
            })

            # Sort locally just in case
            top_movies = sorted(search_result["hits"], key=lambda m: m.get("votes", 0), reverse=True)

            return top_movies

        except Exception as e:
            logger.error(f"Error getting top {count} movies from Algolia: {e}", exc_info=True)
            return []

    async def get_all_movies(self) -> List[Dict[str, Any]]:
        """Get all movies from Algolia movies index."""
        try:
            all_movies = []
            # Iterate through all objects, handling pagination internally with browse_objects
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

    # on_message is set up via decorator inside __init__
    # Slash command handlers via self.tree.command
    # Modal submission via on_submit method in Modal class
    # Button interactions via on_interaction listener

    bot.client.add_listener(bot.on_interaction) # Manually add the interaction listener


    bot.run()


if __name__ == "__main__":
    if not os.path.exists(".env") and not os.environ.get('DISCORD_TOKEN'):
         logger.error("No .env file found, and DISCORD_TOKEN is not set in environment variables.")
         logger.error("Please create a .env file or set environment variables.")
         exit(1)

    main()