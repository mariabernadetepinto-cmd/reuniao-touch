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

# ── STEP 1: discover SPR columns ──
print("=== Colunas de success/delivery ===")
q_cols = """
SELECT column_name, data_type
FROM `meli-bi-data.WHOWNER.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'DM_SHP_ROUTES_LAST_MILE'
  AND (LOWER(column_name) LIKE '%success%'
    OR LOWER(column_name) LIKE '%delivered%'
    OR LOWER(column_name) LIKE '%first%attempt%'
    OR LOWER(column_name) LIKE '%attempt%'
    OR LOWER(column_name) LIKE '%spr%')
ORDER BY column_name
"""
df_cols = client.query(q_cols).to_dataframe()
print(df_cols.to_string(index=False))

# ── STEP 2: main query ──
print("\n=== Query principal ===")
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

    -- Volume
    COUNT(*) AS n_routes,
    COUNT(DISTINCT DATE_TRUNC(SHP_LG_INIT_DTTM_TZ, DAY)) AS active_days,
    SUM(CAST(DELIVERY_SHIPMENTS AS FLOAT64)) AS sum_deliveries,

    -- ORH
    AVG(SAFE_DIVIDE(CAST(ORH AS FLOAT64)-CAST(ORH_PLANNED AS FLOAT64),
                    NULLIF(CAST(ORH_PLANNED AS FLOAT64),0))*100) AS avg_orh_dev,
    COUNTIF(SAFE_DIVIDE(CAST(ORH AS FLOAT64)-CAST(ORH_PLANNED AS FLOAT64),
                        NULLIF(CAST(ORH_PLANNED AS FLOAT64),0))*100 > 20)
      / COUNT(*) AS pct_orh_over20,
    AVG(CAST(ORH AS FLOAT64)) AS avg_orh_abs,

    -- Paradas
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64)) AS avg_stops,
    AVG(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS avg_stops_commercial,
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64)) AS avg_km,

    -- Ciclo
    APPROX_TOP_COUNT(SHP_CYCLE_NAME_PLANNED, 1)[OFFSET(0)].value AS cycle_planned,
    APPROX_TOP_COUNT(SHP_CYCLE_REAL_AGG_DESC, 1)[OFFSET(0)].value AS cycle_real,

    -- Dia da semana predominante (1=Sun, 2=Mon, ..., 7=Sat)
    APPROX_TOP_COUNT(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ), 1)[OFFSET(0)].value AS mode_dow,
    -- % rotas em fim de semana
    COUNTIF(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ) IN (1,7)) / COUNT(*) AS pct_weekend,
    -- % rotas segunda/terça/quarta
    COUNTIF(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ) IN (2,3,4)) / COUNT(*) AS pct_midweek

  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID='MLB' AND SHP_LG_TYPE='LAST_MILE'
    AND SHP_LG_INIT_MONTH_TZ IN ('2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06')
  GROUP BY 1,2
)
SELECT p.is_churn, p.REFERENCE_MONTH, r.*
FROM pool p
JOIN routes_agg r ON p.driver_id=r.driver_id AND p.metrics_month=r.route_month
"""

df = client.query(q).to_dataframe()
print(f"Rows: {len(df)}, Churn: {df['is_churn'].mean():.1%}")
df.to_csv('/tmp/rampa_raw.csv', index=False)

# Filter to NH + NEWBIE
nh_nb = df[df['career'].isin(['NEW HIRE','NEWBIE'])].copy()
print(f"NH+NB: {len(nh_nb)} rows, Churn: {nh_nb['is_churn'].mean():.1%}")

careers_focus = ['NEW HIRE','NEWBIE']

# ══════════════════════════════════════════════
# 1. RAMPA: Volume inflection point
# ══════════════════════════════════════════════
print("\n=== RAMPA: Ponto de inflexão por n_routes ===")
print("(onde r inverte de sinal para NEWBIE)")

# Fine-grained buckets: 1..30+
buckets_routes = [(1,3),(4,6),(7,9),(10,12),(13,15),(16,18),(19,21),(22,24),(25,27),(28,99)]
bucket_labels_r = ['1-3','4-6','7-9','10-12','13-15','16-18','19-21','22-24','25-27','28+']

print("\nChurn rate por faixa de rotas/mês:")
rampa_results = {}
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c} (n={len(sub)}):")
    rampa_results[c] = []
    for (lo,hi), lbl in zip(buckets_routes, bucket_labels_r):
        g = sub[(sub['n_routes']>=lo) & (sub['n_routes']<=hi)]
        if len(g) >= 20:
            cr = g['is_churn'].mean()
            rampa_results[c].append({'bucket':lbl,'n':len(g),'churn_rate':cr,'lo':lo})
            print(f"    {lbl:8s}  n={len(g):5d}  churn={cr:.1%}")

# Where does it peak/valley?
for c in careers_focus:
    r = rampa_results[c]
    if len(r) >= 3:
        rates = [x['churn_rate'] for x in r]
        peak_idx = rates.index(max(rates))
        print(f"\n  → {c}: pico de churn em {r[peak_idx]['bucket']} rotas ({max(rates):.1%})")

# Same for active_days
print("\nChurn rate por faixa de dias ativos/mês:")
buckets_days = [(1,3),(4,6),(7,9),(10,12),(13,15),(16,18),(19,21),(22,24),(25,99)]
bucket_labels_d = ['1-3','4-6','7-9','10-12','13-15','16-18','19-21','22-24','25+']
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c}:")
    for (lo,hi), lbl in zip(buckets_days, bucket_labels_d):
        g = sub[(sub['active_days']>=lo) & (sub['active_days']<=hi)]
        if len(g) >= 10:
            print(f"    {lbl:8s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ══════════════════════════════════════════════
# 2. ORH: safe range for NH/NEWBIE
# ══════════════════════════════════════════════
print("\n=== ORH: faixa segura por carreira ===")
orh_buckets = [(-100,-30),(-30,-10),(-10,0),(0,10),(10,20),(20,35),(35,50),(50,100)]
orh_labels = ['<-30%','-30/-10%','-10/0%','0/10%','10/20%','20/35%','35/50%','>50%']
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c}:")
    for (lo,hi), lbl in zip(orh_buckets, orh_labels):
        g = sub[(sub['avg_orh_dev']>=lo) & (sub['avg_orh_dev']<hi)]
        if len(g) >= 10:
            print(f"    ORH {lbl:12s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ══════════════════════════════════════════════
# 3. PARADAS: safe range
# ══════════════════════════════════════════════
print("\n=== PARADAS (avg_stops/rota): faixa segura ===")
stop_pcts = [0,10,20,30,40,50,60,70,80,90,100]
for c in careers_focus:
    sub = df[df['career']==c].dropna(subset=['avg_stops'])
    percs = np.percentile(sub['avg_stops'], stop_pcts)
    print(f"\n  {c} — percentis avg_stops: {dict(zip(stop_pcts, [round(p,0) for p in percs]))}")
    # buckets by decile
    sub2 = sub.copy()
    sub2['decile'] = pd.qcut(sub2['avg_stops'], 10, labels=False, duplicates='drop')
    for d in range(10):
        g = sub2[sub2['decile']==d]
        if len(g) >= 10:
            lo_v = g['avg_stops'].min()
            hi_v = g['avg_stops'].max()
            print(f"    D{d+1:02d} [{lo_v:.0f}-{hi_v:.0f}]  n={len(g):4d}  churn={g['is_churn'].mean():.1%}")

# ══════════════════════════════════════════════
# 4. CICLO × DIA DA SEMANA
# ══════════════════════════════════════════════
print("\n=== Ciclo × Churn para NH/NEWBIE ===")
dow_map = {1:'Dom',2:'Seg',3:'Ter',4:'Qua',5:'Qui',6:'Sex',7:'Sáb'}
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c} — por ciclo planejado:")
    g = sub.groupby('cycle_planned')['is_churn'].agg(['mean','count'])
    g.columns=['churn_rate','n']
    g = g[g['n']>=15].sort_values('churn_rate',ascending=False)
    print(g.to_string())

print("\n=== Dia da semana × Churn para NH/NEWBIE ===")
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c}:")
    g = sub.groupby('mode_dow')['is_churn'].agg(['mean','count'])
    g.columns=['churn_rate','n']
    g.index = [dow_map.get(int(x), x) for x in g.index]
    print(g.sort_values('churn_rate',ascending=False).to_string())

print("\n=== % fim de semana × Churn (NH/NEWBIE) ===")
weekend_buckets = [(0,0.05),(0.05,0.15),(0.15,0.25),(0.25,0.40),(0.40,1.01)]
wk_labels = ['<5%','5-15%','15-25%','25-40%','>40%']
for c in careers_focus:
    sub = df[df['career']==c]
    print(f"\n  {c} — % rotas em fim de semana:")
    for (lo,hi),lbl in zip(weekend_buckets,wk_labels):
        g = sub[(sub['pct_weekend']>=lo) & (sub['pct_weekend']<hi)]
        if len(g)>=10:
            print(f"    {lbl:8s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ══════════════════════════════════════════════
# 5. ORH × pct_orh_over20 sweet spot
# ══════════════════════════════════════════════
print("\n=== % rotas com ORH>20%: faixa segura ===")
orh_pct_buckets = [(0,0.1),(0.1,0.2),(0.2,0.3),(0.3,0.4),(0.4,0.6),(0.6,1.01)]
orh_pct_labels = ['<10%','10-20%','20-30%','30-40%','40-60%','>60%']
for c in careers_focus + ['TENURED','VETERAN']:
    sub = df[df['career']==c]
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(orh_pct_buckets,orh_pct_labels):
        g = sub[(sub['pct_orh_over20']>=lo) & (sub['pct_orh_over20']<hi)]
        if len(g)>=15:
            print(f"    {lbl:8s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

print("\n=== KM médio por rota × churn (NH/NEWBIE) ===")
for c in careers_focus:
    sub = df[df['career']==c].dropna(subset=['avg_km'])
    km_buckets = [(0,40),(40,60),(60,80),(80,100),(100,130),(130,999)]
    km_labels = ['<40km','40-60km','60-80km','80-100km','100-130km','>130km']
    print(f"\n  {c}:")
    for (lo,hi),lbl in zip(km_buckets,km_labels):
        g = sub[(sub['avg_km']>=lo) & (sub['avg_km']<hi)]
        if len(g)>=10:
            print(f"    {lbl:10s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

print("\nDone!")
