import os, json, sys
from google.oauth2.credentials import Credentials
from google.cloud import bigquery
import pandas as pd
from scipy import stats
import numpy as np

def get_client():
    creds_data = json.loads(os.environ.get("GCP_CREDENTIALS"))
    creds = Credentials(
        token=None, refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"], client_secret=creds_data["client_secret"]
    )
    return bigquery.Client(project=creds_data["quota_project_id"], credentials=creds)

client = get_client()

# ── STEP 1: discover vehicle-type column name ──
print("=== Verificando colunas de veículo ===")
q_cols = """
SELECT column_name, data_type
FROM `meli-bi-data.WHOWNER.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'DM_SHP_ROUTES_LAST_MILE'
  AND LOWER(column_name) LIKE '%vehicle%'
  OR (table_name = 'DM_SHP_ROUTES_LAST_MILE' AND LOWER(column_name) LIKE '%veh%')
  OR (table_name = 'DM_SHP_ROUTES_LAST_MILE' AND LOWER(column_name) LIKE '%truck%')
  OR (table_name = 'DM_SHP_ROUTES_LAST_MILE' AND LOWER(column_name) LIKE '%moto%')
  OR (table_name = 'DM_SHP_ROUTES_LAST_MILE' AND LOWER(column_name) LIKE '%car%')
  OR (table_name = 'DM_SHP_ROUTES_LAST_MILE' AND LOWER(column_name) LIKE '%tipo%')
"""
try:
    df_cols = client.query(q_cols).to_dataframe()
    if len(df_cols):
        print(df_cols.to_string(index=False))
    else:
        print("Nenhuma coluna de veículo encontrada via INFORMATION_SCHEMA, vou tentar direto")
except Exception as e:
    print(f"INFORMATION_SCHEMA erro: {e}")

# ── STEP 2: sample to find vehicle column ──
print("\n=== Sample para descobrir colunas de veículo ===")
q_sample = """
SELECT * EXCEPT(SHP_LG_ROUTE_ID)
FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
WHERE SHP_SITE_ID = 'MLB' AND SHP_LG_TYPE = 'LAST_MILE'
  AND SHP_LG_INIT_MONTH_TZ = '2026-M03'
LIMIT 1
"""
try:
    df_s = client.query(q_sample).to_dataframe()
    veh_cols = [c for c in df_s.columns if any(k in c.upper() for k in ['VEHICLE','VEH','MOTO','TRUCK','TIPO_VEICULO','VEICULO','MODALITY','MODAL'])]
    print(f"Colunas candidatas de veículo: {veh_cols}")
    for c in veh_cols[:5]:
        print(f"  {c}: {df_s[c].iloc[0]}")
except Exception as e:
    print(f"Sample erro: {e}")

# ── STEP 3: full correlation analysis with all variables ──
print("\n=== Query principal: todos os fatores + veículo ===")

