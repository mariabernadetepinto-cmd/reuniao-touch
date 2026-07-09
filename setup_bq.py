import os
import json
import pandas as pd
from google.oauth2.credentials import Credentials
from google.cloud import bigquery

def get_client():
    creds_json = os.environ.get("GCP_CREDENTIALS")
    if not creds_json:
        raise ValueError("GCP_CREDENTIALS not set")
    creds_data = json.loads(creds_json)
    creds = Credentials(
        token=None,
        refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
    )
    return bigquery.Client(project=creds_data["quota_project_id"], credentials=creds)

def run_query(sql):
    client = get_client()
    return client.query(sql).to_dataframe()
