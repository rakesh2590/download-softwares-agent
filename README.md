# Download Softwares Agent

An agent that finds the latest version of each software in `requiredsoftwares.csv`, downloads the Windows installer, and installs it only after you approve.

## Requirements

- Windows
- Python 3.14
- An Anthropic API key

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and replace `your-api-key-here` with your key.

## CSV format

`requiredsoftwares.csv` has four columns:

| Column | Required | Example |
|---|---|---|
| sno | No | 1 |
| name | Yes | VLC Media Player |
| version | No | 3.14 |
| url | No | https://www.7-zip.org/ |

Rows with a blank `name` are skipped. A blank `version` means the latest stable version.

## Run

```bash
python download_softwares.py
```

Each PowerShell command the agent wants to run is printed and waits for `y` or `n`. Read it before you approve. Installers are downloaded to `downloads/` and are not committed to Git.

Each software can use up to $0.50 of API credit.