# Try with vehicle type - use SHP_LG_VEHICLE_TYPE_DESC or similar
# Also include all key numeric + categorical vars
q_main = """
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
      SELECT 1 FROM `meli-bi-data.WHOWNER.LK_DRIVERS_RFL_STATUS_MONTHLY` rfl
      WHERE rfl.CUS_CUST_ID = cm.CUS_CUST_ID
        AND rfl.REFERENCE_MONTH = cm.REFERENCE_MONTH
        AND rfl.SIT_SITE_ID = 'MLB' AND rfl.COMPANY_TYPE = 'MLP'
        AND rfl.SHP_LG_TYPE = 'last_mile'
        AND (rfl.NEW_USER=TRUE OR rfl.REACQUIRE_USER=TRUE OR rfl.REACTIVE_USER=TRUE)
    )
),
routes_agg AS (
  SELECT
    CAST(SHP_LG_DRIVER_USER_ID AS INT64) AS driver_id,
    PARSE_DATE('%Y-M%m', SHP_LG_INIT_MONTH_TZ) AS route_month,
    MAX(SHP_LG_DRIVER_USER_ID_CAREER) AS career,

    -- Volume (soma mensal — proxy de engajamento)
    COUNT(*) AS n_routes,
    SUM(CAST(DELIVERY_SHIPMENTS AS FLOAT64)) AS sum_deliveries,
    SUM(CAST(STOPS_REAL_SHPS AS FLOAT64)) AS sum_stops,
    SUM(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS sum_stops_commercial,
    SUM(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS sum_km,

    -- Média por rota
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64)) AS avg_stops,
    AVG(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS avg_stops_commercial,
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS avg_km,
    SAFE_DIVIDE(SUM(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)),
                SUM(CAST(STOPS_REAL_SHPS AS FLOAT64))) AS pct_commercial,

    -- ORH deviation
    AVG(SAFE_DIVIDE(
      CAST(ORH AS FLOAT64) - CAST(ORH_PLANNED AS FLOAT64),
      NULLIF(CAST(ORH_PLANNED AS FLOAT64),0)
    ) * 100) AS avg_orh_dev,
    COUNTIF(SAFE_DIVIDE(
      CAST(ORH AS FLOAT64) - CAST(ORH_PLANNED AS FLOAT64),
      NULLIF(CAST(ORH_PLANNED AS FLOAT64),0)
    ) * 100 > 20) / COUNT(*) AS pct_routes_orh_over20,

    -- DPPH
    SAFE_DIVIDE(
      SUM(CAST(DELIVERY_SHIPMENTS AS FLOAT64)),
      SUM(CASE WHEN ROUTE_TYPE_1 = 'RUTA CON PAQUETES PARA ENTREGA'
               THEN IF(CAST(ORH AS FLOAT64)=0, 1, CAST(ORH AS FLOAT64))
               ELSE 1 END)
    ) AS dpph,

    -- Ciclo predominante
    APPROX_TOP_COUNT(SHP_CYCLE_NAME_PLANNED, 1)[OFFSET(0)].value AS cycle_planned_mode,
    APPROX_TOP_COUNT(SHP_CYCLE_REAL_AGG_DESC, 1)[OFFSET(0)].value AS cycle_real_mode,

    -- Active days
    COUNT(DISTINCT DATE_TRUNC(SHP_LG_INIT_DTTM_TZ, DAY)) AS active_days,

    -- Veículo
    APPROX_TOP_COUNT(SHP_LG_VEHICLE_TYPE_AGG, 1)[OFFSET(0)].value AS vehicle_type,
    APPROX_TOP_COUNT(SHP_LG_VEHICLE_TYPE, 1)[OFFSET(0)].value AS vehicle_type_detail,
    CAST(MAX(CAST(SHP_LG_VEHICLE_TYPE_IS_RENTAL AS INT64)) AS BOOL) AS is_rental,
    CAST(MAX(CAST(SHP_LG_VEHICLE_TYPE_IS_LEASED AS INT64)) AS BOOL) AS is_leased,
    AVG(CAST(SHP_LG_VEHICLE_CAPACITY_M3 AS FLOAT64)) AS avg_capacity_m3

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
  p.REFERENCE_MONTH,
  r.*
FROM pool p
JOIN routes_agg r ON p.driver_id = r.driver_id AND p.metrics_month = r.route_month
"""

print("Rodando query principal...")
try:
    df = client.query(q_main).to_dataframe()
    print(f"Rows: {len(df)}, Churn: {df['is_churn'].mean():.1%}")
    df.to_csv('/tmp/churn_full_v2.csv', index=False)
    print("Saved /tmp/churn_full_v2.csv")
except Exception as e:
    print(f"Erro query principal: {e}")
    # fallback sem vehicle_type coalesce
    q_main2 = q_main.replace(
        """    APPROX_TOP_COUNT(
      COALESCE(
        CAST(SHP_LG_VEHICLE_TYPE AS STRING),
        CAST(SHP_LG_VEHICLE_TYPE_DESC AS STRING)
      ), 1
    )[OFFSET(0)].value AS vehicle_type""",
        """    CAST(NULL AS STRING) AS vehicle_type"""
    )
    print("Tentando fallback sem vehicle_type...")
    df = client.query(q_main2).to_dataframe()
    print(f"Rows: {len(df)}, Churn: {df['is_churn'].mean():.1%}")
    df.to_csv('/tmp/churn_full_v2.csv', index=False)

