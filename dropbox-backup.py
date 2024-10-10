import os
import time
import logging
import aiohttp
import aiofiles
import asyncio
import requests
import functools
from dotenv import load_dotenv

# Start timer
start_time = time.time()

# Load environment variables from .env file
if not os.path.exists('.dropbox-backup.env'):
    print(".env file not found!")
    exit(1)
load_dotenv('.dropbox-backup.env')

# Setup logging
script_dir = os.path.dirname(os.path.abspath(__file__))
logfile = os.path.join(script_dir, 'dropbox-backup.log')
logging.basicConfig(filename=logfile, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filemode='w')

# Get Dropbox API credentials from environment variables
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DESTINATION = os.getenv("DESTINATION", './backup')
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"

class AuthError(Exception):
    pass

class PermissionError(Exception):
    pass

class RateLimitError(Exception):
    pass

rate_limit_event = asyncio.Event()

def retry_with_token_refresh(max_retries=3, delay=2, backoff=2):
    """
    Retry decorator with exponential backoff and token refresh on AuthError.
    Args:
        max_retries (int): The maximum number of retries before giving up.
        delay (int): The initial delay between retries in seconds.
        backoff (int): The multiplier by which the delay increases after each retry.
    """
    def decorator_retry(func):
        @functools.wraps(func)
        async def wrapper_retry(*args, **kwargs):
            retries = 0
            current_delay = delay

            while retries < max_retries:
                try:
                    return await func(*args, **kwargs)  # Attempt to execute the function
                except AuthError as e:
                    logging.warning(f"Auth error encountered: {e}. Refreshing token...")
                    refresh_access_token()  # Refresh token if it's an auth error
                except RateLimitError as e:
                    logging.warning(f"Rate Limit error encountered: {e}. Retrying with backoff")
                    rate_limit_event.clear()

                retries += 1
                if retries >= max_retries:
                    logging.error(f"Exceeded maximum retry attempts ({max_retries}) for {func.__name__}. Exiting.")
                    raise  # Re-raise the exception after final failure
                else:
                    logging.info(f"Retrying {func.__name__} ({retries}/{max_retries}) after {current_delay} seconds...")
                    await asyncio.sleep(current_delay)

                    rate_limit_event.set()
                    current_delay *= backoff  # Exponentially increase delay for the next retry
        return wrapper_retry
    return decorator_retry

# Function to refresh the access token
def refresh_access_token():
    global ACCESS_TOKEN, REFRESH_TOKEN, dbx
    logging.info("Refreshing access token...")
    
    try:
        response = requests.post(TOKEN_URL, data={
            'grant_type': 'refresh_token',
            'refresh_token': REFRESH_TOKEN,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        })
        
        if response.status_code != 200:
            logging.error(f"Failed to refresh token: {response.text}")
            exit(1)

        # Update tokens
        tokens = response.json()
        ACCESS_TOKEN = tokens.get("access_token")
        new_refresh_token = tokens.get("refresh_token")

        # Update refresh token if a new one is provided
        if new_refresh_token:
            REFRESH_TOKEN = new_refresh_token
            logging.info(f"New refresh token: {REFRESH_TOKEN}")

        logging.info(f"New access token: {ACCESS_TOKEN}")

    except Exception as e:
        logging.error(f"Error refreshing token: {e}")
        exit(1)

async def response_handler(response):
    if response.status >= 400:
        logging.error(f"{response.status} - {await response.text()}")
        if response.status == 401:
            logging.error("Authentication failed: Invalid or expired access token.")
            raise AuthError("Invalid or expired access token.")
        elif response.status == 403:
            logging.error("Access denied: You do not have permission to access this folder.")
            raise PermissionError("Access denied to the folder.")
        elif response.status == 429:
            logging.error("Rate limit: Too many requests.")
            raise RateLimitError("Rate limit exceeded.")
        else:
            raise Exception("API Error")

async def api_request_handler(session, url, headers = None, json = None):
    await rate_limit_event.wait()

    default_headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    if headers:
        default_headers.update(headers)

    params = {
        'headers': default_headers
    }

    if json:
        params['json'] = json

    response = await session.post(
        url,
        **params
    )
    await response_handler(response)

    return response

