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

print("=== Query: valores planejados × churn ===")
q = """
WITH pool AS (
  SELECT
    CAST(cm.CUS_CUST_ID AS INT64) AS driver_id,
    cm.REFERENCE_MONTH,
    cm.NEW_CHURN_AT_CM AS is_churn,
    CASE WHEN cm.NEW_CHURN_AT_CM
         THEN DATE_SUB(cm.REFERENCE_MONTH, INTERVAL 1 MONTH)
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
    MAX(SHP_LG_DRIVER_USER_ID_CAREER) AS career,
    COUNT(*) AS n_routes,

    -- ──── PLANEJADO ────
    -- Paradas planejadas (média por rota no mês)
    AVG(CAST(STOPS_PLANNED_SHPS AS FLOAT64))       AS avg_stops_planned,
    AVG(CAST(STOPS_PLANNED AS FLOAT64))             AS avg_stops_total_planned,

    -- ORH planejado
    AVG(CAST(ORH_PLANNED AS FLOAT64))               AS avg_orh_planned,

    -- DPPH planejado = pacotes planejados / ORH planejado
    SAFE_DIVIDE(
      SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)),
      SUM(CAST(ORH_PLANNED AS FLOAT64))
    )                                               AS dpph_planned,

    -- Pacotes planejados por parada
    AVG(CAST(AVG_SHPS_STOP_PLANNED AS FLOAT64))     AS avg_shps_per_stop_planned,

    -- Tempo planejado por parada (minutos)
    AVG(CAST(AVG_MIN_STOP_PLANNED AS FLOAT64))      AS avg_min_per_stop_planned,

    -- Tempo planejado de trânsito (min/km)
    AVG(CAST(AVG_MIN_KM_TRANSIT_PLANNED AS FLOAT64)) AS avg_min_km_transit_planned,

    -- KM máximo planejado da rota
    AVG(CAST(MAX_KM_ROUTE_PLANNED AS FLOAT64))      AS avg_max_km_planned,

    -- Headroom planejado (minutos de folga)
    AVG(CAST(HEADROOM_MIN_PLANNED AS FLOAT64))      AS avg_headroom_planned,

    -- SPR planejado
    AVG(CAST(LM_SPR_PLANNED AS FLOAT64))            AS avg_spr_planned,

    -- Horário de saída planejado
    AVG(CAST(STEM_OUT_HOUR_PLANNED AS FLOAT64))     AS avg_stem_out_hour_planned,

    -- Pacotes despachados planejados (volume total mês)
    SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)) AS sum_shps_planned,

    -- Ocupação m3 planejada
    AVG(CAST(M3_OCCUPANCY_PLANNED AS FLOAT64))      AS avg_m3_occupancy_planned,

    -- ──── REALIZADO (para comparação delta) ────
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64))           AS avg_stops_real,
    AVG(CAST(ORH AS FLOAT64))                       AS avg_orh_real,
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64))      AS avg_km_real,

    -- Delta: real vs planejado
    AVG(SAFE_DIVIDE(
      CAST(ORH AS FLOAT64) - CAST(ORH_PLANNED AS FLOAT64),
      NULLIF(CAST(ORH_PLANNED AS FLOAT64),0)
    ) * 100)                                        AS avg_orh_dev_pct,

    AVG(SAFE_DIVIDE(
      CAST(STOPS_REAL_SHPS AS FLOAT64) - CAST(STOPS_PLANNED_SHPS AS FLOAT64),
      NULLIF(CAST(STOPS_PLANNED_SHPS AS FLOAT64),0)
    ) * 100)                                        AS avg_stops_dev_pct

  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID='MLB' AND SHP_LG_TYPE='LAST_MILE'
    AND SHP_LG_INIT_MONTH_TZ IN (
      '2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06'
    )
  GROUP BY 1,2
)
SELECT p.is_churn, p.REFERENCE_MONTH, r.*
FROM pool p
JOIN routes_agg r ON p.driver_id=r.driver_id AND p.metrics_month=r.route_month
"""

df = client.query(q).to_dataframe()
print(f"Rows: {len(df)}, Churn: {df['is_churn'].mean():.1%}")
df.to_csv('/tmp/planned_raw.csv', index=False)

planned_cols = [
    'avg_stops_planned','avg_stops_total_planned','avg_orh_planned',
    'dpph_planned','avg_shps_per_stop_planned','avg_min_per_stop_planned',
    'avg_min_km_transit_planned','avg_max_km_planned','avg_headroom_planned',
    'avg_spr_planned','avg_stem_out_hour_planned','sum_shps_planned',
    'avg_m3_occupancy_planned','avg_orh_dev_pct','avg_stops_dev_pct'
]
careers = ['NEW HIRE','NEWBIE','TENURED','VETERAN']

# ── Correlações ──
print("\n=== Correlações point-biserial: PLANEJADO × Churn ===")
results = []
for m in planned_cols:
    if m not in df.columns: continue
    sub = df[df[m].notna() & np.isfinite(df[m])]
    if len(sub) < 100: continue
    r_all, p_all = stats.pointbiserialr(sub['is_churn'].astype(int), sub[m])
    row = {'metric':m, 'ALL':round(r_all,4), 'p_val':round(p_all,6)}
    for c in careers:
        sc = sub[sub['career']==c]
        if len(sc) > 30:
            rc, _ = stats.pointbiserialr(sc['is_churn'].astype(int), sc[m])
            row[c] = round(rc,4)
        else:
            row[c] = None
    results.append(row)

