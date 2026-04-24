# Setup

These instructions configure and run the fetchlinks backend on Linux.

## 1) Create and activate virtual environment

From the repository root:

```bash
python3 -m venv ../venv
source ../venv/bin/activate
```

## 2) Install dependencies

```bash
pip install -r requirements.txt
```

## 3) Configure credentials

Create a credential directory:

```bash
mkdir -p ~/.fetchlinks
```

### Reddit credential file

Create ~/.fetchlinks/reddit.json:

```json
{
	"reddit": {
		"APP_CLIENT_ID": "your_client_id",
		"APP_CLIENT_SECRET": "your_client_secret"
	}
}
```

### Bluesky credential file (optional)

Bluesky is disabled by default. If you want to enable it, create ~/.fetchlinks/bluesky.json:

```json
{
	"bluesky": {
		"IDENTIFIER": "your-handle.bsky.social",
		"APP_PASSWORD": "xxxx-xxxx-xxxx-xxxx"
	}
}
```

## 4) Configure sources

Edit fetchlinks/data/config/sources.json:

- Keep rss.enabled and reddit.enabled as needed.
- Bluesky defaults to disabled. Set bluesky.enabled to true only if you created a Bluesky credential file.
- Ensure each credential_location path matches your local files.

## 5) Run the backend

```bash
cd fetchlinks
python3 fetch_links.py -config data/config/config.json -sources data/config/sources.json
```

On first run, the backend initializes the SQLite database automatically if it does not exist.

## 6) Validate output

- Database location is controlled by data/config/config.json.
- Logs are written to the log_location path in data/config/config.json.