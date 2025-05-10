#!/usr/bin/env python
"""
Paradiso Discord Bot (Algolia v3 - Complete)

A Discord bot for the Paradiso movie voting system with full feature set:
- Movie addition with exact matching and confirmation
- Voting system with proper error handling
- Random movie selection with history tracking
- Full recommendation support (related and visual similarity)
- All slash commands and text commands
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
from algoliasearch.recommend_client import RecommendClient
from discord import app_commands
from dotenv import load_dotenv

# Import utilities
from utils.algolia_utils import (
    add_movie_to_algolia, vote_for_movie, find_movie_by_title, search_movies_for_vote, get_top_movies, get_all_movies,
    generate_user_token, _check_movie_exists, get_random_movie, get_recommendations
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
        self.recommend_client = RecommendClient.create(algolia_app_id, algolia_api_key)

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
        async def on_reaction_add(reaction, user):
            """Handle emoji reactions to movie messages."""
            if user == self.client.user:
                return
            
            # Check if this is a movie message (you might want to track these)
            # For now, we'll assume any message with embeds containing movie info
            if reaction.message.embeds:
                embed = reaction.message.embeds[0]
                
                # Extract movie ID from embed footer (if present)
                if embed.footer and "ID: " in embed.footer.text:
                    try:
                        movie_id = embed.footer.text.split("ID: ")[1].split(" ")[0]
                        
                        # Map Discord emoji to our vote types
                        emoji_mapping = {
                            "👍": "thumb_up",
                            "👎": "thumb_down",
                            "❤️": "love",
                            "😂": "laugh",
                            "😮": "surprise",
                            "😢": "sad",
                            "💩": "poop"
                        }
                        
                        emoji_str = str(reaction.emoji)
                        if emoji_str in emoji_mapping:
                            emoji_type = emoji_mapping[emoji_str]
                            
                            # Record the vote
                            success, result = await vote_for_movie(
                                self.algolia_client,
                                self.algolia_movies_index_name,
                                self.algolia_votes_index_name,
                                movie_id,
                                str(user.id),
                                emoji_type
                            )
                            
                            if success:
                                # Optionally notify the user
                                pass
                    except Exception as e:
                        logger.error(f"Error processing emoji reaction: {e}")

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

    async def _handle_info_command(self, channel: Union[discord.TextChannel, discord.DMChannel], query: str):
        try:
            movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name, query)
            if not movie:
                await channel.send(f"Could not find '{query}'. Use `search [query]`.")
                return
            await send_detailed_movie_embed(channel, movie)
        except Exception as e:
            logger.error(f"Error in manual info command: {e}", exc_info=True)
            await channel.send(f"An error occurred: {str(e)}")

    async def _start_add_movie_flow(self, message: discord.Message, title: str):
        user_id = message.author.id
        if user_id in self.add_movie_flows:
            await message.channel.send("You are already adding a movie. Complete or type 'cancel'.")
            return

        try:
            # V3 API: Simple index.search call
            index = self.algolia_client.init_index(self.algolia_movies_index_name)
            search_response = index.search(title, {
                'hitsPerPage': 3,
                'attributesToRetrieve': ['objectID', 'title', 'year', 'votes']
            })

            dm_channel = await message.author.create_dm()
            if search_response.get('nbHits', 0) > 0:
                embed = discord.Embed(title=f"Movies Found Matching '{title}'", color=0xffa500)
                for i, hit in enumerate(search_response.get('hits', [])):
                    embed.add_field(name=f"{i + 1}. {hit.get('title', 'Unknown')} ({hit.get('year', 'N/A')})",
                                    value=f"Votes: {hit.get('votes', 0)}. Reply 'add new' to add yours.", inline=False)
                self.add_movie_flows[user_id] = {'title': title, 'stage': 'await_add_new_confirmation',
                                                 'channel': dm_channel, 'original_channel': message.channel}
                await dm_channel.send(embed=embed)
                if not isinstance(message.channel, discord.DMChannel):
                    await message.channel.send(f"📬 Found matches for '{title}'. Check DMs ({dm_channel.mention}).")
            else:
                self.add_movie_flows[user_id] = {'title': title, 'year': None, 'stage': 'year', 'channel': dm_channel,
                                                 'original_channel': message.channel}
                await dm_channel.send(
                    f"📽️ No matches for '{title}'. Let's add it!\nYear released? ('unknown' or 'cancel')")
                if not isinstance(message.channel, discord.DMChannel):
                    await message.channel.send(
                        f"📬 No matches for '{title}'. Check DMs ({dm_channel.mention}) to add details.")
        except Exception as e:
            logger.error(f"Error in text add flow start: {e}", exc_info=True)
            await message.channel.send("Error searching. Try again.")
            if user_id in self.add_movie_flows: del self.add_movie_flows[user_id]

    async def _handle_add_movie_flow(self, message: discord.Message):
        user_id = message.author.id
        flow = self.add_movie_flows.get(user_id)
        if not flow or message.channel.id != flow['channel'].id: return
        response = message.content.strip()

        if response.lower() == 'cancel':
            await message.channel.send("Movie addition cancelled.")
            if flow.get('original_channel') and not isinstance(flow['original_channel'], discord.DMChannel):
                try:
                    await flow['original_channel'].send(f"Addition of '{flow.get('title', 'movie')}' cancelled.")
                except:
                    pass
            del self.add_movie_flows[user_id]
            return

        if flow['stage'] == 'await_add_new_confirmation':
            if response.lower() == 'add new':
                flow['stage'] = 'year'
                await message.channel.send(
                    f"Adding new movie: '{flow['title']}'\nYear released? ('unknown' or 'cancel')")
            else:
                await message.channel.send(f"Reply 'add new' to add '{flow['title']}' or 'cancel' to stop.")

        elif flow['stage'] == 'year':
            if response.lower() == 'unknown':
                flow['year'] = None
            else:
                try:
                    year = int(response)
                    if 1850 <= year <= 2030:
                        flow['year'] = year
                    else:
                        await message.channel.send("Year must be between 1850 and 2030.")
                        return
                except ValueError:
                    await message.channel.send("Enter a valid year number.")
                    return
            flow['stage'] = 'director'
            await message.channel.send("Director? ('unknown' or 'cancel')")

        elif flow['stage'] == 'director':
            flow['director'] = response if response.lower() != 'unknown' else None
            flow['stage'] = 'actors'
            await message.channel.send("Actors? (comma-separated or 'unknown')")

        elif flow['stage'] == 'actors':
            if response.lower() == 'unknown':
                flow['actors'] = []
            else:
                flow['actors'] = [a.strip() for a in response.split(',') if a.strip()]
            flow['stage'] = 'genre'
            await message.channel.send("Genres? (comma-separated or 'unknown')")

        elif flow['stage'] == 'genre':
            if response.lower() == 'unknown':
                flow['genre'] = []
            else:
                flow['genre'] = [g.strip() for g in response.split(',') if g.strip()]
            flow['stage'] = 'confirm_manual'

            # Show summary for confirmation
            embed = discord.Embed(title=f"Confirm adding: {flow['title']}", color=0x00ff00)
            embed.add_field(name="Year", value=flow.get('year') or 'Unknown', inline=True)
            embed.add_field(name="Director", value=flow.get('director') or 'Unknown', inline=True)
            embed.add_field(name="Actors", value=', '.join(flow.get('actors', [])) or 'Unknown', inline=False)
            embed.add_field(name="Genres", value=', '.join(flow.get('genre', [])) or 'Unknown', inline=False)
            await message.channel.send(embed=embed)
            await message.channel.send("Add this movie? ('yes' or 'no')")

        elif flow['stage'] == 'confirm_manual':
            if response.lower() in ['yes', 'y']:
                movie_data = {
                    "objectID": f"manual_{int(time.time())}_{random.randint(0, 999)}",
                    "title": flow.get('title', 'Unknown Movie'),
                    "originalTitle": flow.get('title', 'Unknown Movie'),
                    "year": flow['year'],
                    "director": flow.get('director') or "Unknown",
                    "actors": flow.get('actors', []),
                    "genre": flow.get('genre', []),
                    "plot": f"Added manually by {message.author.display_name}.",
                    "image": None,
                    "rating": None,
                    "imdbID": None,
                    "tmdbID": None,
                    "source": "manual",
                    "votes": 0,
                    "addedDate": int(time.time()),
                    "addedBy": generate_user_token(str(message.author.id)),
                }
                await self._add_movie_from_flow(user_id, movie_data, message.author, flow.get('original_channel'))
            elif response.lower() in ['no', 'n']:
                await message.channel.send("Movie addition cancelled.")
                if flow.get('original_channel') and not isinstance(flow['original_channel'], discord.DMChannel):
                    try:
                        await flow['original_channel'].send(f"Addition of '{flow['title']}' cancelled.")
                    except:
                        pass
                del self.add_movie_flows[user_id]
            else:
                await message.channel.send("Please respond with 'yes' or 'no'.")

    async def _add_movie_from_flow(self, user_id: int, movie_data: Dict[str, Any], author: discord.User,
                                   original_channel: Optional[discord.TextChannel]):
        try:
            existing_movie = await _check_movie_exists(self.algolia_client, self.algolia_movies_index_name,
                                                       movie_data['title'], movie_data.get('year'))
            if existing_movie:
                await self.add_movie_flows[user_id]['channel'].send(
                    f"❌ Similar movie exists: '{existing_movie['title']}' ({existing_movie.get('year', 'N/A')})")
                if original_channel and not isinstance(original_channel, discord.DMChannel):
                    try:
                        await original_channel.send(
                            f"❌ Similar movie exists: '{existing_movie['title']}' ({existing_movie.get('year', 'N/A')})")
                    except:
                        pass
                del self.add_movie_flows[user_id]
                return

            await add_movie_to_algolia(self.algolia_client, self.algolia_movies_index_name, movie_data)
            logger.info(f"Added movie via text flow: {movie_data.get('title')} ({movie_data.get('objectID')})")
            embed = format_movie_embed(movie_data, title_prefix="🎬 Added: ")
            embed.set_footer(text=f"Added by {author.display_name}")
            await self.add_movie_flows[user_id]['channel'].send("✅ Movie added!", embed=embed)
            if original_channel and original_channel != self.add_movie_flows[user_id]['channel'] and not isinstance(
                    original_channel, discord.DMChannel):
                try:
                    await original_channel.send(f"✅ Movie '{movie_data['title']}' added!")
                except:
                    pass
        except Exception as e:
            logger.error(f"Error in _add_movie_from_flow: {e}", exc_info=True)
            await self.add_movie_flows[user_id]['channel'].send(f"❌ Error adding movie: {str(e)}")
        finally:
            if user_id in self.add_movie_flows:
                del self.add_movie_flows[user_id]

    async def _handle_vote_command(self, channel: Union[discord.TextChannel, discord.DMChannel], author: discord.User,
                                   title: str):
        try:
            search_results_dict = await search_movies_for_vote(self.algolia_client, self.algolia_movies_index_name,
                                                               title)

            if search_results_dict["nbHits"] == 0:
                await channel.send(f"❌ No movies matching '{title}'. Use `movies` or `search`.")
                return

            hits = search_results_dict["hits"]
            if search_results_dict["nbHits"] == 1:
                movie_to_vote = hits[0]
                await channel.send(f"Found '{movie_to_vote['title']}'. Voting...")
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name,
                                                       self.algolia_votes_index_name, movie_to_vote["objectID"],
                                                       str(author.id))
                if success:
                    embed = format_movie_embed(result,
                                               title_prefix=f"✅ Vote recorded for: {result['title']}")
                    embed.description = f"This movie now has {result['votes']} vote(s)!"
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"❌ {result}" if isinstance(result, str) else "❌ Error voting.")
            else:
                choices = hits[:5]
                embed = discord.Embed(title=f"Multiple matches for '{title}'", color=0xffa500)
                for i, movie in enumerate(choices):
                    embed.add_field(name=f"{i + 1}. {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})",
                                    value=f"Votes: {movie.get('votes', 0)}. Reply # or 'cancel'.", inline=False)
                dm_channel = await author.create_dm()
                self.pending_votes[author.id] = {'channel': dm_channel, 'choices': choices, 'timestamp': time.time()}
                await dm_channel.send(embed=embed)
                if not isinstance(channel, discord.DMChannel):
                    await channel.send(f"Multiple matches for '{title}'. Check DMs ({dm_channel.mention}).")
        except Exception as e:
            logger.error(f"Error in manual vote command for '{title}': {e}", exc_info=True)
            await channel.send(f"❌ Error searching: {str(e)}")

    async def _handle_movies_command(self, channel: Union[discord.TextChannel, discord.DMChannel]):
        try:
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, 10)
            if not top_movies:
                await channel.send("No movies voted yet! Use `add [title]` or `/add`.")
                return
            embed = discord.Embed(title="🎬 Paradiso Movie Night Voting (Top 10)", color=0x03a9f4)
            for i, movie in enumerate(top_movies):
                medal = "🥇🥈🥉"[i] if i < 3 else f"{i + 1}."
                embed.add_field(name=f"{medal} {movie.get('title', 'N/A')} ({movie.get('year', 'N/A')})",
                                value=f"Votes: {movie.get('votes', 0)} | Rating: {movie.get('rating', 'N/A')}/10",
                                inline=False)
            embed.set_footer(text="Use 'vote [title]' or /vote. /movies in server for full list.")
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual movies cmd: {e}", exc_info=True)
            await channel.send("Error getting movies.")

    async def _handle_top_command(self, channel: Union[discord.TextChannel, discord.DMChannel], count: int = 5):
        try:
            count = max(1, min(10, count))
            top_movies = await get_top_movies(self.algolia_client, self.algolia_movies_index_name, count)
            if not top_movies:
                await channel.send("❌ No movies voted yet!")
                return
            embed = discord.Embed(title=f"🏆 Top {len(top_movies)} Voted Movies", color=0x00ff00)
            for i, movie in enumerate(top_movies):
                medal = "🥇🥈🥉"[i] if i < 3 else f"{i + 1}."
                details = [f"**Votes**: {movie.get('votes', 0)}", f"**Year**: {movie.get('year', 'N/A')}"]
                if movie.get("rating") is not None: details.append(f"**Rating**: ⭐ {movie['rating']}/10")
                embed.add_field(name=f"{medal} {movie.get('title', 'N/A')}", value="\n".join(details), inline=False)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in manual top cmd: {e}", exc_info=True)
            await channel.send(f"❌ Error: {str(e)}")

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

            embed = format_movie_embed(random_movie, title_prefix="🎲")
            embed.add_field(name="Votes", value=str(random_movie.get("votes", 0)), inline=True)
            if random_movie.get("votes", 0) == 0:
                embed.set_footer(text="This movie has no votes yet! Why not be the first?")
            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /random command: {e}", exc_info=True)
            await channel.send(f"❌ An error occurred while fetching a random movie: {str(e)}")

    async def _handle_vote_selection_response(self, message: discord.Message, flow_state: Dict[str, Any]):
        user_id = message.author.id
        response = message.content.strip()

        # Check timeout
        if time.time() - flow_state['timestamp'] > 60:
            await message.channel.send("Vote selection timed out. Please try again.")
            del self.pending_votes[user_id]
            return

        if response.lower() == 'cancel':
            await message.channel.send("Vote cancelled.")
            del self.pending_votes[user_id]
            return

        try:
            selection = int(response)
            choices = flow_state['choices']
            if 1 <= selection <= len(choices):
                chosen_movie = choices[selection - 1]
                await message.channel.send(f"Voting for '{chosen_movie['title']}'...")
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name,
                                                       self.algolia_votes_index_name, chosen_movie["objectID"],
                                                       str(user_id))
                if success:
                    embed = format_movie_embed(result,
                                               title_prefix=f"✅ Vote recorded for: {result['title']}")
                    embed.description = f"This movie now has {result['votes']} vote(s)!"
                    await message.channel.send(embed=embed)
                    # Notify original channel if different
                    if flow_state.get('original_channel'):
                        try:
                            await flow_state['original_channel'].send(f"✅ Vote recorded for '{result['title']}'!")
                        except:
                            pass
                else:
                    await message.channel.send(f"❌ {result}" if isinstance(result, str) else "❌ Error voting.")
            else:
                await message.channel.send(f"Invalid selection. Enter 1-{len(choices)} or 'cancel'.")
                return
        except ValueError:
            await message.channel.send(f"Invalid input. Enter # or 'cancel'.")
            return
        except Exception as e:
            logger.error(f"Error in text vote selection for user {user_id}: {e}", exc_info=True)
            await message.channel.send(f"❌ Error processing vote: {str(e)}")
        finally:
            if user_id in self.pending_votes:
                del self.pending_votes[user_id]

    # --- Slash Command Handlers ---
    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer(thinking=True)
        user_id = interaction.user.id
        try:
            search_results_dict = await search_movies_for_vote(self.algolia_client, self.algolia_movies_index_name, title)
            if search_results_dict["nbHits"] == 0:
                await interaction.followup.send(f"❌ No movies matching '{title}'. Use `/movies` or `/search`.")
                return

            hits = search_results_dict["hits"]
            if search_results_dict["nbHits"] == 1:
                movie_to_vote = hits[0]
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name,
                                                    self.algolia_votes_index_name, movie_to_vote["objectID"],
                                                    str(user_id), emoji_type="thumb_up")  # Default to thumb_up
                if success:
                    embed = format_movie_embed(result, title_prefix=f"✅ Vote recorded for: {result['title']}")
                    embed.description = f"Your 👍 vote has been recorded!"
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(f"❌ {result}" if isinstance(result, str) else "❌ Error voting.")
            else:
                choices = hits[:5]
                embed = discord.Embed(title=f"Multiple movies for '{title}'",
                                    description="Select the movie to vote for:", color=0xffa500)
                choice_list_desc = []
                for i, m in enumerate(choices):
                    voted = m.get('voted', {})
                    total_votes = sum(len(users) for users in voted.values())
                    choice_list_desc.append(f"{i + 1}. {m.get('title', 'N/A')} ({m.get('year', 'N/A')}) - Votes: 👍 {total_votes}")
                embed.add_field(name="Choices", value="\n".join(choice_list_desc), inline=False)
                view = VoteSelectionView(self, user_id, choices)
                message = await interaction.followup.send(embed=embed, view=view)
                self.vote_messages[message.id] = {'user_id': user_id, 'choices': choices}
                view.message = message
        except Exception as e:
            logger.error(f"Error in /vote for '{title}': {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error searching: {str(e)}")

    async def cmd_vote(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer(thinking=True)
        user_id = interaction.user.id
        try:
            search_results_dict = await search_movies_for_vote(self.algolia_client, self.algolia_movies_index_name,
                                                               title)
            if search_results_dict["nbHits"] == 0:
                await interaction.followup.send(f"❌ No movies matching '{title}'. Use `/movies` or `/search`.")
                return

            hits = search_results_dict["hits"]
            if search_results_dict["nbHits"] == 1:
                movie_to_vote = hits[0]
                success, result = await vote_for_movie(self.algolia_client, self.algolia_movies_index_name,
                                                       self.algolia_votes_index_name, movie_to_vote["objectID"],
                                                       str(user_id))
                if success:
                    embed = format_movie_embed(result, title_prefix=f"✅ Vote recorded for: {result['title']}")
                    embed.description = f"This movie now has {result['votes']} vote(s)!"
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(f"❌ {result}" if isinstance(result, str) else "❌ Error voting.")
            else:
                choices = hits[:5]
                embed = discord.Embed(title=f"Multiple movies for '{title}'",
                                      description="Select the movie to vote for:", color=0xffa500)
                choice_list_desc = [
                    f"{i + 1}. {m.get('title', 'N/A')} ({m.get('year', 'N/A')}) - Votes: {m.get('votes', 0)}" for i, m
                    in enumerate(choices)]
                embed.add_field(name="Choices", value="\n".join(choice_list_desc), inline=False)
                view = VoteSelectionView(self, user_id, choices)
                message = await interaction.followup.send(embed=embed, view=view)
                self.vote_messages[message.id] = {'user_id': user_id, 'choices': choices}
                view.message = message
        except Exception as e:
            logger.error(f"Error in /vote for '{title}': {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error searching: {str(e)}")

    async def cmd_movies(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            all_movies = await get_all_movies(self.algolia_client, self.algolia_movies_index_name)
            if not all_movies:
                await interaction.followup.send("No movies added yet! Use `/add`.")
                return

            movies_per_page, detailed_count = 10, 5
            view = MoviesPaginationView(self, interaction.user.id, all_movies, movies_per_page, detailed_count)
            embed = await self._get_movies_page_embed(all_movies, view.current_page, movies_per_page, detailed_count,
                                                      view.total_pages)
            await view.update_buttons()
            message = await interaction.followup.send(embed=embed, view=view)
            view.message = message
        except Exception as e:
            logger.error(f"Error in /movies: {e}", exc_info=True)
            await interaction.followup.send("Error getting movies.")

    async def _get_movies_page_embed(self, all_movies: List[Dict[str, Any]], current_page: int, movies_per_page: int,
                                     detailed_count: int, total_pages: int) -> discord.Embed:
        start_index = current_page * movies_per_page
        end_index = start_index + movies_per_page
        page_movies = all_movies[start_index:end_index]
        embed = discord.Embed(title=f"🎬 Paradiso Movies (Page {current_page + 1}/{total_pages})", color=0x03a9f4)
        for i, movie in enumerate(page_movies):
            title = movie.get("title", "Unknown")
            year_str = f" ({movie.get('year')})" if movie.get('year') else ""
            votes = movie.get("votes", 0)
            rating = movie.get("rating")
            plot = movie.get("plot", "No description.")

            name = f"{start_index + i + 1}. {title}{year_str}"
            value = f"**Votes**: {votes} | **Rating**: {f'⭐ {rating}/10' if rating else 'N/A'}"
            if i < detailed_count:
                if plot and len(plot) > 100: plot = plot[:97] + "..."
                value += f"\n*Plot*: {plot if plot else 'N/A'}"
            embed.add_field(name=name, value=value, inline=False)
        if page_movies and page_movies[0].get("image") and current_page == 0:
            embed.set_thumbnail(url=page_movies[0]["image"])
        embed.set_footer(text=f"Total movies: {len(all_movies)}")
        return embed

    async def cmd_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        try:
            main_query, filter_string = parse_algolia_filters(query)
            logger.info(f"Parsed Search: Query='{main_query}', Filters='{filter_string}'")

            # V3 API: Simple index.search call with filters
            index = self.algolia_client.init_index(self.algolia_movies_index_name)
            search_params = {
                'hitsPerPage': 5,
                'attributesToRetrieve': ['*', 'objectID'],
                'attributesToHighlight': ['title', 'director', 'actors', 'plot', 'genre'],
                'attributesToSnippet': ['plot:20']
            }

            if filter_string:
                search_params['filters'] = filter_string

            search_response = index.search(main_query, search_params)

            if search_response.get('nbHits', 0) == 0:
                await interaction.followup.send(f"No results found for '{query}'.")
                return

            await send_search_results_embed(interaction.followup, query, search_response.get('hits', []),
                                            search_response.get('nbHits', 0))

        except Exception as e:
            logger.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error searching: {str(e)}")

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

            # Get recommendations using Algolia's related-products model
            recommendations = await get_recommendations(
                self.algolia_client,  # Search client
                self.recommend_client,  # Recommend client
                self.algolia_movies_index_name,
                reference_movie['objectID'],
                model="related",
                count=count
            )

            if not recommendations:
                await interaction.followup.send(f"❌ No recommendations found for '{reference_movie['title']}'.")
                return

            # Create recommendation embed
            embed = discord.Embed(
                title=f"🎬 Movies like '{reference_movie['title']}'",
                description=f"Found {len(recommendations)} recommendations based on content and user engagement",
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
                if movie.get('rating'):
                    value_parts.append(f"Rating: ⭐{movie['rating']}/10")

                embed.add_field(
                    name=f"{i + 1}. {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})",
                    value="\n".join(value_parts) if value_parts else "No additional info",
                    inline=False
                )

            if reference_movie.get('image'):
                embed.set_thumbnail(url=reference_movie['image'])

            embed.set_footer(text=f"Recommendations powered by Algolia • Use /vote to vote for these movies")
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

            # Get visually similar movies using Algolia's looking-similar model
            similar_movies = await get_recommendations(
                self.algolia_client,  # Search client
                self.recommend_client,  # Recommend client
                self.algolia_movies_index_name,
                reference_movie['objectID'],
                model="similar",
                count=count
            )

            if not similar_movies:
                await interaction.followup.send(f"❌ No visually similar movies found for '{reference_movie['title']}'.")
                return

            # Create visual similarity embed
            embed = discord.Embed(
                title=f"🎨 Movies visually similar to '{reference_movie['title']}'",
                description=f"Found {len(similar_movies)} visually similar movies",
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
                value_parts = []
                if movie.get('image'):
                    value_parts.append(f"[View Poster]({movie['image']})")
                if movie.get('votes') is not None:
                    value_parts.append(f"Votes: {movie['votes']}")
                if movie.get('genre'):
                    value_parts.append(f"Genre: {', '.join(movie['genre'][:2])}")

                embed.add_field(
                    name=f"{i + 1}. {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})",
                    value="\n".join(value_parts) if value_parts else "No additional info",
                    inline=False
                )

            embed.set_footer(text=f"Visual similarity powered by Algolia • Use /vote to vote for these movies")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /lookalike: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error finding visually similar movies: {str(e)}")

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

    async def cmd_info(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        try:
            movie = await find_movie_by_title(self.algolia_client, self.algolia_movies_index_name, query)
            if not movie:
                await interaction.followup.send(f"Could not find '{query}'. Use `/search`.")
                return
            await send_detailed_movie_embed(interaction.followup, movie)
        except Exception as e:
            logger.error(f"Error in /info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error fetching info: {str(e)}")

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

            embed = format_movie_embed(random_movie, title_prefix="🎲")
            embed.add_field(name="Votes", value=str(random_movie.get("votes", 0)), inline=True)
            if random_movie.get("votes", 0) == 0:
                embed.set_footer(text="This movie has no votes yet! Why not be the first?")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in /random command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while fetching a random movie: {str(e)}")

    async def cmd_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="👋 Paradiso Bot Help", color=0x03a9f4)
        embed.add_field(name="Basic Commands", value="`/add` `/vote` `/movies` `/top` `/random`", inline=False)
        embed.add_field(name="Search & Discover", value="`/search` `/info` `/recommend` `/lookalike`", inline=False)
        embed.add_field(name="How Recommendations Work",
                        value="• `/recommend` - Similar movies based on content & user behavior\n• `/lookalike` - Visually similar movies based on posters",
                        inline=False)
        embed.set_footer(text="Use /help <command> for detailed help on a specific command")
        await interaction.response.send_message(embed=embed, ephemeral=True)


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