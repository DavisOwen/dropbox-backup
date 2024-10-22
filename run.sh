#!/bin/bash

# Set variables
cd "$(dirname "${BASH_SOURCE[0]}")"
VENV_DIR="./env"      # Path to your virtual environment
REQUIREMENTS="./requirements.txt"  # Path to requirements.txt
SCRIPT_NAME="./dropbox-backup.py"

# Check if the virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment not found. Creating one..."
    python3 -m venv "$VENV_DIR"
fi

# Activate the virtual environment
source "$VENV_DIR/bin/activate"

pip install -r "$REQUIREMENTS"

# Run the Python script
echo "Running $SCRIPT_NAME..."
python "$SCRIPT_NAME"

# Deactivate the virtual environment
deactivate