df_corr = pd.DataFrame(results).sort_values('ALL', key=abs, ascending=False)
print(df_corr.to_string(index=False))

# ── Percentis planejados: retidos vs churned por carreira ──
print("\n=== Percentis PLANEJADOS: Retidos vs Churned ===")
key_planned = ['dpph_planned','avg_stops_planned','avg_orh_planned',
               'avg_headroom_planned','avg_min_per_stop_planned',
               'avg_max_km_planned','avg_spr_planned','avg_stem_out_hour_planned']
for m in key_planned:
    if m not in df.columns: continue
    print(f"\n  {m}:")
    for c in careers:
        sub = df[(df['career']==c) & df[m].notna() & np.isfinite(df[m])]
        if len(sub) < 30: continue
        ret = sub[~sub['is_churn']][m]
        chu = sub[sub['is_churn']][m]
        if len(ret)<10 or len(chu)<10: continue
        rp = np.percentile(ret,[25,50,75])
        cp = np.percentile(chu,[25,50,75])
        print(f"    {c:12s}  Retidos P25={rp[0]:.1f} P50={rp[1]:.1f} P75={rp[2]:.1f}  |  Churned P25={cp[0]:.1f} P50={cp[1]:.1f} P75={cp[2]:.1f}")

# ── DPPH planejado por bucket: churn rate por carreira ──
print("\n=== DPPH Planejado × Churn (buckets) ===")
dpph_buckets = [(0,8),(8,10),(10,12),(12,14),(14,16),(16,18),(18,22),(22,99)]
dpph_labels  = ['<8','8-10','10-12','12-14','14-16','16-18','18-22','>22']
for c in careers:
    sub = df[(df['career']==c) & df['dpph_planned'].notna() & np.isfinite(df['dpph_planned'])]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(dpph_buckets,dpph_labels):
        g = sub[(sub['dpph_planned']>=lo) & (sub['dpph_planned']<hi)]
        if len(g)>=15:
            print(f"    DPPH plan {lbl:6s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ── Headroom planejado × churn ──
print("\n=== Headroom planejado (minutos de folga) × Churn ===")
hr_buckets = [(-999,-30),(-30,0),(0,15),(15,30),(30,60),(60,999)]
hr_labels  = ['<−30min','−30/0','0/15min','15/30min','30/60min','>60min']
for c in careers:
    sub = df[(df['career']==c) & df['avg_headroom_planned'].notna() & np.isfinite(df['avg_headroom_planned'])]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(hr_buckets,hr_labels):
        g = sub[(sub['avg_headroom_planned']>=lo) & (sub['avg_headroom_planned']<hi)]
        if len(g)>=15:
            print(f"    Headroom {lbl:12s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ── SPR planejado × churn ──
print("\n=== LM_SPR_PLANNED × Churn ===")
spr_buckets = [(0,60),(60,70),(70,80),(80,85),(85,90),(90,95),(95,101)]
spr_labels  = ['<60%','60-70%','70-80%','80-85%','85-90%','90-95%','>95%']
for c in careers:
    sub = df[(df['career']==c) & df['avg_spr_planned'].notna() & np.isfinite(df['avg_spr_planned'])]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(spr_buckets,spr_labels):
        g = sub[(sub['avg_spr_planned']>=lo) & (sub['avg_spr_planned']<hi)]
        if len(g)>=15:
            print(f"    SPR plan {lbl:8s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ── Horário de saída planejado × churn ──
print("\n=== Horário saída planejado × Churn ===")
stem_buckets = [(0,7),(7,9),(9,11),(11,13),(13,15),(15,24)]
stem_labels  = ['0-7h','7-9h','9-11h','11-13h','13-15h','>15h']
for c in careers:
    sub = df[(df['career']==c) & df['avg_stem_out_hour_planned'].notna() & np.isfinite(df['avg_stem_out_hour_planned'])]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(stem_buckets,stem_labels):
        g = sub[(sub['avg_stem_out_hour_planned']>=lo) & (sub['avg_stem_out_hour_planned']<hi)]
        if len(g)>=15:
            print(f"    Saída {lbl:8s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ── Paradas planejadas × churn NH/NEWBIE ──
print("\n=== Paradas planejadas/rota × Churn (NH e NEWBIE) ===")
stop_buckets = [(0,30),(30,40),(40,50),(50,60),(60,70),(70,99)]
stop_labels  = ['<30','30-40','40-50','50-60','60-70','>70']
for c in ['NEW HIRE','NEWBIE']:
    sub = df[(df['career']==c) & df['avg_stops_planned'].notna() & np.isfinite(df['avg_stops_planned'])]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(stop_buckets,stop_labels):
        g = sub[(sub['avg_stops_planned']>=lo) & (sub['avg_stops_planned']<hi)]
        if len(g)>=10:
            print(f"    Stops plan {lbl:6s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

print("\n=== Médias planejadas: Retidos vs Churned por carreira ===")
for m in ['dpph_planned','avg_stops_planned','avg_orh_planned','avg_headroom_planned','avg_spr_planned']:
    if m not in df.columns: continue
    print(f"\n  {m}:")
    for c in careers:
        sub = df[(df['career']==c) & df[m].notna() & np.isfinite(df[m])]
        if len(sub)<30: continue
        ret_mean = sub[~sub['is_churn']][m].mean()
        chu_mean = sub[sub['is_churn']][m].mean()
        delta = chu_mean - ret_mean
        print(f"    {c:12s}  Retidos={ret_mean:.2f}  Churned={chu_mean:.2f}  Δ={delta:+.2f}")

print("\nDone!")
