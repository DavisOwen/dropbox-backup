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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_LEVEL_DICT = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
}

# Setup logging
script_dir = os.path.dirname(os.path.abspath(__file__))
logfile = os.path.join(script_dir, 'dropbox-backup.log')
logging.basicConfig(filename=logfile, level=LOG_LEVEL_DICT.get(LOG_LEVEL, logging.INFO), format='%(asctime)s - %(levelname)s - %(message)s', filemode='w')

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
    def __init__(self, message="Rate limit exceeded.", retry_after=None):
        self.retry_after = retry_after
        super().__init__(message)

class UnsupportedFileError(Exception):
    pass

class RateLimiter:
    def __init__(self, max_concurrent_requests: int, delay: float):
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)  # Limit concurrent requests
        self.delay = delay  # Delay between requests

    async def request_with_rate_limit(self, request_func, *args, **kwargs):
        async with self.semaphore:  # Acquire the semaphore
            await asyncio.sleep(self.delay)  # Delay while holding the semaphore
            return await request_func(*args, **kwargs)  # Make the request while still holding the semaphore

rate_limiter = RateLimiter(max_concurrent_requests=30, delay=0.5)

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
                    if e.retry_after:
                        current_delay = e.retry_after
                except Exception as e:
                    logging.exception(e)
                    logging.error("Retrying with backoff")

                retries += 1
                if retries >= max_retries:
                    logging.error(f"Exceeded maximum retry attempts ({max_retries}) for {func.__name__}. Exiting.")
                    raise  # Re-raise the exception after final failure
                else:
                    logging.info(f"Retrying {func.__name__} ({retries}/{max_retries}) after {current_delay} seconds...")
                    await asyncio.sleep(current_delay)

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
            logging.debug(f"New refresh token: {REFRESH_TOKEN}")

        logging.debug(f"New access token: {ACCESS_TOKEN}")

    except Exception as e:
        logging.exception(f"Error refreshing token: {e}")
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
            response = await response.json()
            retry_after = response.get('error', {}).get('retry_after', None)
            raise RateLimitError("Rate limit exceeded.", retry_after)
        elif response.status == 409:
            logging.warning("Unsupported file") 
            raise UnsupportedFileError("Unsupported file")
        else:
            raise Exception("API Error")

async def api_request_handler(session, url, headers = None, json = None):
    default_headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    if headers:
        default_headers.update(headers)

    params = {
        'headers': default_headers
    }

    if json:
        params['json'] = json

    response = await rate_limiter.request_with_rate_limit(session.post, url, **params)

    await response_handler(response)

    return response

@retry_with_token_refresh(max_retries=10, delay=2, backoff=2)
# Function to list files recursively in Dropbox asynchronously
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
        logging.exception(f"Error fetching files from {folder_path}: {str(e)}")
        raise # Raise exception so the decorator will handle retries

@retry_with_token_refresh(max_retries=10, delay=2, backoff=2)
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
        logging.exception(f"Error continuing to fetch files: {str(e)}")
        raise # Raise exception so the decorator will handle retries

# Function to list and download files recursively in Dropbox
async def list_and_download_files(session, folder_path="", cursor=None):
    folder_path_str = folder_path if folder_path else "/"
    logging.debug(f"Listing files for {folder_path_str}")
    try:
        if cursor:
            result = await fetch_continue_folder_files(session, cursor)
        else:
            result = await fetch_folder_files(session, folder_path)
        total_files = 0
        entries = result.get('entries', [])
        total_files += len(entries)
        logging.info(f"{total_files} more files found, downloading...")
        tasks = []
        for entry in entries:
            if entry.get('.tag') == 'file':
                file_path = entry['path_display']
                logging.debug(f"Found file: {file_path}")
                destination_path = os.path.join(DESTINATION, file_path.lstrip('/'))
                os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                tasks.append(download_file(
                    session,
                    file_path,
                    destination_path,
                ))

        if result.get('has_more'):
            tasks.append(list_and_download_files(session, cursor=result.get('cursor')))

        await asyncio.gather(*tasks)

    except Exception as e:
        logging.exception(f"Listing files for {folder_path_str} failed. Error: {str(e)}")
        raise

# Function to download a file from Dropbox
@retry_with_token_refresh(max_retries=10, delay=2, backoff=2)
async def download_file(session, dropbox_path, local_path):
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

        async with aiofiles.open(local_path, 'wb') as f:
            async for chunk in response.content.iter_any():
                await f.write(chunk)
        logging.debug(f"Downloaded {dropbox_path} successfully.")
    except UnsupportedFileError as e:
        # Some files like .paper files just can't be downloaded, we don't care
        # about skipping these
        pass
    except Exception as e:
        logging.exception(f"Error downloading {dropbox_path}. Error: {str(e)}")
        raise # Raise exception so the decorator will handle retries

async def main():
    # Initialize Dropbox client by refreshing token
    refresh_access_token()

    # Create destination folder if not exists
    os.makedirs(DESTINATION, exist_ok=True)

    timeout = aiohttp.ClientTimeout(
        # total=None,            # No overall timeout
        # connect=60,            # Timeout for establishing the connection
        # sock_read=600,         # Timeout for reading data (increase for large files)
        # sock_connect=60        # Timeout for TCP connection setup
    )

    connector = aiohttp.TCPConnector(keepalive_timeout=5)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # List all files in the root directory
        logging.info("Starting backup...")

        await list_and_download_files(session)


    # Log total time taken
    elapsed_time = time.time() - start_time
    logging.info(f"Elapsed time: {elapsed_time} seconds")

if __name__ == "__main__":
    asyncio.run(main())
