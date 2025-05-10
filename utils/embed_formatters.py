"""
Embed formatter utilities for Paradiso Discord Bot
Provides consistent formatting for embeds across different commands.
"""

import logging
import discord
import datetime
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger("paradiso_bot")

def format_movie_embed(movie: Dict[str, Any], title_prefix: str = "") -> discord.Embed:
    """
    Format a movie object into a Discord embed.

    Args:
        movie: Movie dictionary with details
        title_prefix: Prefix to add to the title

    Returns:
        Discord embed with movie information
    """
    # Extract basic information
    title = movie.get("title", "Unknown")
    year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
    votes = movie.get("votes", 0)
    rating = movie.get("rating")
    director = movie.get("director")
    genre = movie.get("genre", [])
    plot = movie.get("plot")
    image = movie.get("image")

    # Create embed with optional title prefix
    embed_title = f"{title_prefix}{title}{year}" if title else f"{title_prefix}Unknown Movie"
    embed = discord.Embed(
        title=embed_title,
        color=0x00ff00
    )

    # Add description if there's a plot
    if plot:
        # Truncate long plots
        if len(plot) > 200:
            plot = plot[:197] + "..."
        embed.description = plot

    # Set thumbnail if available
    if image:
        embed.set_thumbnail(url=image)

    # Add director
    if director and director != "Unknown":
        embed.add_field(name="Director", value=director, inline=True)

    # Add rating
    if rating is not None:
        embed.add_field(name="Rating", value=f"⭐ {rating}/10", inline=True)

    # Add votes
    embed.add_field(name="Votes", value=f"🗳️ {votes}", inline=True)

    # Add genres
    if genre:
        embed.add_field(name="Genres", value=", ".join(genre), inline=True)

    return embed


async def send_search_results_embed(
    channel: Union[discord.Webhook, discord.abc.Messageable],
    query: str,
    results: List[Dict[str, Any]],
    total_count: int
) -> None:
    """
    Format and send search results as an embed.
    
    Args:
        channel: Channel or webhook to send to
        query: Original search query
        results: List of search result objects
        total_count: Total number of hits
    """
    if not results:
        embed = discord.Embed(
            title=f"No results for '{query}'",
            description="No movies found matching your search.",
            color=0xff9900
        )
        await channel.send(embed=embed)
        return
    
    embed = discord.Embed(
        title=f"Search Results for '{query}'",
        description=f"Found {total_count} movies matching your search:",
        color=0x3498db
    )
    
    # Add thumbnail from first result if available
    if results and results[0].get("image"):
        embed.set_thumbnail(url=results[0]["image"])
    
    for i, movie in enumerate(results[:10]):  # Limit to 10 results
        # Extract basic information
        title = movie.get("title", "Unknown")
        year = f" ({movie.get('year')})" if movie.get('year') is not None else ""
        votes = movie.get("votes", 0)
        
        # Extract highlights if available
        highlights = []
        if movie.get("_highlightResult"):
            highlight_result = movie["_highlightResult"]
            
            for field in ["director", "actors", "genre"]:
                if field in highlight_result:
                    if isinstance(highlight_result[field], list):
                        # Handle array fields like actors, genre
                        highlighted_items = [h["value"] for h in highlight_result[field] if h.get("matchLevel") != "none"]
                        if highlighted_items:
                            field_name = field.capitalize()
                            highlights.append(f"**{field_name}**: {', '.join(highlighted_items[:3])}")
                    elif highlight_result[field].get("matchLevel") != "none":
                        # Handle string fields like director
                        field_name = field.capitalize()
                        highlights.append(f"**{field_name}**: {highlight_result[field]['value']}")
        
        # Extract snippet if available
        snippet = None
        if movie.get("_snippetResult") and movie["_snippetResult"].get("plot"):
            snippet = movie["_snippetResult"]["plot"]["value"]
        
        # Format the movie details
        details = [f"**Votes**: {votes}"]
        
        # Add year and rating if available
        if movie.get("year"):
            details.append(f"**Year**: {movie.get('year')}")
        if movie.get("rating"):
            details.append(f"**Rating**: ⭐ {movie.get('rating')}/10")
        
        # Add highlighted fields
        details.extend(highlights)
        
        # Add snippet if available
        if snippet:
            details.append(f"**Plot**: {snippet}")
        
        embed.add_field(
            name=f"{i+1}. {title}{year}",
            value="\n".join(details),
            inline=False
        )
    
    embed.set_footer(text="Use /vote [title] to vote for a movie or /info [title] for more details.")
    
    await channel.send(embed=embed)


async def send_detailed_movie_embed(
    channel: Union[discord.Webhook, discord.abc.Messageable],
    movie: Dict[str, Any]
) -> None:
    """
    Format and send detailed movie information as an embed.
    
    Args:
        channel: Channel or webhook to send to
        movie: Movie object with details
    """
    if not movie:
        embed = discord.Embed(
            title="Movie Not Found",
            description="The requested movie could not be found.",
            color=0xff0000
        )
        await channel.send(embed=embed)
        return
    
    # Extract basic information
    title = movie.get("title", "Unknown")
    original_title = movie.get("originalTitle", "")
    year = movie.get("year")
    votes = movie.get("votes", 0)
    director = movie.get("director", "Unknown")
    actors = movie.get("actors", [])
    genre = movie.get("genre", [])
    plot = movie.get("plot", "No plot available.")
    rating = movie.get("rating")
    
    # Create embed
    embed = discord.Embed(
        title=f"🎬 {title} ({year})" if year else f"🎬 {title}",
        description=plot,
        color=0x00ff00
    )
    
    # Set thumbnail if available
    if movie.get("image"):
        embed.set_thumbnail(url=movie["image"])
    
    # Add original title if different
    if original_title and original_title != title:
        embed.add_field(name="Original Title", value=original_title, inline=True)
    
    # Add director
    if director and director != "Unknown":
        embed.add_field(name="Director", value=director, inline=True)
    
    # Add rating
    if rating is not None:
        embed.add_field(name="Rating", value=f"⭐ {rating}/10", inline=True)
    
    # Add votes
    embed.add_field(name="Votes", value=f"🗳️ {votes}", inline=True)
    
    # Add genres
    if genre:
        embed.add_field(name="Genres", value=", ".join(genre), inline=True)
    
    # Add actors
    if actors:
        embed.add_field(name="Starring", value=", ".join(actors[:8]), inline=False)
    
    # Add external links if available
    links = []
    if movie.get("imdbID"):
        links.append(f"[IMDb](https://www.imdb.com/title/{movie['imdbID']}/)")
    if movie.get("tmdbID"):
        links.append(f"[TMDb](https://www.themoviedb.org/movie/{movie['tmdbID']})")
    
    if links:
        embed.add_field(name="Links", value=" | ".join(links), inline=False)
    
    # Add objectID and timestamp as footer
    added_date = movie.get("addedDate", 0)
    footer_text = f"ID: {movie.get('objectID', 'Unknown')}"
    if added_date:
        date_str = datetime.datetime.fromtimestamp(added_date).strftime("%Y-%m-%d")
        footer_text += f" | Added: {date_str}"
    
    embed.set_footer(text=footer_text)
    
    await channel.send(embed=embed)