# ── Correlações ──
print("\n=== Correlações Point-Biserial ===")
numeric_cols = [
    'n_routes','sum_deliveries','sum_stops','sum_stops_commercial','sum_km',
    'avg_stops','avg_stops_commercial','avg_km','pct_commercial',
    'avg_orh_dev','pct_routes_orh_over20','dpph','active_days','avg_capacity_m3'
]
careers = ['NEW HIRE','NEWBIE','TENURED','VETERAN']

all_results = []
for m in numeric_cols:
    if m not in df.columns:
        continue
    sub = df[df[m].notna() & np.isfinite(df[m])]
    r_all, p_all = stats.pointbiserialr(sub['is_churn'].astype(int), sub[m])
    row = {'metric': m, 'ALL': round(r_all,4), 'p_val': round(p_all,6)}
    for c in careers:
        sc = sub[sub['career']==c]
        if len(sc) > 50:
            r_c, _ = stats.pointbiserialr(sc['is_churn'].astype(int), sc[m])
            row[c] = round(r_c,4)
        else:
            row[c] = None
    all_results.append(row)

df_corr = pd.DataFrame(all_results).sort_values('ALL', key=abs, ascending=False)
print(df_corr.to_string(index=False))
df_corr.to_csv('/tmp/correlations_v2.csv', index=False)

# ── Percentis por status de churn ──
print("\n=== Percentis: Retidos vs Churned ===")
for m in numeric_cols:
    if m not in df.columns:
        continue
    sub = df[df[m].notna() & np.isfinite(df[m])]
    ret = sub[~sub['is_churn']][m]
    chu = sub[sub['is_churn']][m]
    percs = [25,50,75,90]
    rp = np.percentile(ret, percs)
    cp = np.percentile(chu, percs)
    print(f"\n  {m}:")
    print(f"    Retidos  P25={rp[0]:.1f}  P50={rp[1]:.1f}  P75={rp[2]:.1f}  P90={rp[3]:.1f}")
    print(f"    Churned  P25={cp[0]:.1f}  P50={cp[1]:.1f}  P75={cp[2]:.1f}  P90={cp[3]:.1f}")

# ── Tipo de veículo × churn ──
print("\n=== Tipo de veículo × Churn ===")
if 'vehicle_type' in df.columns and df['vehicle_type'].notna().sum() > 100:
    vt = df.groupby('vehicle_type').agg(
        n=('is_churn','count'), churn_rate=('is_churn','mean')
    ).sort_values('n', ascending=False)
    print(vt.head(20).to_string())
    df_vt = df.groupby(['vehicle_type','career']).agg(
        n=('is_churn','count'), churn_rate=('is_churn','mean')
    ).reset_index()
    df_vt[df_vt['n']>=30].sort_values(['career','churn_rate'],ascending=[True,False]).to_csv('/tmp/vehicle_career.csv',index=False)
    print("\nSaved /tmp/vehicle_career.csv")
else:
    print("vehicle_type não disponível ou vazio — tentando query dedicada")
    # Try to find the actual column name
    q_veh = """
    SELECT column_name, data_type
    FROM `meli-bi-data.WHOWNER.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = 'DM_SHP_ROUTES_LAST_MILE'
    ORDER BY column_name
    """
    try:
        df_all_cols = client.query(q_veh).to_dataframe()
        veh_candidates = df_all_cols[df_all_cols['column_name'].str.upper().str.contains('VEH|VEHICLE|MOTO|TRUCK|MODAL|TRANSP')]
        print("Colunas candidatas veículo:")
        print(veh_candidates.to_string(index=False))
    except Exception as e:
        print(f"Erro: {e}")

print("\nDone!")
