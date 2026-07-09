from google.cloud import bigquery

client = bigquery.Client(project="meli-bi-data")

def query(sql, limit=None):
    if limit and "LIMIT" not in sql.upper():
        sql = sql.rstrip("; \n") + f" LIMIT {limit}"
    print(f"Rodando query...\n")
    df = client.query(sql).to_dataframe()
    print(f"{len(df)} linhas retornadas\n")
    return df

if __name__ == "__main__":
    df = query("SELECT * FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`", limit=5)
    print(df.to_string())
