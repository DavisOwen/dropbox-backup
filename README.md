# Dropbox Backup

A simple python script that downloads all your Dropbox files to a directory of your choice. Typically used to backup your files to an external HDD.

## Credentials

```bash
cp .dropbox-backup.env.example .dropbox-backup.env
```

Obtain credentials using the instructions in [Obtaining a Dropbox Refresh Token](#obtaining-a-dropbox-refresh-token), and supply them in the corresponding fields in `.dropbox-backup.env`

Destination directory can be specified to choose which directory you want your backup to be stored in.

## Usage

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
python ./dropbox-backup.py &
```

Output can be viewed in the `dropbox_backup.log` file. The runtime for this can be quite long, so it is recommended to run in the background (with `&`), or to automate this via crontab or task scheduler to run in the background intermittently.

##  Obtaining a Dropbox Refresh Token

To automate your Dropbox API interactions and avoid manual authentication each time, follow these steps to obtain a refresh token:

1. **Create a Dropbox App:**
    - Go to the [Dropbox App Console](https://www.dropbox.com/developers/apps).
    - Click on **"Create App"**.
    - Select **"Scoped access"** and choose the appropriate permissions (e.g., files and folders access).
    - After filling out the required details, click **"Create App"**.

2. **Authorize Your App:**
    - Construct a URL to authorize your app, replacing `CLIENT_ID` with your app's client ID:
    ```plaintext
    https://www.dropbox.com/oauth2/authorize?client_id=CLIENT_ID&response_type=code&token_access_type=offline
    ```
    - Open this URL in a web browser. This will prompt you to log in to Dropbox and authorize the app.
    - After authorization, you will be redirected to the redirect URI you specified in your app settings, along with an authorization code in the URL.

3. **Exchange Authorization Code for Access and Refresh Tokens:**
    - Use the following `curl` command to exchange the authorization code for an access token and refresh token:
     ```bash
     curl -X POST https://api.dropboxapi.com/oauth2/token \
         --header "Content-Type: application/x-www-form-urlencoded" \
         --data "code=AUTHORIZATION_CODE&grant_type=authorization_code&client_id=CLIENT_ID&client_secret=CLIENT_SECRET"
     ```
    - Replace `AUTHORIZATION_CODE` with the code you received in the redirect.
    - Replace `CLIENT_ID` and `CLIENT_SECRET` with your app's credentials.
