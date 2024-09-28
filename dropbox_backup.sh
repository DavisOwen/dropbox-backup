#!/bin/bash

# # Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGFILE="$SCRIPT_DIR/dropbox_backup.log"

# Redirect stdout and stderr to the log file
exec > >(tee -a "$LOGFILE") 2>&1

# Load environment variables from the .env file
if [ -f .dropbox_backup.env ]; then
    source .dropbox_backup.env
else
    echo ".env file not found!"
    exit 1
fi

TOKEN_URL="https://api.dropboxapi.com/oauth2/token"

# Array to hold all file paths for downloading
declare -a FILES_TO_DOWNLOAD

# Function to refresh the access token
refresh_access_token() {
    echo "Refreshing access token..."
    response=$(curl -s -X POST $TOKEN_URL \
        --data "grant_type=refresh_token" \
        --data "refresh_token=$REFRESH_TOKEN" \
        --data "client_id=$CLIENT_ID" \
        --data "client_secret=$CLIENT_SECRET")

    # Extract new access token and refresh token
    ACCESS_TOKEN=$(echo "$response" | jq -r '.access_token')
    REFRESH_TOKEN=$(echo "$response" | jq -r '.refresh_token')

    # Log new tokens
    echo "New access token: $ACCESS_TOKEN"
    echo "New refresh token: $REFRESH_TOKEN"

    # Check if the token refresh was successful
    if [[ "$ACCESS_TOKEN" == "null" ]]; then
        echo "Failed to refresh access token. Exiting."
        exit 1
    fi
}

# Function to check and refresh the access token if expired
check_access_token() {
    http_response=$(curl -s -o /dev/null -w "%{http_code}" -X POST https://api.dropboxapi.com/2/users/get_current_account \
        --header "Authorization: Bearer $ACCESS_TOKEN")

    if [ "$http_response" -eq 401 ]; then
        refresh_access_token
    fi
}

# Function to recursively list all files in a folder
list_files() {
    DROPBOX_FOLDER_PATH=$1
    CURSOR=$2

    check_access_token

    echo "Listing files in folder: $DROPBOX_FOLDER_PATH"  # Log the folder being processed

    if [ -z "$CURSOR" ]; then
        # First call (no cursor)
      # List all files and folders in the Dropbox folder
      response=$(curl -s -X POST https://api.dropboxapi.com/2/files/list_folder \
          --header "Authorization: Bearer $ACCESS_TOKEN" \
          --header "Content-Type: application/json" \
          --data "{\"path\": \"$DROPBOX_FOLDER_PATH\"}")
    else
      # Subsequent call (with cursor)
      response=$(curl -s -X POST https://api.dropboxapi.com/2/files/list_folder/continue \
          --header "Authorization: Bearer $ACCESS_TOKEN" \
          --header "Content-Type: application/json" \
          --data "{\"cursor\": \"$CURSOR\"}")
    fi

    # Loop through the entries
    for entry in $(echo "$response" | jq -r '.entries[] | @base64'); do
        entry_decoded=$(echo "$entry" | base64 --decode)
        entry_tag=$(echo "$entry_decoded" | jq -r '.[".tag"]')

        # Check if the entry is a file
        if [ "$entry_tag" == "file" ]; then
            file_path=$(echo "$entry_decoded" | jq -r '.path_display')
            FILES_TO_DOWNLOAD+=("$file_path")  # Add file to the list
            echo "Found file: $file_path"  # Log the found file

        # Check if the entry is a folder
        elif [ "$entry_tag" == "folder" ]; then
            folder_path=$(echo "$entry_decoded" | jq -r '.path_display')
            echo "Entering folder: $folder_path"  # Log the folder being entered
            list_files "$folder_path" ""  # Recur for folders (start fresh with empty cursor)
        fi
    done

    # Check for pagination
    HAS_MORE=$(echo "$response" | jq -r '.has_more')
    if [ "$HAS_MORE" == "true" ]; then
        CURSOR=$(echo "$response" | jq -r '.cursor')
        list_files "$DROPBOX_FOLDER_PATH" "$CURSOR"  # Recur for next page of results
    fi
}

# Function to download all files collected in the array
download_file() {
    DROPBOX_PATH=$1
    LOCAL_PATH="$TEMP_DIR/$(basename "$DROPBOX_PATH")"

    http_response=$(curl -s -w "%{http_code}" -X POST https://content.dropboxapi.com/2/files/download \
        --header "Authorization: Bearer $ACCESS_TOKEN" \
        --header "Dropbox-API-Arg: {\"path\": \"$DROPBOX_PATH\"}" \
        --output "$LOCAL_PATH")

    echo "HTTP Response Code: $http_response" >> "$LOGFILE"

    # Check if the download was successful based on HTTP status code
    if [ "$http_response" -eq 200 ]; then
        echo "Downloaded $DROPBOX_PATH successfully." >> "$LOGFILE"
        mv "$LOCAL_PATH" "$DESTINATION/"
        echo "Moved $LOCAL_PATH to $DESTINATION/" >> "$LOGFILE"
    else
        echo "Error downloading $DROPBOX_PATH. HTTP Response Code: $http_response" >> "$LOGFILE"
    fi
}

# Start listing files from the root folder
FILES_TO_DOWNLOAD=()
list_files "" ""

# Final log message Log total files
total_files=${#FILES_TO_DOWNLOAD[@]}
echo "Total files to download: $total_files"

# Download files after listing
for ((i = 0; i < total_files; i++)); do
    file=${FILES_TO_DOWNLOAD[$i]}
    download_file "$file"
    
    # Calculate and log progress
    progress=$(( (i + 1) * 100 / total_files ))
    echo "Progress: $progress% ($((i + 1))/$total_files files downloaded)"
done

echo "Total files downloaded: $total_files" >> "$LOGFILE"
