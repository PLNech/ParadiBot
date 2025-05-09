#!/usr/bin/env python
"""
Paradiso Review Augmentation Script

This script uses the Algolia GenAI API and/or local llms to reconcile reviews and movie titles.
It processes reviews without an 'augmented' tag, attempts to identify movie information,
and then confirms matches against the paradiso_movies index.

Usage:
    python reconcile_reviews.py --app-id YOUR_APP_ID --admin-key YOUR_ADMIN_KEY [--limit 100]

Requirements:
    - Python 3.7+
    - requests package (pip install requests)
    - algoliasearch package (pip install algoliasearch)
"""

import argparse
import json
import os
import time
import logging
import requests
import re
from algoliasearch.search_client import SearchClient
from typing import Dict, List, Optional, Any, Union

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
GENAI_API_BASE_URL = "https://generative-us.algolia.com"  # Using US endpoint for faster throughput
OLLAMA_API_BASE_URL = "http://localhost:11434/api/generate" # Ollama local API endpoint
# GENAI_API_BASE_URL = "https://generative-eu.algolia.com"  # Using EU endpoint for Mistral Small 3.1

PROMPT_GUESS_TITLE_INSTRUCTIONS = """
[TASK]
You are analyzing a movie review to extract specific information about the movie being discussed. Extract ONLY information that is explicitly mentioned or strongly implied in the review.

[EXTRACTION GUIDELINES]
1. Extract the title of the movie (REQUIRED)
2. Extract the director if mentioned
3. Extract key actors if mentioned
4. Extract the release year if mentioned

[IMPORTANT RULES]
- If you cannot identify the title with high confidence, DO NOT EXTRACT ANY INFORMATION
- Do not guess or infer information not present in the text
- Only include information you are certain about
- If multiple movies are mentioned, focus on the main movie being reviewed

[OUTPUT FORMAT]
Return a JSON object with these fields:
{
  "title": "The exact movie title",
  "director": "Director name or null if not mentioned",
  "actors": ["Actor 1", "Actor 2"] or [] if none mentioned,
  "year": YYYY (as number) or null if not mentioned,
  "query": "Title Director MainActor" (combined search terms)
}

If you cannot identify the movie with confidence, return only:
{"confidence": "low"}
"""

PROMPT_CONFIRM_MATCH_INSTRUCTIONS = """
[TASK]
Determine if the movie information from the query matches one of the movie records in the search results.

[CONTEXT]
- You will receive a search query containing movie information (title, possibly director/actors)
- The search results contain movie records with objectIDs, titles, actors, directors, etc.
- Your task is to find the most confident match between the query and the search results

[MATCHING CRITERIA]
1. Title match is most important - look for exact or very close matches
2. If multiple title matches exist, use additional information (actors, director, year) to disambiguate
3. Be cautious with common movie titles - ensure other details align

[RESPONSE FORMAT]
- If you find a confident match: Return ONLY the objectID of the matched movie (e.g., "abc123")
- If you cannot confidently determine a match: Return ONLY "NOT_SURE"
- Do not include ANY explanations, notes, or additional text in your response
"""

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Reconcile movie reviews with titles using Algolia GenAI API or local Ollama')
    parser.add_argument('--app-id', required=True, help='Algolia Application ID')
    parser.add_argument('--admin-key', required=True, help='Algolia Admin API Key')
    parser.add_argument('--batch-size', type=int, default=10, help='Number of reviews to process in one batch')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--limit', type=int, default=100, help='Maximum number of reviews to process')
    parser.add_argument('--use-local-model', type=str, help='Name of the local Ollama model to use (e.g., "mistral"). If provided, skips Algolia GenAI and uses local Ollama.')
    return parser.parse_args()


def setup_algolia_client(app_id: str, admin_key: str) -> SearchClient:
    """Set up the Algolia client."""
    return SearchClient.create(app_id, admin_key)


