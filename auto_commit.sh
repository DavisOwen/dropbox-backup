# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGFILE="$SCRIPT_DIR/auto_commit.log"

# Load environment variables from the .env file
if [ -f .auto_commit.env ]; then
    source .auto_commit.env
else
    echo ".env file not found!"
    exit 1
fi

# Function to log errors
log_error() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') ERROR: $1" >> "$LOGFILE"
}

# Try to navigate to the directory
cd "$DIR" || {
    log_error "Failed to navigate to $DIR"
    exit 1
}

if [[ -n $(git status --porcelain) ]]; then
    git add .
    git commit -m "Automated commit on $(date +'%Y-%m-%d %H:%M:%S')" || {
      log_error "Failed to commit changes"
      exit 1
    }
else
    echo "$(date +'%Y-%m-%d %H:%M:%S') INFO: No changes to commit." >> "$LOGFILE"
fi
