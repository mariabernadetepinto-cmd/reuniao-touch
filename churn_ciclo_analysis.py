import os, json, sys
from google.oauth2.credentials import Credentials
from google.cloud import bigquery
import pandas as pd
from scipy import stats
import numpy as np

def get_client():
    creds_data = json.loads(os.environ.get("GCP_CREDENTIALS"))
    creds = Credentials(
        token=None,
        refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"]
    )
    return bigquery.Client(project=creds_data["quota_project_id"], credentials=creds)

client = get_client()

# ── QUERY 1: numeric correlations (paradas, km, paradas comerciais) ──
print("=== Query 1: correlações numéricas ===")
q1 = """
WITH pool AS (
  SELECT
    CAST(cm.CUS_CUST_ID AS INT64) AS driver_id,
    cm.REFERENCE_MONTH,
    cm.NEW_CHURN_AT_CM AS is_churn,
    CASE WHEN cm.NEW_CHURN_AT_CM
         THEN DATE_SUB(cm.REFERENCE_MONTH, INTERVAL 1 MONTH)
         ELSE cm.REFERENCE_MONTH END AS metrics_month
  FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_BY_CONTRACT_MODEL_MONTHLY` cm
  WHERE cm.SIT_SITE_ID = 'MLB'
    AND cm.COMPANY_TYPE = 'MLP'
    AND cm.SHP_LG_TYPE = 'last_mile'
    AND cm.CM_GROUP = 'Frota_Fixa'
    AND cm.REFERENCE_MONTH BETWEEN '2026-01-01' AND '2026-06-30'
    AND NOT EXISTS (
      SELECT 1
      FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_STATUS_MONTHLY` rfl
      WHERE rfl.CUS_CUST_ID = cm.CUS_CUST_ID
        AND rfl.REFERENCE_MONTH = cm.REFERENCE_MONTH
        AND rfl.SIT_SITE_ID = 'MLB'
        AND rfl.COMPANY_TYPE = 'MLP'
        AND rfl.SHP_LG_TYPE = 'last_mile'
        AND (rfl.NEW_USER = TRUE OR rfl.REACQUIRE_USER = TRUE OR rfl.REACTIVE_USER = TRUE)
    )
),
routes_agg AS (
  SELECT
    CAST(SHP_LG_DRIVER_USER_ID AS INT64) AS driver_id,
    PARSE_DATE('%Y-M%m', SHP_LG_INIT_MONTH_TZ) AS route_month,
    MAX(SHP_LG_DRIVER_USER_ID_CAREER) AS career,
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64)) AS avg_stops,
    AVG(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS avg_stops_commercial,
    SUM(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS sum_stops_commercial,
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS avg_km,
    SUM(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS sum_km,
    AVG(SAFE_DIVIDE(
      CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64),
      CAST(STOPS_REAL_SHPS AS FLOAT64)
    )) AS pct_commercial,
    COUNT(*) AS total_routes
  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID = 'MLB'
    AND SHP_LG_TYPE = 'LAST_MILE'
    AND SHP_LG_INIT_MONTH_TZ IN (
      '2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06'
    )
  GROUP BY 1, 2
)
SELECT
  p.is_churn,
  r.career,
  r.avg_stops,
  r.avg_stops_commercial,
  r.sum_stops_commercial,
  r.avg_km,
  r.sum_km,
  r.pct_commercial,
  r.total_routes
FROM pool p
JOIN routes_agg r ON p.driver_id = r.driver_id AND p.metrics_month = r.route_month
"""

df1 = client.query(q1).to_dataframe()
print(f"Rows: {len(df1)}, Churn rate: {df1['is_churn'].mean():.1%}")

metrics = ['avg_stops','avg_stops_commercial','sum_stops_commercial','avg_km','sum_km','pct_commercial']
careers = ['NEW HIRE','NEWBIE','TENURED','VETERAN']

print("\n--- Correlações point-biserial por carreira ---")
results = []
for m in metrics:
    sub = df1[df1[m].notna() & np.isfinite(df1[m])]
    r_all, p_all = stats.pointbiserialr(sub['is_churn'].astype(int), sub[m])
    row = {'metric': m, 'ALL': round(r_all,4)}
    for c in careers:
        sc = sub[sub['career']==c]
        if len(sc) > 50:
            r_c, p_c = stats.pointbiserialr(sc['is_churn'].astype(int), sc[m])
            row[c] = round(r_c,4)
        else:
            row[c] = None
    results.append(row)
    print(f"  {m:30s}  ALL={r_all:+.4f}  p={p_all:.4f}")

df_corr = pd.DataFrame(results)
print(df_corr.to_string(index=False))

# churned vs retained means
print("\n--- Médias: churned vs retained ---")
for m in metrics:
    sub = df1[df1[m].notna() & np.isfinite(df1[m])]
    g = sub.groupby('is_churn')[m].mean()
    print(f"  {m:35s}  retained={g.get(False,float('nan')):.2f}  churned={g.get(True,float('nan')):.2f}")

