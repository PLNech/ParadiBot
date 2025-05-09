# Paradiso Bot Tests

This directory contains tests for the Paradiso Bot.

## Algolia V4 Tests

The `test_algolia_v4.py` file contains tests specifically for validating the integration with Algolia's v4 client.

### Prerequisites

1. Create a `.env` file in the project root with your Algolia test credentials:

```
ALGOLIA_APP_ID=your_app_id
ALGOLIA_API_KEY=your_admin_api_key
TEST_ALGOLIA_MOVIES_INDEX=test_paradiso_movies
TEST_ALGOLIA_VOTES_INDEX=test_paradiso_votes
```

> **Important**: Use test indices for testing, not your production indices.

### Running Tests

To run the Algolia v4 tests:

```bash
pytest -xvs tests/test_algolia_v4.py
```

The tests will:
1. Clear the test indices
2. Add test movie data
3. Run tests for all Algolia operations
4. Clean up test data

## Notes

- The tests use pytest's async support for testing async functions
- Make sure to install test dependencies: `pip install pytest pytest-asyncio` 