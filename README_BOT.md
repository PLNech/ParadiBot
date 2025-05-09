# Paradiso Discord Bot

A Discord bot for the Paradiso movie voting system, using Algolia for data storage.

## Features

- Add movies to a voting queue
- Vote for movies
- Search for movies by title, actor, director, etc.
- Find related movies
- See top voted movies
- List all movies in the voting queue

## Requirements

- Python 3.9+
- discord.py
- python-dotenv
- algoliasearch
- requests
- pytest (for testing)

## Installation

1. Clone this repository
2. Set up a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a `.env` file based on `.env-example`
5. Create an Algolia account and set up indices:
   - Create an application in Algolia
   - Create two indices: `paradiso_movies` and `paradiso_votes`

## Configuration

Create a `.env` file in the project root with the following variables:

```
# Discord Bot Configuration
DISCORD_TOKEN=your_discord_bot_token_here

# Algolia Configuration
ALGOLIA_APP_ID=your_algolia_app_id_here
ALGOLIA_API_KEY=your_algolia_api_key_here
ALGOLIA_MOVIES_INDEX=paradiso_movies
ALGOLIA_VOTES_INDEX=paradiso_votes
```

## Running the Bot

Start the bot with:

```bash
python paradiso_bot.py
```

## Running Tests

Run the tests with:

```bash
python run_tests.py
```

Or using pytest directly:

```bash
pytest test/
```

## Setting up as a Service on Debian

1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/paradiso-bot.service
   ```

2. Add the following content:
   ```
   [Unit]
   Description=Paradiso Discord Bot
   After=network.target

   [Service]
   User=your_username
   Group=your_group
   WorkingDirectory=/path/to/bot
   ExecStart=/path/to/python /path/to/bot/paradiso_bot.py
   Restart=on-failure
   RestartSec=5
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

3. Reload systemd, enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable paradiso-bot
   sudo systemctl start paradiso-bot
   ```

4. Check the status:
   ```bash
   sudo systemctl status paradiso-bot
   ```

## Bot Commands

- `/add [title]` - Add a movie to the voting queue
- `/vote [title]` - Vote for a movie
- `/movies` - List all movies in the voting queue
- `/search [query]` - Search for movies by title, actor, director, etc.
- `/related [query]` - Find movies related to a specific movie
- `/top [count]` - Show the top voted movies (default: top 5)
- `/help` - Show help for all commands

## Development

### Project Structure

- `paradiso_bot.py` - Main bot script
- `test/` - Unit tests
  - `test_bot.py` - Basic bot tests
  - `test_commands.py` - Command tests
  - `test_algolia.py` - Algolia integration tests
- `requirements.txt` - Dependencies

### Testing

The project follows a Test-Driven Development (TDD) approach. Tests are written for all key functionality.

To run the tests with coverage report:

```bash
pytest --cov=paradiso_bot test/
```

## Troubleshooting

- **Discord commands not appearing:** Try running `/help` or restart the bot to force sync commands
- **Algolia errors:** Check your API keys and indices names in the `.env` file
- **Import errors:** Make sure all dependencies are installed correctly

## License

This project is licensed under the MIT License - see the LICENSE file for details. 