# ── QUERY 2: ciclo — churn rate por categoria ──
print("\n=== Query 2: churn rate por ciclo planejado ===")
q2 = """
WITH pool AS (
  SELECT
    CAST(cm.CUS_CUST_ID AS INT64) AS driver_id,
    cm.REFERENCE_MONTH,
    cm.NEW_CHURN_AT_CM AS is_churn,
    CASE WHEN cm.NEW_CHURN_AT_CM
         THEN DATE_SUB(cm.REFERENCE_MONTH, INTERVAL 1 MONTH)
         ELSE cm.REFERENCE_MONTH END AS metrics_month
  FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_BY_CONTRACT_MODEL_MONTHLY` cm
  WHERE cm.SIT_SITE_ID = 'MLB'
    AND cm.COMPANY_TYPE = 'MLP'
    AND cm.SHP_LG_TYPE = 'last_mile'
    AND cm.CM_GROUP = 'Frota_Fixa'
    AND cm.REFERENCE_MONTH BETWEEN '2026-01-01' AND '2026-06-30'
    AND NOT EXISTS (
      SELECT 1
      FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_STATUS_MONTHLY` rfl
      WHERE rfl.CUS_CUST_ID = cm.CUS_CUST_ID
        AND rfl.REFERENCE_MONTH = cm.REFERENCE_MONTH
        AND rfl.SIT_SITE_ID = 'MLB'
        AND rfl.COMPANY_TYPE = 'MLP'
        AND rfl.SHP_LG_TYPE = 'last_mile'
        AND (rfl.NEW_USER = TRUE OR rfl.REACQUIRE_USER = TRUE OR rfl.REACTIVE_USER = TRUE)
    )
),
routes_routes AS (
  SELECT
    CAST(SHP_LG_DRIVER_USER_ID AS INT64) AS driver_id,
    PARSE_DATE('%Y-M%m', SHP_LG_INIT_MONTH_TZ) AS route_month,
    MAX(SHP_LG_DRIVER_USER_ID_CAREER) AS career,
    -- ciclo planejado predominante no mês
    APPROX_TOP_COUNT(SHP_CYCLE_NAME_PLANNED, 1)[OFFSET(0)].value AS cycle_planned_mode,
    APPROX_TOP_COUNT(SHP_CYCLE_REAL_AGG_DESC, 1)[OFFSET(0)].value AS cycle_real_mode
  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID = 'MLB'
    AND SHP_LG_TYPE = 'LAST_MILE'
    AND SHP_LG_INIT_MONTH_TZ IN (
      '2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06'
    )
  GROUP BY 1, 2
)
SELECT
  p.is_churn,
  r.career,
  r.cycle_planned_mode,
  r.cycle_real_mode
FROM pool p
JOIN routes_routes r ON p.driver_id = r.driver_id AND p.metrics_month = r.route_month
"""

df2 = client.query(q2).to_dataframe()
print(f"Rows: {len(df2)}")

print("\n--- Churn rate por SHP_CYCLE_NAME_PLANNED (top 15) ---")
grp = df2.groupby('cycle_planned_mode').agg(
    n=('is_churn','count'), churn_rate=('is_churn','mean')
).sort_values('n', ascending=False).head(15)
grp['churn_rate'] = grp['churn_rate'].map('{:.1%}'.format)
print(grp.to_string())

print("\n--- Churn rate por SHP_CYCLE_REAL_AGG_DESC (top 15) ---")
grp2 = df2.groupby('cycle_real_mode').agg(
    n=('is_churn','count'), churn_rate=('is_churn','mean')
).sort_values('n', ascending=False).head(15)
grp2['churn_rate'] = grp2['churn_rate'].map('{:.1%}'.format)
print(grp2.to_string())

print("\n--- Ciclo planejado × carreira: churn rate ---")
ct = df2.groupby(['cycle_planned_mode','career'])['is_churn'].agg(['mean','count']).reset_index()
ct.columns = ['cycle','career','churn_rate','n']
ct = ct[ct['n']>=30].sort_values(['cycle','career'])
for c in careers:
    sub = ct[ct['career']==c].sort_values('churn_rate', ascending=False).head(8)
    if len(sub):
        print(f"\n  {c}:")
        for _, row in sub.iterrows():
            print(f"    {row['cycle']:40s}  n={int(row['n']):5d}  churn={row['churn_rate']:.1%}")

print("\n--- Ciclo REAL × carreira: churn rate ---")
ct2 = df2.groupby(['cycle_real_mode','career'])['is_churn'].agg(['mean','count']).reset_index()
ct2.columns = ['cycle','career','churn_rate','n']
ct2 = ct2[ct2['n']>=30].sort_values(['cycle','career'])
for c in careers:
    sub = ct2[ct2['career']==c].sort_values('churn_rate', ascending=False).head(8)
    if len(sub):
        print(f"\n  {c}:")
        for _, row in sub.iterrows():
            print(f"    {row['cycle']:40s}  n={int(row['n']):5d}  churn={row['churn_rate']:.1%}")

# Save for HTML
df1.to_csv('/tmp/ciclo_numeric.csv', index=False)
df2.to_csv('/tmp/ciclo_categorical.csv', index=False)
print("\nSalvo em /tmp/ciclo_numeric.csv e /tmp/ciclo_categorical.csv")
