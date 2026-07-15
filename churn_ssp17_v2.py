# churn_ssp17_v2.py — extends churn_ssp17.py with:
# vehicle type (APPROX_TOP_COUNT), DPPH gap, stops gap, KM, facility time
import os, json
from google.oauth2.credentials import Credentials
from google.cloud import bigquery
import pandas as pd
import numpy as np
from scipy import stats

def get_client():
    creds_data = json.loads(os.environ.get("GCP_CREDENTIALS"))
    creds = Credentials(token=None, refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"], client_secret=creds_data["client_secret"])
    return bigquery.Client(project=creds_data["quota_project_id"], credentials=creds)

client = get_client()
print("=== SSP17 v2 — Gap Plan vs Real, KM, Veículo Detalhado, Facility ===")

q = """
WITH pool AS (
  SELECT CAST(cm.CUS_CUST_ID AS INT64) AS driver_id, cm.REFERENCE_MONTH,
    cm.NEW_CHURN_AT_CM AS is_churn,
    CASE WHEN cm.NEW_CHURN_AT_CM THEN DATE_SUB(cm.REFERENCE_MONTH, INTERVAL 1 MONTH)
         ELSE cm.REFERENCE_MONTH END AS metrics_month
  FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_BY_CONTRACT_MODEL_MONTHLY` cm
  WHERE cm.SIT_SITE_ID='MLB' AND cm.COMPANY_TYPE='MLP'
    AND cm.SHP_LG_TYPE='last_mile' AND cm.CM_GROUP='Frota_Fixa'
    AND cm.REFERENCE_MONTH BETWEEN '2026-01-01' AND '2026-06-30'
    AND NOT EXISTS (
      SELECT 1 FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_STATUS_MONTHLY` rfl
      WHERE rfl.CUS_CUST_ID=cm.CUS_CUST_ID AND rfl.REFERENCE_MONTH=cm.REFERENCE_MONTH
        AND rfl.SIT_SITE_ID='MLB' AND rfl.COMPANY_TYPE='MLP' AND rfl.SHP_LG_TYPE='last_mile'
        AND (rfl.NEW_USER=TRUE OR rfl.REACQUIRE_USER=TRUE OR rfl.REACTIVE_USER=TRUE)
    )
),
routes_agg AS (
  SELECT
    CAST(SHP_LG_DRIVER_USER_ID AS INT64) AS driver_id,
    PARSE_DATE('%Y-M%m', SHP_LG_INIT_MONTH_TZ) AS route_month,
    SHP_LG_FACILITY_ID AS facility_id,
    MAX(SHP_LG_DRIVER_USER_ID_CAREER) AS career,
    APPROX_TOP_COUNT(SHP_LG_VEHICLE_TYPE_AGG, 1)[OFFSET(0)].value AS vehicle_type_agg,
    APPROX_TOP_COUNT(SHP_LG_VEHICLE_TYPE, 1)[OFFSET(0)].value AS vehicle_type_detail,
    COUNT(*) AS n_routes,
    COUNT(DISTINCT DATE_TRUNC(SHP_LG_INIT_DTTM_TZ, DAY)) AS active_days,
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS avg_km,
    AVG(SAFE_DIVIDE(CAST(ORH AS FLOAT64)-CAST(ORH_PLANNED AS FLOAT64),
                    NULLIF(CAST(ORH_PLANNED AS FLOAT64),0))*100) AS avg_orh_dev_pct,
    COUNTIF(SAFE_DIVIDE(CAST(ORH AS FLOAT64)-CAST(ORH_PLANNED AS FLOAT64),
                        NULLIF(CAST(ORH_PLANNED AS FLOAT64),0))>0.20)/COUNT(*) AS pct_orh_over20,
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64)) AS avg_stops_real,
    AVG(CAST(STOPS_PLANNED_SHPS AS FLOAT64)) AS avg_stops_planned,
    AVG(SAFE_DIVIDE(CAST(STOPS_REAL_SHPS AS FLOAT64)-CAST(STOPS_PLANNED_SHPS AS FLOAT64),
                    NULLIF(CAST(STOPS_PLANNED_SHPS AS FLOAT64),0))*100) AS avg_stops_gap_pct,
    SAFE_DIVIDE(SUM(CAST(DELIVERY_SHIPMENTS AS FLOAT64)),
                NULLIF(SUM(CAST(ORH AS FLOAT64)),0)) AS dpph_real,
    SAFE_DIVIDE(SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)),
                NULLIF(SUM(CAST(ORH_PLANNED AS FLOAT64)),0)) AS dpph_planned,
    SAFE_DIVIDE(
      SAFE_DIVIDE(SUM(CAST(DELIVERY_SHIPMENTS AS FLOAT64)),NULLIF(SUM(CAST(ORH AS FLOAT64)),0))
      - SAFE_DIVIDE(SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)),NULLIF(SUM(CAST(ORH_PLANNED AS FLOAT64)),0)),
      NULLIF(SAFE_DIVIDE(SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)),NULLIF(SUM(CAST(ORH_PLANNED AS FLOAT64)),0)),0)
    )*100 AS dpph_gap_pct,
    AVG(CAST(FACILITY_LOAD_TIME_MINUTES AS FLOAT64)) AS avg_facility_load_min,
    AVG(CAST(FACILITY_STAY_TIME_MINUTES AS FLOAT64)) AS avg_facility_stay_min,
    APPROX_TOP_COUNT(SHP_CYCLE_NAME_PLANNED, 1)[OFFSET(0)].value AS cycle_planned,
    COUNTIF(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ) IN (1,7))/COUNT(*) AS pct_weekend,
    AVG(CAST(LM_SPR_PLANNED AS FLOAT64)) AS avg_spr_planned,
    AVG(CAST(HEADROOM_MIN_PLANNED AS FLOAT64)) AS avg_headroom_planned
  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID='MLB' AND SHP_LG_TYPE='LAST_MILE'
    AND SHP_LG_FACILITY_ID = 'SSP17'
    AND SHP_LG_INIT_MONTH_TZ IN ('2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06')
  GROUP BY 1,2,3
)
SELECT p.is_churn, p.REFERENCE_MONTH, r.*
FROM pool p
JOIN routes_agg r ON p.driver_id=r.driver_id AND p.metrics_month=r.route_month
"""

df = client.query(q).to_dataframe()
print(f"Rows: {len(df)}, Churn: {df['is_churn'].mean():.1%}")
df.to_csv('/tmp/ssp17_v2.csv', index=False)

print("\nDone!")