def create_prompt(client: SearchClient, admin_key: str, name: str, instructions: str, tone: str = "natural") -> str:
    """
    Create a prompt in Algolia GenAI.
    
    Args:
        client: Algolia client
        name: Name of the prompt
        instructions: Instructions for the prompt
        tone: Tone of the prompt
        
    Returns:
        objectID of the created prompt
    """
    # Check if prompt already exists
    prompts_index = client.init_index("algolia_rag_prompts")
    
    try:
        results = prompts_index.search(name, {"restrictSearchableAttributes": ["name"]})
        
        if results["hits"]:
            # Prompt already exists, return its objectID
            logger.info(f"Prompt '{name}' already exists, using existing prompt.")
            return results["hits"][0]["objectID"]
    except Exception as e:
        logger.warning(f"Error searching for prompt: {e}, will try to create it")
    
    # Create new prompt
    headers = {
        "x-algolia-application-id": client.app_id,
        "x-algolia-api-key": admin_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "name": name,
        "instructions": instructions,
        "tone": tone
    }
    
    response = requests.post(
        f"{GENAI_API_BASE_URL}/create/prompt",
        headers=headers,
        json=payload
    )
    
    if response.status_code != 200:
        raise Exception(f"Failed to create prompt: {response.text}")
    
    prompt_id = response.json()["objectID"]
    logger.info(f"Created prompt '{name}' with ID: {prompt_id}")
    return prompt_id


def create_data_source(client: SearchClient, admin_key: str, name: str, source: str, filters: str = None) -> str:
    """
    Create a data source in Algolia GenAI.
    
    Args:
        client: Algolia client
        name: Name of the data source
        source: Source index
        filters: Additional filters
        
    Returns:
        objectID of the created data source
    """
    # Check if data source already exists
    data_sources_index = client.init_index("algolia_rag_data_sources")
    
    try:
        results = data_sources_index.search(name, {"restrictSearchableAttributes": ["name"]})
        
        if results["hits"]:
            # Data source already exists, return its objectID
            logger.info(f"Data source '{name}' already exists, using existing data source.")
            return results["hits"][0]["objectID"]
    except Exception as e:
        logger.warning(f"Error searching for data source: {e}, will try to create it")
    
    # Create new data source
    headers = {
        "x-algolia-application-id": client.app_id,
        "x-algolia-api-key": admin_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "name": name,
        "source": source,
    }
    
    if filters:
        payload["filters"] = filters
    
    response = requests.post(
        f"{GENAI_API_BASE_URL}/create/data_source",
        headers=headers,
        json=payload
    )
    
    if response.status_code != 200:
        raise Exception(f"Failed to create data source: {response.text}")
    
    data_source_id = response.json()["objectID"]
    logger.info(f"Created data source '{name}' with ID: {data_source_id}")
    return data_source_id


