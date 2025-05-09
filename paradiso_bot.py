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
from typing import List, Dict, Any, Optional, Union

import discord
from discord import app_commands
from dotenv import load_dotenv
import requests
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
            
            # Sync commands
            await self.tree.sync()
            logger.info("Commands synced")

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
        self.client.run(self.discord_token)

    # Command handlers
    async def cmd_add(self, interaction: discord.Interaction, title: str):
        """Add a movie to the voting queue."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Search for the movie
            movie_data = await self.search_movie(title)
            
            if not movie_data:
                await interaction.followup.send(f"❌ Could not find movie: '{title}'. Please check the title and try again.")
                return
            
            # Check if movie already exists in Algolia
            search_result = self.movies_index.search("", {
                "filters": f"objectID:{movie_data['id']}"
            })
            
            if search_result["nbHits"] > 0:
                await interaction.followup.send(f"❌ '{movie_data['title']}' is already in the voting queue!")
                return
            
            # Add the movie to Algolia
            movie_obj = await self.add_movie_to_algolia(movie_data, str(interaction.user.id))
            
            # Create embed for movie
            embed = discord.Embed(
                title=f"🎬 Added: {movie_obj['title']} ({movie_obj['year'] if movie_obj['year'] else 'N/A'})",
                description=movie_obj["plot"] if len(movie_obj["plot"]) < 300 else movie_obj["plot"][:297] + "...",
                color=0x00ff00
            )
            
            if movie_obj["director"]:
                embed.add_field(name="Director", value=movie_obj["director"], inline=True)
            
            if movie_obj["actors"]:
                embed.add_field(name="Starring", value=", ".join(movie_obj["actors"][:3]), inline=True)
            
            if movie_obj["imdbRating"]:
                embed.add_field(name="Rating", value=f"⭐ {movie_obj['imdbRating']}/10", inline=True)
            
            if movie_obj["poster"]:
                embed.set_thumbnail(url=movie_obj["poster"])
            
            embed.set_footer(text=f"Added by {interaction.user.display_name}")
            
            await interaction.followup.send(embed=embed)
            
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
                await interaction.followup.send(f"❌ Could not find '{title}' in the voting queue. Use /movies to see available movies.")
                return
            
            # Record the vote
            success = await self.vote_for_movie(movie["objectID"], str(interaction.user.id))
            
            if not success:
                await interaction.followup.send(f"❌ You have already voted for '{movie['title']}'!")
                return
            
            # Update movie information
            updated_movie = await self.get_movie_by_id(movie["objectID"])
            
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
                
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
                
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
                    name=f"{i+1}. {title}{year}",
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

    async def cmd_related(self, interaction: discord.Interaction, query: str):
        """Find related movies based on search terms."""
        await interaction.response.defer()
        
        try:
            # Search for the initial movie
            initial_results = self.movies_index.search(query, {
                "hitsPerPage": 1,
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director", 
                    "actors", "genre", "poster", "votes"
                ]
            })
            
            if initial_results["nbHits"] == 0:
                await interaction.followup.send(f"No movies found matching '{query}'.")
                return
            
            # Get the top result
            top_movie = initial_results["hits"][0]
            
            # Build a query for related movies
            related_query = ""
            if top_movie.get("genre") and len(top_movie["genre"]) > 0:
                related_query += " ".join(top_movie["genre"][:2])
            
            if top_movie.get("director"):
                related_query += f" {top_movie['director']}"
            
            if top_movie.get("actors") and len(top_movie["actors"]) > 0:
                related_query += f" {' '.join(top_movie['actors'][:2])}"
            
            # Search for related movies
            related_results = self.movies_index.search(related_query, {
                "hitsPerPage": 5,
                "filters": f"NOT objectID:{top_movie['objectID']}",  # Exclude the original movie
                "attributesToRetrieve": [
                    "objectID", "title", "year", "director", 
                    "actors", "genre", "poster", "votes", "plot"
                ]
            })
            
            # Create an embed for related movies
            embed = discord.Embed(
                title=f"🎬 Movies Related to '{top_movie['title']}'",
                description=f"Based on genre, director, and actors:",
                color=0x03a9f4
            )
            
            # First show the reference movie
            ref_year = f" ({top_movie.get('year')})" if top_movie.get("year") else ""
            embed.add_field(
                name=f"📌 {top_movie['title']}{ref_year}",
                value=f"**Genre**: {', '.join(top_movie.get('genre', []))}\n"
                      f"**Director**: {top_movie.get('director', 'Unknown')}\n"
                      f"**Votes**: {top_movie.get('votes', 0)}",
                inline=False
            )
            
            # Add a separator
            embed.add_field(name="Related Movies", value="─────────────", inline=False)
            
            # Add related movies
            if related_results["nbHits"] == 0:
                embed.add_field(name="No Related Movies", value="Couldn't find any related movies.", inline=False)
            else:
                for i, movie in enumerate(related_results["hits"]):
                    title = movie.get("title", "Unknown")
                    year = f" ({movie.get('year')})" if movie.get("year") else ""
                    votes = movie.get("votes", 0)
                    
                    # Find common elements
                    common_genres = set(movie.get("genre", [])) & set(top_movie.get("genre", []))
                    common_actors = set(movie.get("actors", [])) & set(top_movie.get("actors", []))
                    
                    relation_points = []
                    if common_genres:
                        relation_points.append(f"**Common Genres**: {', '.join(common_genres)}")
                    if movie.get("director") == top_movie.get("director"):
                        relation_points.append(f"**Same Director**: {movie.get('director')}")
                    if common_actors:
                        relation_points.append(f"**Common Actors**: {', '.join(common_actors)}")
                    relation_points.append(f"**Votes**: {votes}")
                    
                    embed.add_field(
                        name=f"{i+1}. {title}{year}",
                        value="\n".join(relation_points),
                        inline=False
                    )
            
            # Add thumbnail from reference movie if available
            if top_movie.get("poster"):
                embed.set_thumbnail(url=top_movie["poster"])
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in /related command: {e}")
            await interaction.followup.send(f"An error occurred while finding related movies: {str(e)}")

    async def cmd_top(self, interaction: discord.Interaction, count: int = 5):
        """Show the top voted movies."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Limit count to reasonable values
            count = max(1, min(10, count))
            
            # Get top voted movies
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
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in top command: {e}")
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
                "name": "/movies",
                "description": "List all movies in the voting queue"
            },
            {
                "name": "/add [title]",
                "description": "Add a movie to the voting queue"
            },
            {
                "name": "/vote [title]",
                "description": "Vote for a movie in the queue"
            },
            {
                "name": "/search [query]",
                "description": "Search for movies by title, actor, director, etc."
            },
            {
                "name": "/related [query]",
                "description": "Find movies related to a specific movie"
            },
            {
                "name": "/top [count]",
                "description": "Show the top voted movies (default: top 5)"
            }
        ]
        
        for cmd in commands:
            embed.add_field(name=cmd["name"], value=cmd["description"], inline=False)
        
        embed.set_footer(text="Happy voting! 🎬")
        
        await interaction.response.send_message(embed=embed)

    # Helper Methods
    async def search_movie(self, title: str) -> Optional[Dict[str, Any]]:
        """Search for a movie using external API or database."""
        try:
            # First try to find in our own database
            existing_movie = await self.find_movie_by_title(title)
            if existing_movie:
                return {
                    "id": existing_movie["objectID"],
                    "title": existing_movie["title"],
                    "original_title": existing_movie.get("originalTitle", existing_movie["title"]),
                    "year": existing_movie.get("year"),
                    "director": existing_movie.get("director", "Unknown"),
                    "actors": existing_movie.get("actors", []),
                    "genre": existing_movie.get("genre", []),
                    "plot": existing_movie.get("plot", ""),
                    "poster": existing_movie.get("poster"),
                    "imdb_rating": existing_movie.get("imdbRating"),
                    "imdb_id": existing_movie.get("imdbID"),
                    "tmdb_id": existing_movie.get("tmdbID"),
                    "source": "algolia"
                }
            
            # For MVP, we'll use a simple implementation
            # In a real implementation, we'd use OMDB/TMDB/etc.
            # But we'll just create a basic entry for testing
            return {
                "id": f"movie_{int(time.time())}",
                "title": title,
                "original_title": title,
                "year": None,
                "director": "Unknown",
                "actors": [],
                "genre": [],
                "plot": "No plot available. This is a minimal movie entry created by the bot.",
                "poster": None,
                "imdb_rating": None,
                "imdb_id": None,
                "tmdb_id": None,
                "source": "manual"
            }
        except Exception as e:
            logger.error(f"Error searching for movie: {e}")
            return None

    async def add_movie_to_algolia(self, movie_data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Add a movie to Algolia index."""
        try:
            # Format the movie data for Algolia
            movie_obj = {
                "objectID": movie_data["id"],
                "title": movie_data["title"],
                "originalTitle": movie_data["original_title"],
                "year": movie_data["year"],
                "director": movie_data["director"],
                "actors": movie_data["actors"],
                "genre": movie_data["genre"],
                "plot": movie_data["plot"],
                "poster": movie_data["poster"],
                "imdbRating": movie_data["imdb_rating"],
                "imdbID": movie_data["imdb_id"],
                "tmdbID": movie_data["tmdb_id"],
                "votes": 0,
                "addedDate": int(time.time()),
                "addedBy": self.generate_user_token(user_id),
                "source": movie_data["source"]
            }
            
            # Save to Algolia
            self.movies_index.save_object(movie_obj)
            return movie_obj
        except Exception as e:
            logger.error(f"Error adding movie to Algolia: {e}")
            raise

    async def vote_for_movie(self, movie_id: str, user_id: str) -> bool:
        """Vote for a movie in Algolia."""
        try:
            user_token = self.generate_user_token(user_id)
            
            # Check if user already voted for this movie
            search_result = self.votes_index.search("", {
                "filters": f"userToken:{user_token} AND movieId:{movie_id}"
            })
            
            if search_result["nbHits"] > 0:
                return False  # User already voted
            
            # Record the vote
            self.votes_index.save_object({
                "objectID": f"{user_token}_{movie_id}",
                "userToken": user_token,
                "movieId": movie_id,
                "timestamp": int(time.time())
            })
            
            # Increment the movie's vote count
            self.movies_index.partial_update_object({
                "objectID": movie_id,
                "votes": {
                    "_operation": "Increment",
                    "value": 1
                }
            })
            
            return True
        except Exception as e:
            logger.error(f"Error voting for movie: {e}")
            return False

    async def get_movie_by_id(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Get a movie by its ID from Algolia."""
        try:
            return self.movies_index.get_object(movie_id)
        except Exception as e:
            logger.error(f"Error getting movie by ID: {e}")
            return None

    async def find_movie_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a movie by title in Algolia."""
        try:
            search_result = self.movies_index.search(title, {
                "hitsPerPage": 5
            })
            
            if search_result["nbHits"] == 0:
                return None
            
            # Try to find an exact match
            for hit in search_result["hits"]:
                if hit["title"].lower() == title.lower():
                    return hit
            
            # Return the first result if no exact match
            return search_result["hits"][0]
        except Exception as e:
            logger.error(f"Error finding movie by title: {e}")
            return None

    async def get_top_movies(self, count: int = 5) -> List[Dict[str, Any]]:
        """Get the top voted movies from Algolia."""
        try:
            search_result = self.movies_index.search("", {
                "filters": "votes > 0",
                "hitsPerPage": count,
                "sortCriteria": ["votes:desc"]
            })
            
            return search_result["hits"]
        except Exception as e:
            logger.error(f"Error getting top movies: {e}")
            return []

    async def get_all_movies(self) -> List[Dict[str, Any]]:
        """Get all movies from Algolia."""
        try:
            search_result = self.movies_index.search("", {
                "hitsPerPage": 100
            })
            
            return search_result["hits"]
        except Exception as e:
            logger.error(f"Error getting all movies: {e}")
            return []

    def generate_user_token(self, user_id: str) -> str:
        """Generate a user token for Algolia based on Discord user ID."""
        return f"discord_{user_id}"


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