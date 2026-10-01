# Fetching signals data

Pulls the latest non-PII campaign snapshot (`user`, `items`, `item_actions`) using the
two-step flow described in [curl.yaml](../bluedot_fetch/curl.yaml): a Keycloak `client_credentials` token,
then the pre-signed S3 URLs from `GET /v1/campaign/dump`.

## Setup

```sh
pip install -r bluedot_fetch/requirements.txt
cp bluedot_fetch/.env.example bluedot_fetch/.env      # then fill in the real hosts and CLIENT_SECRET
```

## Run

```sh
python bluedot_fetch/fetchdata.py                 # fetch metadata + download the three files into ./dumps
python bluedot_fetch/fetchdata.py --no-download   # print the dump metadata only
python bluedot_fetch/fetchdata.py --allow-torn    # download even if the last_modified values disagree
python bluedot_fetch/fetchdata.py --out-dir data  # write elsewhere
```

## Notes

- `.env` and `dumps/` are gitignored — the secret and the data stay local.
- The pre-signed URLs are short-lived; the script checks `expires_at` before downloading.
- If the three `last_modified` timestamps span more than 60s the snapshot may mix exporter
  runs, so the script refuses to download unless `--allow-torn` is passed.
- Downloads deliberately send no `Authorization` header — the signature is in the query
  string and a header breaks it.