def generate_response(
    client: SearchClient, 
    admin_key: str,
    prompt_id: str, 
    data_source_id: str, 
    query: str, 
    additional_filters: str = None,
    with_object_ids: List[str] = None
) -> Dict:
    """
    Generate a response using the Algolia GenAI API with Mistral Small 3.1.
    
    Args:
        client: Algolia client
        prompt_id: ID of the prompt to use
        data_source_id: ID of the data source to use
        query: Query text
        additional_filters: Additional filters
        with_object_ids: Specific object IDs to search
        
    Returns:
        Response from the GenAI API
    """
    headers = {
        "x-algolia-application-id": client.app_id,
        "x-algolia-api-key": admin_key,
        "Content-Type": "application/json"
    }
    
    # Maximum number of hits for better context
    nb_hits = 20 if not with_object_ids else 1
    
    payload = {
        "query": query,
        "promptId": prompt_id,
        "dataSourceId": data_source_id,
        "save": True,
        "useCache": False,
        "origin": "api",
        "nbHits": nb_hits,
        # Specify attributes to highlight to improve context quality
        "highlighting": True,
        # Ensure we're getting enough context for complex reviews
        "attributesToRetrieve": ["*"]
    }
    
    if additional_filters:
        payload["additionalFilters"] = additional_filters
    
    if with_object_ids:
        payload["withObjectIds"] = with_object_ids
    
    # Add retry logic for API stability
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{GENAI_API_BASE_URL}/generate/response",
                headers=headers,
                json=payload,
                timeout=30  # Set a reasonable timeout
            )
            
            if response.status_code == 200:
                return response.json()
            
            # Handle rate limiting (429) specifically
            if response.status_code == 429:
                retry_delay = min(retry_delay * 2, 10)  # Exponential backoff, max 10 seconds
                logger.warning(f"Rate limited. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
                
            # Other error codes
            error_msg = f"API error (attempt {attempt+1}/{max_retries}): Status {response.status_code}, {response.text}"
            logger.error(error_msg)
            
            if attempt == max_retries - 1:
                raise Exception(error_msg)
                
            time.sleep(retry_delay)
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error (attempt {attempt+1}/{max_retries}): {str(e)}"
            logger.error(error_msg)
            
            if attempt == max_retries - 1:
                raise Exception(error_msg)
                
            time.sleep(retry_delay)
    
    # If we've exhausted all retries
    raise Exception("Failed to generate response after multiple attempts")


def parse_llm_output(output: str) -> Dict:
    """
    Parse the LLM output to extract movie information.
    This function is flexible to handle various output formats.
    
    Args:
        output: The output from the LLM
        
    Returns:
        Parsed movie information
    """
    # Clean the output to handle potential markdown or extra whitespace
    cleaned_output = output.replace('```json', '').replace('```', '').strip()
    
    try:
        # First try to parse as JSON directly
        return json.loads(cleaned_output)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse as clean JSON, attempting extraction. Output: {cleaned_output[:200]}...")
        
        # Try to extract JSON using regex patterns
        # Look for the entire JSON object pattern with flexible whitespace
        json_match = re.search(r'(\{[\s\S]*?\})', cleaned_output)
        if json_match:
            try:
                json_str = json_match.group(1).strip()
                # Fix common issues like single quotes or missing quotes
                json_str = json_str.replace("'", '"')
                return json.loads(json_str)
            except json.JSONDecodeError:
                logger.debug(f"Failed to parse extracted JSON: {json_match.group(1)[:100]}...")
        
        # Try to extract individual fields with more flexible regex patterns
        result = {}
        
        # Extract title with flexible pattern
        title_pattern = r'["\']?title["\']?\s*:\s*["\']([^"\']+)["\']'
        title_match = re.search(title_pattern, cleaned_output, re.IGNORECASE)
        if title_match:
            result["title"] = title_match.group(1).strip()
        
        # Extract director with flexible pattern
        director_pattern = r'["\']?director["\']?\s*:\s*["\']([^"\']+)["\']'
        director_match = re.search(director_pattern, cleaned_output, re.IGNORECASE)
        if director_match:
            result["director"] = director_match.group(1).strip()
        
        # Extract year with flexible pattern
        year_pattern = r'["\']?year["\']?\s*:\s*(\d{4})'
        year_match = re.search(year_pattern, cleaned_output, re.IGNORECASE)
        if year_match:
            try:
                result["year"] = int(year_match.group(1).strip())
            except ValueError:
                result["year"] = year_match.group(1).strip()
        
        # Extract actors with flexible pattern
        actors_pattern = r'["\']?actors["\']?\s*:\s*\[(.*?)\]'
        actors_match = re.search(actors_pattern, cleaned_output, re.IGNORECASE | re.DOTALL)
        if actors_match:
            actors_str = actors_match.group(1)
            # Try to find all quoted strings
            actors = re.findall(r'["\']([^"\']+)["\']', actors_str)
            if actors:
                result["actors"] = actors
            else:
                # If no quotes found, try comma-separated values
                actors = [a.strip() for a in actors_str.split(',') if a.strip()]
                if actors:
                    result["actors"] = actors
        
        # Extract query with flexible pattern
        query_pattern = r'["\']?query["\']?\s*:\s*["\']([^"\']+)["\']'
        query_match = re.search(query_pattern, cleaned_output, re.IGNORECASE)
        if query_match:
            result["query"] = query_match.group(1).strip()
        
        # Extract confidence level if present
        confidence_pattern = r'["\']?confidence["\']?\s*:\s*["\']?(low|medium|high)["\']?'
        confidence_match = re.search(confidence_pattern, cleaned_output, re.IGNORECASE)
        if confidence_match:
            result["confidence"] = confidence_match.group(1).lower()
        
        # If we couldn't extract any meaningful fields, return a low confidence result
        if not result:
            return {"confidence": "low"}
        
        # Also return low confidence if only confidence field was found
        if len(result) == 1 and "confidence" in result:
            return {"confidence": "low"}
        
        # If the word "low" appears near "confidence" in the text, return low confidence
        if "low confidence" in cleaned_output.lower() or "confidence: low" in cleaned_output.lower():
            return {"confidence": "low"}
        
        # If we have title but no query, generate a query
        if "title" in result and "query" not in result:
            query_parts = [result["title"]]
            if "director" in result and result["director"]:
                query_parts.append(result["director"])
            if "actors" in result and result["actors"] and len(result["actors"]) > 0:
                query_parts.append(result["actors"][0])
            result["query"] = " ".join(query_parts)
        
        # If we have a query, ensure it's not too long (Algolia has limits)
        if "query" in result and len(result["query"]) > 300:
            result["query"] = result["query"][:300]
        
        return result


def extract_object_id(response_text: str) -> str:
    """
    Extract the object ID from the LLM response.
    
    Args:
        response_text: The text response from the LLM
        
    Returns:
        The object ID or "NOT_SURE"
    """
    # Clean up the response text (remove markdown formatting, quotes, etc.)
    cleaned_text = response_text.strip()
    cleaned_text = cleaned_text.replace('```', '').replace('"', '').replace("'", "").strip()
    
    # Check for "NOT_SURE" with case insensitivity
    if "not_sure" in cleaned_text.lower() or "not sure" in cleaned_text.lower():
        return "NOT_SURE"
    
    # Check if the entire response is just an object ID (preferred format)
    if re.match(r'^[a-zA-Z0-9_-]{5,}$', cleaned_text):
        return cleaned_text
    
    # Try to extract an objectID from text with additional content
    # First, prioritize a pattern that looks specifically like objectID: value
    object_id_label_match = re.search(r'(?:objectid|object\s*id|id)[\s:]*([a-zA-Z0-9_-]{5,})', 
                                     cleaned_text.lower(), re.IGNORECASE)
    if object_id_label_match:
        return object_id_label_match.group(1)
    
    # If no labeled match, try to find any alphanumeric string that looks like an object ID
    object_id_match = re.search(r'([a-zA-Z0-9_-]{5,})', cleaned_text)
    if object_id_match:
        # Make sure we're not just matching some random text
        potential_id = object_id_match.group(1)
        # Usually object IDs don't contain common English words
        common_words = ["the", "and", "but", "not", "sure", "match", "found", "movie", "review", "object"]
        if not any(word in potential_id.lower() for word in common_words):
            return potential_id
    
    # If nothing looks like an object ID, return NOT_SURE
    return "NOT_SURE"


def generate_local_response(
    model_name: str,
    prompt_instructions: str,
    query_text: str,
    search_results: Optional[List[Dict]] = None
) -> str:
    """
    Generate a response using a local Ollama model.

    Args:
        model_name: Name of the Ollama model to use.
        prompt_instructions: The instructions for the model.
        query_text: The main query text (e.g., review content or search terms).
        search_results: Optional list of search results to include in the context.

    Returns:
        The generated text response from the local model.
    """
    headers = {
        "Content-Type": "application/json"
    }

    # Construct the full prompt with instructions, query, and optional search results
    full_prompt = f"[INSTRUCTIONS]\n{prompt_instructions}\n\n[QUERY]\n{query_text}"

    if search_results:
        # Include search results in the prompt for the confirmation step
        search_results_str = json.dumps(search_results, indent=2)
        full_prompt += f"\n\n[SEARCH RESULTS]\n{search_results_str}"

    payload = {
        "model": model_name,
        "prompt": full_prompt,
        "stream": False, # We need the full response at once
        "format": "json" if "json" in prompt_instructions.lower() else "" # Ask for JSON if instructions indicate
    }

    # Add retry logic
    max_retries = 3
    retry_delay = 1  # seconds

    for attempt in range(max_retries):
        try:
            response = requests.post(
                OLLAMA_API_BASE_URL,
                headers=headers,
                json=payload,
                timeout=60 # Increase timeout for local processing
            )

            if response.status_code == 200:
                return response.json().get("response", "") # Ollama returns response in 'response' field

            error_msg = f"Ollama API error (attempt {attempt+1}/{max_retries}): Status {response.status_code}, {response.text}"
            logger.error(error_msg)

            if attempt == max_retries - 1:
                raise Exception(error_msg)

            time.sleep(retry_delay)

        except requests.exceptions.RequestException as e:
            error_msg = f"Ollama Request error (attempt {attempt+1}/{max_retries}): {str(e)}"
            logger.error(error_msg)

            if attempt == max_retries - 1:
                raise Exception(error_msg)

            time.sleep(retry_delay)

    raise Exception("Failed to generate response from Ollama after multiple attempts")


def process_reviews(
    client: SearchClient,
    admin_key: str,
    prompt_guess_id: Optional[str], # These can be None if using local model
    prompt_confirm_id: Optional[str],
    reviews_data_source_id: Optional[str],
    movies_data_source_id: Optional[str],
    local_model_name: Optional[str], # New argument
    batch_size: int = 10,
    limit: int = 100
):
    """
    Process reviews to match them with movies using Algolia GenAI or local Ollama.

    Args:
        client: Algolia client
        prompt_guess_id: ID of the guess title prompt (Algolia GenAI)
        prompt_confirm_id: ID of the confirm match prompt (Algolia GenAI)
        reviews_data_source_id: ID of the reviews data source (Algolia GenAI)
        movies_data_source_id: ID of the movies data source (Algolia GenAI)
        local_model_name: Name of the local Ollama model to use (if any)
        batch_size: Number of reviews to process at once
        limit: Maximum number of reviews to process
    """
    reviews_index = client.init_index("paradiso_reviews")
    movies_index = client.init_index("paradiso_movies") # Need this for searching even with local model

    # Find reviews without augmentation tag
    filters = "NOT _tags:augmented"
    hits_per_page = min(batch_size, 1000)  # Algolia max is 1000
    
    # Get the first batch of reviews
    results = reviews_index.search("", {
        "filters": filters,
        "hitsPerPage": hits_per_page
    })
    
    total_processed = 0
    total_matched = 0
    start_time = time.time()
    
    hits = results["hits"]
    while hits and total_processed < limit:
        # Process reviews in batches
        batch_start_time = time.time()
        logger.info(f"Processing batch of {len(hits)} reviews (processed so far: {total_processed})")
        
        for review in hits:
            # Process each review
            review_id = review["objectID"]
            review_text = review.get("review_text", "")
            summary = review.get("summary", "")
            
            # Combine review text and summary if available
            if summary:
                full_text = f"Review Summary: {summary}\n\nFull Review: {review_text}"
            else:
                full_text = f"Full Review: {review_text}"
            
            if not full_text.strip():
                logger.warning(f"Review {review_id} has no text, skipping.")
                continue
            
            logger.info(f"Processing review {review_id} ({total_processed+1}/{limit})")
            
            try:
                # Step 1: Generate guess for movie info
                guess_result = {}
                if local_model_name:
                    # Use local model for guessing
                    logger.info(f"Using local model '{local_model_name}' for guessing review {review_id}")
                    guess_response_text = generate_local_response(
                        local_model_name,
                        PROMPT_GUESS_TITLE_INSTRUCTIONS,
                        full_text
                    )
                    guess_result = parse_llm_output(guess_response_text)
                else:
                    # Use Algolia GenAI for guessing
                    logger.info(f"Using Algolia GenAI for guessing review {review_id}")
                    guess_response = generate_response( # Assuming create_prompt/data_source were called in main
                        client,
                        admin_key,
                        prompt_guess_id,
                        reviews_data_source_id,
                        full_text,
                        with_object_ids=[review_id]
                    )
                    guess_result = parse_llm_output(guess_response["response"])

                if guess_result.get("confidence") == "low" or not guess_result.get("query"):
                    logger.info(f"Low confidence guess for review {review_id}, skipping match phase.")

                    # Update review with the guess result, even if low confidence
                    reviews_index.partial_update_object({
                        "objectID": review_id,
                        "augmented": {
                            "confidence": "low",
                            "processed_at": int(time.time())
                        }
                    })
                    continue

                # Step 2: Use the extracted info to confirm a match
                search_query = guess_result.get("query", "")

                matched_object_id = "NOT_SURE"

                if local_model_name:
                    # Use local model for confirming match
                    logger.info(f"Using local model '{local_model_name}' for confirming match for query: '{search_query}'")
                    # First, search Algolia for movies based on the query
                    movie_search_results = movies_index.search(search_query, {
                        "hitsPerPage": 10, # Get a few relevant hits for the model
                        "attributesToRetrieve": ["objectID", "title", "actors", "director", "year"],
                        "highlightPostTag": "</mark>", # Add highlighting
                        "highlightPreTag": "<mark>" # Add highlighting
                    })

                    confirm_response_text = generate_local_response(
                        local_model_name,
                        PROMPT_CONFIRM_MATCH_INSTRUCTIONS,
                        search_query, # Send the query again
                        movie_search_results.get("hits", []) # Pass the search results
                    )
                    matched_object_id = extract_object_id(confirm_response_text)

                else:
                    # Use Algolia GenAI for confirming match
                    logger.info(f"Using Algolia GenAI for confirming match with query: '{search_query}'")
                    confirm_response = generate_response( # Assuming create_prompt/data_source were called in main
                        client,
                        admin_key,
                        prompt_confirm_id,
                        movies_data_source_id,
                        search_query
                    )
                    matched_object_id = extract_object_id(confirm_response["response"])

                if matched_object_id == "NOT_SURE":
                    logger.info(f"No confident match found for review {review_id}")
                    
                    # Update review with the guess result, but no match
                    reviews_index.partial_update_object({
                        "objectID": review_id,
                        "augmented": {
                            "title": guess_result.get("title"),
                            "director": guess_result.get("director"),
                            "actors": guess_result.get("actors"),
                            "year": guess_result.get("year"),
                            "query": guess_result.get("query"),
                            "confidence": "medium",
                            "processed_at": int(time.time())
                        }
                    })
                else:
                    logger.info(f"✅ MATCH FOUND: Review {review_id} → Movie {matched_object_id}")
                    total_matched += 1
                    
                    # Update review with the match
                    updates = {
                        "objectID": review_id,
                        "augmented": {
                            "title": guess_result.get("title"),
                            "director": guess_result.get("director"),
                            "actors": guess_result.get("actors"),
                            "year": guess_result.get("year"),
                            "query": search_query,
                            "movie_id": matched_object_id,
                            "confidence": "high",
                            "processed_at": int(time.time())
                        },
                        "_tags": ["augmented"]
                    }
                    reviews_index.partial_update_object(updates)
            
            except Exception as e:
                logger.error(f"Error processing review {review_id}: {e}")
            
            total_processed += 1
            if total_processed >= limit:
                break
            
            # Add a short delay between requests to avoid rate limiting
            time.sleep(0.1)
        
        # Calculate batch processing time and rate
        batch_time = time.time() - batch_start_time
        items_per_second = len(hits) / batch_time if batch_time > 0 else 0
        logger.info(f"Batch completed in {batch_time:.2f}s ({items_per_second:.2f} reviews/second)")
        
        # If we've reached the limit or no more results, break
        if total_processed >= limit or len(hits) < hits_per_page:
            break
        
        # Get the next batch of reviews
        results = reviews_index.search("", {
            "filters": filters,
            "hitsPerPage": hits_per_page,
            "page": results["page"] + 1
        })
        hits = results["hits"]
    
    # Calculate overall processing time and statistics
    total_time = time.time() - start_time
    avg_time_per_review = total_time / total_processed if total_processed > 0 else 0
    match_rate = (total_matched / total_processed * 100) if total_processed > 0 else 0
    
    logger.info(f"=== PROCESSING SUMMARY ===")
    logger.info(f"Processed {total_processed} reviews in {total_time:.2f}s ({avg_time_per_review:.2f}s per review)")
    logger.info(f"Found {total_matched} matches ({match_rate:.1f}% match rate)")
    logger.info(f"===========================")


def main():
    """Main function to orchestrate the review reconciliation process."""
    args = parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Print script banner for visual cues
    print("\n" + "="*70)
    print(" PARADISO MOVIE REVIEWS RECONCILIATION SCRIPT ".center(70, "="))
    print(" Using Algolia GenAI with Mistral Small 3.1 ".center(70, "="))
    print("="*70 + "\n")
    
    logger.info("Starting Paradiso Review Augmentation")
    
    # Setup Algolia client (needed for searching even with local model)
    logger.info(f"Connecting to Algolia with App ID: {args.app_id}")
    client = setup_algolia_client(args.app_id, args.admin_key)

    prompt_guess_id = None
    prompt_confirm_id = None
    reviews_data_source_id = None
    movies_data_source_id = None

    if not args.use_local_model:
        # Create or get prompts and data sources if using Algolia GenAI
        logger.info("Setting up prompts and data sources for Algolia GenAI...")
        prompt_guess_id = create_prompt(
            client,
            args.admin_key,
            "Paradiso - Guess Movie Info from Review (Mistral)",
            PROMPT_GUESS_TITLE_INSTRUCTIONS,
            "professional"
        )

        prompt_confirm_id = create_prompt(
            client,
            args.admin_key,
            "Paradiso - Confirm Movie Match (Mistral)",
            PROMPT_CONFIRM_MATCH_INSTRUCTIONS,
            "professional"
        )

        reviews_data_source_id = create_data_source(
            client,
            args.admin_key,
            "Paradiso - Reviews Database",
            "paradiso_reviews"
        )

        movies_data_source_id = create_data_source(
            client,
            args.admin_key,
            "Paradiso - Movies Database",
            "paradiso_movies"
        )
    else:
         logger.info(f"Using local Ollama model '{args.use_local_model}', skipping Algolia GenAI setup.")


    logger.info(f"Starting review processing (batch size: {args.batch_size}, limit: {args.limit})...")

    # Process reviews
    try:
        process_reviews(
            client,
            args.admin_key,
            prompt_guess_id,
            prompt_confirm_id,
            reviews_data_source_id,
            movies_data_source_id,
            args.use_local_model, # Pass the local model name
            args.batch_size,
            args.limit
        )

        logger.info("✅ Paradiso Review Augmentation completed successfully")
    except KeyboardInterrupt:
        logger.info("\n⚠️ Process interrupted by user. Partial results have been saved.")
    except Exception as e:
        logger.error(f"❌ Error during review processing: {e}")
        logger.error("Some reviews may have been processed before the error occurred.")
    
    print("\n" + "="*70)
    print(" PROCESS COMPLETE ".center(70, "="))
    print("="*70 + "\n")


if __name__ == "__main__":
    main()