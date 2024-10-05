import os
import time
import logging
import dropbox
import requests
import functools
from dropbox.exceptions import AuthError
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
        def wrapper_retry(*args, **kwargs):
            retries = 0
            current_delay = delay

            while retries < max_retries:
                try:
                    return func(*args, **kwargs)  # Attempt to execute the function
                except AuthError as e:
                    logging.warning(f"Auth error encountered: {e}. Refreshing token...")
                    refresh_access_token()  # Refresh token if it's an auth error
                except Exception as e:
                    logging.warning(f"Error encountered: {e}")

                retries += 1
                if retries >= max_retries:
                    logging.error(f"Exceeded maximum retry attempts ({max_retries}) for {func.__name__}. Exiting.")
                    raise  # Re-raise the exception after final failure
                else:
                    logging.info(f"Retrying {func.__name__} ({retries}/{max_retries}) after {current_delay} seconds...")
                    time.sleep(current_delay)
                    current_delay *= backoff  # Exponentially increase delay for the next retry
        return wrapper_retry
    return decorator_retry

# Function to refresh the access token
def refresh_access_token():
    global ACCESS_TOKEN, REFRESH_TOKEN
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

# Create Dropbox client using the access token
def create_dbx_client():
    try:
        dbx = dropbox.Dropbox(ACCESS_TOKEN)
        dbx.users_get_current_account()  # To check if the token is valid
        return dbx
    except AuthError as e:
        logging.error("Failed to authenticate Dropbox client: {}".format(e))
        refresh_access_token()
        dbx = dropbox.Dropbox(ACCESS_TOKEN)
        return dbx

# Initialize Dropbox client
dbx = create_dbx_client()

# Function to list files recursively in Dropbox
@retry_with_token_refresh(max_retries=3, delay=2, backoff=2)
def list_files(folder_path=""):
    files_to_download = []
    try:
        result = dbx.files_list_folder(folder_path, recursive=True)
        while True:
            for entry in result.entries:
                if isinstance(entry, dropbox.files.FileMetadata):
                    files_to_download.append(entry.path_display)
                    logging.info(f"Found file: {entry.path_display}")
            if not result.has_more:
                break
            result = dbx.files_list_folder_continue(result.cursor)
    except AuthError:
        logging.error("Access token expired. Refreshing...")
        raise # Raise exception so the decorator will handle retries
    return files_to_download

# Function to download a file from Dropbox
@retry_with_token_refresh(max_retries=3, delay=2, backoff=2)
def download_file(dropbox_path, local_path):
    try:
        with open(local_path, 'wb') as f:
            metadata, response = dbx.files_download(dropbox_path)
            f.write(response.content)
        logging.info(f"Downloaded {dropbox_path} successfully.")
    except AuthError:
        logging.error(f"Error downloading {dropbox_path}. Refreshing token...")
        raise # Raise exception so the decorator will handle retries

# Create destination folder if not exists
os.makedirs(DESTINATION, exist_ok=True)

# List all files in the root directory
logging.info("Starting file listing...")
files_to_download = list_files()

# Download files
total_files = len(files_to_download)
logging.info(f"Total files to download: {total_files}")
for i, file_path in enumerate(files_to_download):
    local_file_path = os.path.join(DESTINATION, os.path.basename(file_path))
    download_file(file_path, local_file_path)

    # Log progress
    progress = (i + 1) * 100 // total_files
    logging.info(f"Progress: {progress}% ({i + 1}/{total_files} files downloaded)")

# Log total time taken
elapsed_time = time.time() - start_time
logging.info(f"Total files downloaded: {total_files}")
logging.info(f"Elapsed time: {elapsed_time} seconds")
