import os
import json
from google.oauth2.credentials import Credentials
from google.cloud import bigquery

def get_bq_client(project="meli-bi-data"):
    creds_json = os.environ.get("GCP_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
    else:
        with open("creds.json") as f:
            info = json.load(f)

    creds = Credentials(
        token=None,
        refresh_token=info["refresh_token"],
        client_id=info["client_id"],
        client_secret=info["client_secret"],
        token_uri="https://oauth2.googleapis.com/token"
    )
    return bigquery.Client(project=project, credentials=creds)

def run_query(sql, project="meli-bi-data"):
    client = get_bq_client(project)
    return client.query(sql).to_dataframe()