# Function to list files recursively in Dropbox asynchronously
@retry_with_token_refresh(max_retries=3, delay=2, backoff=2)
async def fetch_folder_files(session, folder_path):
    try:
        # Making a request to list folder contents
        response = await api_request_handler(
            session=session,
            url="https://api.dropboxapi.com/2/files/list_folder",
            json={"path": folder_path, "recursive": True}
        )
        return await response.json()
    except Exception as e:
        logging.error(f"Error fetching files from {folder_path}: {str(e)}")
        raise # Raise exception so the decorator will handle retries

@retry_with_token_refresh(max_retries=3, delay=2, backoff=2)
async def fetch_continue_folder_files(session, cursor):
    try:
        # Making a request to continue listing folder contents
        response = await api_request_handler(
            session=session,
            url="https://api.dropboxapi.com/2/files/list_folder/continue",
            json={"cursor": cursor}
        )
        return await response.json()
    except Exception as e:
        logging.error(f"Error continuing to fetch files: {str(e)}")
        raise # Raise exception so the decorator will handle retries

# Function to list files recursively in Dropbox
async def list_files(session, folder_path=""):
    files_to_download = []
    try:
        result = await fetch_folder_files(session, folder_path)
        tasks = []
        while result:
            for entry in result.get('entries', []):
                if entry.get('.tag') == 'file':
                    files_to_download.append(entry['path_display'])
                    logging.info(f"Found file: {entry['path_display']}")
                elif entry.get('.tag') == 'folder':
                    # Start a new task for each subfolder
                    tasks.append(list_files(session, entry['path_display']))

            if not result.get('has_more'):
                break
            result = await fetch_continue_folder_files(session, result.get('cursor'))

         # Wait for all folder tasks to complete
        if tasks:
            nested_files = await asyncio.gather(*tasks)
            for file_list in nested_files:
                files_to_download.extend(file_list)

    except Exception as e:
        logging.error(f"Listing files for {folder_path} failed. Error: {str(e)}")
        raise

    return files_to_download

# Function to download a file from Dropbox
@retry_with_token_refresh(max_retries=3, delay=2, backoff=2)
async def download_file(session, dropbox_path, local_path, progress, total_files):
    url = "https://content.dropboxapi.com/2/files/download"
    
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Dropbox-API-Arg": f'{{"path": "{dropbox_path}"}}',
    }

    try:
        response = await api_request_handler(
            session=session,
            url=url,
            headers=headers,
        )
        await response_handler(response)

        content = await response.read()

        async with aiofiles.open(local_path, 'wb') as f:
            await f.write(content)
        logging.info(f"Downloaded {dropbox_path} successfully.")

        # Log progress
        progress_str = (progress + 1) * 100 // total_files
        logging.info(f"Progress: {progress_str}% ({progress + 1}/{total_files} files downloaded)")
    except Exception as e:
        logging.error(f"Error downloading {dropbox_path}. Error: {str(e)}")
        raise # Raise exception so the decorator will handle retries

async def main():
    # Initialize Dropbox client by refreshing token
    refresh_access_token()

    # Create destination folder if not exists
    os.makedirs(DESTINATION, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        # Allow all requests initially
        rate_limit_event.set()
        # List all files in the root directory
        logging.info("Starting file listing...")
        files_to_download = await list_files(session)

        # Download files
        total_files = len(files_to_download)
        logging.info(f"Total files to download: {total_files}")

        tasks = [
            download_file(
                session,
                file_path,
                os.path.join(DESTINATION, os.path.basename(file_path)),
                progress,
                total_files
            )
            for progress, file_path in files_to_download
        ]
        asyncio.gather(*tasks)

    # Log total time taken
    elapsed_time = time.time() - start_time
    logging.info(f"Total files downloaded: {total_files}")
    logging.info(f"Elapsed time: {elapsed_time} seconds")

if __name__ == "__main__":
    asyncio.run(main())
