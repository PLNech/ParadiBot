"""
UI View components for Paradiso Discord Bot
Includes button-based pagination and selection views.
"""

import asyncio
import logging
import discord
from discord.ui import View, Button, Select
from typing import List, Dict, Any, Optional, Callable

from utils.algolia_utils import vote_for_movie

logger = logging.getLogger("paradiso_bot")

class VoteSelectionView(View):
    """
    View for movie vote selection buttons.
    Presents up to 5 numbered buttons for selecting movies to vote for.
    """
    def __init__(self, bot_instance, user_id: int, choices: List[Dict[str, Any]]):
        """Initialize with bot instance and vote choices."""
        super().__init__(timeout=60)  # 60 second timeout
        self.bot = bot_instance
        self.user_id = user_id
        self.choices = choices
        self.message = None  # Will be set when message is sent
        
        # Add numbered buttons for each choice (up to 5)
        for i, movie in enumerate(choices[:5]):
            button = Button(
                label=f"{i+1}",
                style=discord.ButtonStyle.primary,
                custom_id=f"vote_choice_{i}"
            )
            button.callback = self._create_vote_callback(i)
            self.add_item(button)
        
        # Add cancel button
        cancel_button = Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="vote_cancel"
        )
        cancel_button.callback = self.cancel_vote
        self.add_item(cancel_button)
    
    def _create_vote_callback(self, choice_index: int) -> Callable:
        """Create a callback for a vote button."""
        async def vote_callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "You cannot interact with someone else's vote selection.", 
                    ephemeral=True
                )
                return
            
            # Get the chosen movie
            chosen_movie = self.choices[choice_index]
            await interaction.response.defer(thinking=True)
            
            try:
                # Record the vote using the client directly
                success, result = await vote_for_movie(
                    self.bot.algolia_client, 
                    self.bot.algolia_movies_index_name,
                    self.bot.algolia_votes_index_name,
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
                    if updated_movie.get("image"): 
                        embed.set_thumbnail(url=updated_movie["image"])
                    embed.set_footer(text=f"Voted by {interaction.user.display_name}")
                    
                    # Disable all buttons after voting
                    for item in self.children:
                        item.disabled = True
                    
                    # Edit the original message with the updated embed and disabled buttons
                    await interaction.followup.edit_message(
                        message_id=self.message.id,
                        embed=embed,
                        view=self
                    )
                else:
                    if isinstance(result, str) and result == "Already voted":
                        await interaction.followup.send(
                            f"❌ You have already voted for '{chosen_movie['title']}'!"
                        )
                    else:
                        logger.error(f"Error recording vote: {result}")
                        await interaction.followup.send(
                            f"❌ An error occurred while recording your vote."
                        )
            
            except Exception as e:
                logger.error(f"Error in vote button callback: {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ An error occurred while processing your vote: {str(e)}"
                )
            
            # Clean up the state regardless of success
            if self.message and self.message.id in self.bot.vote_messages:
                del self.bot.vote_messages[self.message.id]
        
        return vote_callback
    
    async def cancel_vote(self, interaction: discord.Interaction) -> None:
        """Cancel the vote selection."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You cannot interact with someone else's vote selection.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=False)
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        # Update the message with cancelled status
        embed = discord.Embed(
            title="Vote Cancelled",
            description="You cancelled the vote selection.",
            color=0xffcc00
        )
        
        await interaction.followup.edit_message(
            message_id=self.message.id,
            embed=embed,
            view=self
        )
        
        # Clean up the state
        if self.message and self.message.id in self.bot.vote_messages:
            del self.bot.vote_messages[self.message.id]
    
    async def on_timeout(self) -> None:
        """Handle timeout of the vote selection view."""
        # Only modify message if it still exists
        if self.message:
            try:
                # Disable all buttons
                for item in self.children:
                    item.disabled = True
                
                # Update the message with timeout status
                embed = discord.Embed(
                    title="Vote Selection Timed Out",
                    description="The vote selection has expired. Please use the `/vote` command again.",
                    color=0xff0000
                )
                
                await self.message.edit(embed=embed, view=self)
                
                # Clean up the state
                if self.message.id in self.bot.vote_messages:
                    del self.bot.vote_messages[self.message.id]
            
            except discord.NotFound:
                logger.warning(f"Message {self.message.id} not found during timeout handling")
            except Exception as e:
                logger.error(f"Error in vote selection timeout handler: {e}", exc_info=True)


class MoviesPaginationView(View):
    """
    View for movie list pagination buttons.
    Provides next/previous page navigation and a page selector.
    """
    def __init__(self, bot_instance, user_id: int, all_movies: List[Dict[str, Any]], 
                 movies_per_page: int = 10, detailed_count: int = 5):
        """Initialize with bot instance and pagination parameters."""
        super().__init__(timeout=180)  # 3 minute timeout
        self.bot = bot_instance
        self.user_id = user_id
        self.all_movies = all_movies
        self.movies_per_page = movies_per_page
        self.detailed_count = detailed_count
        self.current_page = 0
        self.total_pages = max(1, (len(all_movies) + movies_per_page - 1) // movies_per_page)
        self.message = None  # Will be set when message is sent
        
        # Add navigation buttons
        self.first_button = Button(
            label="⏮️ First",
            style=discord.ButtonStyle.secondary,
            custom_id="movies_first",
            disabled=True  # Initially disabled on first page
        )
        self.first_button.callback = self.first_page
        
        self.prev_button = Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.primary,
            custom_id="movies_prev",
            disabled=True  # Initially disabled on first page
        )
        self.prev_button.callback = self.prev_page
        
        self.next_button = Button(
            label="Next ▶️",
            style=discord.ButtonStyle.primary,
            custom_id="movies_next",
            disabled=self.total_pages <= 1  # Disabled if only one page
        )
        self.next_button.callback = self.next_page
        
        self.last_button = Button(
            label="Last ⏭️",
            style=discord.ButtonStyle.secondary,
            custom_id="movies_last",
            disabled=self.total_pages <= 1  # Disabled if only one page
        )
        self.last_button.callback = self.last_page
        
        # Add buttons to view
        self.add_item(self.first_button)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.last_button)
    
    async def update_buttons(self) -> None:
        """Update button states based on current page."""
        self.first_button.disabled = self.current_page == 0
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        self.last_button.disabled = self.current_page >= self.total_pages - 1
    
    async def first_page(self, interaction: discord.Interaction) -> None:
        """Go to first page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You cannot navigate someone else's movie list.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=False)
        
        self.current_page = 0
        await self.update_buttons()
        
        # Get embed for first page
        embed = await self.bot._get_movies_page_embed(
            self.all_movies,
            self.current_page,
            self.movies_per_page,
            self.detailed_count,
            self.total_pages
        )
        
        await interaction.followup.edit_message(
            message_id=self.message.id,
            embed=embed,
            view=self
        )
    
    async def prev_page(self, interaction: discord.Interaction) -> None:
        """Go to previous page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You cannot navigate someone else's movie list.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=False)
        
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_buttons()
            
            # Get embed for previous page
            embed = await self.bot._get_movies_page_embed(
                self.all_movies,
                self.current_page,
                self.movies_per_page,
                self.detailed_count,
                self.total_pages
            )
            
            await interaction.followup.edit_message(
                message_id=self.message.id,
                embed=embed,
                view=self
            )
    
    async def next_page(self, interaction: discord.Interaction) -> None:
        """Go to next page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You cannot navigate someone else's movie list.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=False)
        
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_buttons()
            
            # Get embed for next page
            embed = await self.bot._get_movies_page_embed(
                self.all_movies,
                self.current_page,
                self.movies_per_page,
                self.detailed_count,
                self.total_pages
            )
            
            await interaction.followup.edit_message(
                message_id=self.message.id,
                embed=embed,
                view=self
            )
    
    async def last_page(self, interaction: discord.Interaction) -> None:
        """Go to last page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You cannot navigate someone else's movie list.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=False)
        
        self.current_page = self.total_pages - 1
        await self.update_buttons()
        
        # Get embed for last page
        embed = await self.bot._get_movies_page_embed(
            self.all_movies,
            self.current_page,
            self.movies_per_page,
            self.detailed_count,
            self.total_pages
        )
        
        await interaction.followup.edit_message(
            message_id=self.message.id,
            embed=embed,
            view=self
        )
    
    async def on_timeout(self) -> None:
        """Handle timeout of the pagination view."""
        # Only modify message if it still exists
        if self.message:
            try:
                # Disable all buttons
                for item in self.children:
                    item.disabled = True
                
                await self.message.edit(view=self)
                
                # Clean up the state
                if self.message.id in self.bot.movies_pagination_state:
                    del self.bot.movies_pagination_state[self.message.id]
            
            except discord.NotFound:
                logger.warning(f"Message {self.message.id} not found during timeout handling")
            except Exception as e:
                logger.error(f"Error in pagination timeout handler: {e}", exc_info=True)
