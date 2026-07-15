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

print("=== SSP17 × Churn — análise completa mês a mês 2026 ===")

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
    CAST(SHP_LG_DRIVER_USER_ID AS INT64)          AS driver_id,
    PARSE_DATE('%Y-M%m', SHP_LG_INIT_MONTH_TZ)    AS route_month,
    SHP_LG_FACILITY_ID                             AS facility_id,
    MAX(SHP_LG_DRIVER_USER_ID_CAREER)              AS career,
    MAX(SHP_LG_VEHICLE_TYPE_AGG)                   AS vehicle_type,

    COUNT(*)                                       AS n_routes,
    COUNT(DISTINCT DATE_TRUNC(SHP_LG_INIT_DTTM_TZ, DAY)) AS active_days,

    -- Paradas
    AVG(CAST(STOPS_REAL_SHPS AS FLOAT64))          AS avg_stops,
    AVG(CAST(STOPS_PLANNED_SHPS AS FLOAT64))       AS avg_stops_planned,
    AVG(CAST(STOPS_REAL_SHPS_COMMERCIAL AS FLOAT64)) AS avg_stops_commercial,

    -- ORH deviation
    AVG(SAFE_DIVIDE(
      CAST(ORH AS FLOAT64) - CAST(ORH_PLANNED AS FLOAT64),
      NULLIF(CAST(ORH_PLANNED AS FLOAT64), 0)
    ) * 100)                                       AS avg_orh_dev_pct,
    COUNTIF(SAFE_DIVIDE(
      CAST(ORH AS FLOAT64) - CAST(ORH_PLANNED AS FLOAT64),
      NULLIF(CAST(ORH_PLANNED AS FLOAT64), 0)
    ) > 0.20) / COUNT(*)                          AS pct_orh_over20,
    AVG(CAST(ORH AS FLOAT64))                      AS avg_orh_abs,
    AVG(CAST(ORH_PLANNED AS FLOAT64))              AS avg_orh_planned,

    -- DPPH planned
    SAFE_DIVIDE(
      SUM(CAST(SHIPMENTS_DISPATCHED_PLANNED AS FLOAT64)),
      SUM(CAST(ORH_PLANNED AS FLOAT64))
    )                                              AS dpph_planned,

    -- SPR / headroom planejados
    AVG(CAST(LM_SPR_PLANNED AS FLOAT64))           AS avg_spr_planned,
    AVG(CAST(HEADROOM_MIN_PLANNED AS FLOAT64))     AS avg_headroom_planned,

    -- KM
    AVG(CAST(ODOMETER_DISTANCE_KM AS FLOAT64))     AS avg_km,

    -- Ciclo
    APPROX_TOP_COUNT(SHP_CYCLE_NAME_PLANNED, 1)[OFFSET(0)].value AS cycle_planned,

    -- Dia da semana
    APPROX_TOP_COUNT(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ), 1)[OFFSET(0)].value AS mode_dow,
    COUNTIF(EXTRACT(DAYOFWEEK FROM SHP_LG_INIT_DTTM_TZ) IN (1,7)) / COUNT(*) AS pct_weekend,

    -- M3 e SPR real
    AVG(CAST(M3_OCCUPANCY_PLANNED AS FLOAT64))     AS avg_m3_planned

  FROM `meli-bi-data.WHOWNER.DM_SHP_ROUTES_LAST_MILE`
  WHERE SHP_SITE_ID='MLB' AND SHP_LG_TYPE='LAST_MILE'
    AND SHP_LG_INIT_MONTH_TZ IN (
      '2025-M12','2026-M01','2026-M02','2026-M03','2026-M04','2026-M05','2026-M06'
    )
  GROUP BY 1,2,3
)
SELECT
  p.is_churn,
  p.REFERENCE_MONTH,
  r.*
FROM pool p
JOIN routes_agg r
  ON p.driver_id = r.driver_id
  AND p.metrics_month = r.route_month
"""

df_all = client.query(q).to_dataframe()
print(f"Total MLB pool: {len(df_all)} rows, churn geral: {df_all['is_churn'].mean():.1%}")
df_all.to_csv('/tmp/ssp17_all.csv', index=False)

# Split SSP17 vs resto
df_ssp17 = df_all[df_all['facility_id'] == 'SSP17'].copy()
df_rest   = df_all[df_all['facility_id'] != 'SSP17'].copy()
print(f"\nSSP17: {len(df_ssp17)} rows, churn={df_ssp17['is_churn'].mean():.1%}")
print(f"Resto: {len(df_rest)} rows, churn={df_rest['is_churn'].mean():.1%}")

# ══ 1. Churn mês a mês ══
print("\n=== 1. Churn rate mês a mês — SSP17 vs MLB ===")
months_order = sorted(df_all['REFERENCE_MONTH'].unique())
rows = []
for m in months_order:
    s17 = df_ssp17[df_ssp17['REFERENCE_MONTH'] == m]
    mlb = df_all[df_all['REFERENCE_MONTH'] == m]
    row = {
        'mes': str(m)[:7],
        'ssp17_n': len(s17),
        'ssp17_churn': s17['is_churn'].mean() if len(s17) else None,
        'mlb_n': len(mlb),
        'mlb_churn': mlb['is_churn'].mean() if len(mlb) else None,
    }
    if row['ssp17_churn'] and row['mlb_churn']:
        row['delta_pp'] = (row['ssp17_churn'] - row['mlb_churn']) * 100
    else:
        row['delta_pp'] = None
    rows.append(row)
df_mes = pd.DataFrame(rows)
print(df_mes.to_string(index=False))
df_mes.to_csv('/tmp/ssp17_mes_a_mes.csv', index=False)

# ══ 2. Churn por carreira × mês ══
print("\n=== 2. SSP17 — Churn por carreira × mês ===")
careers = ['NEW HIRE','NEWBIE','TENURED','VETERAN']
for c in careers:
    sub = df_ssp17[df_ssp17['career'] == c]
    print(f"\n  {c} (n={len(sub)}):")
    for m in months_order:
        sm = sub[sub['REFERENCE_MONTH'] == m]
        if len(sm) >= 5:
            print(f"    {str(m)[:7]}  n={len(sm):4d}  churn={sm['is_churn'].mean():.1%}")

# ══ 3. Tipo de veículo × churn ══
print("\n=== 3. SSP17 — Veículo × Churn ===")
veh = df_ssp17.groupby('vehicle_type')['is_churn'].agg(['mean','count']).rename(columns={'mean':'churn','count':'n'})
veh = veh[veh['n'] >= 15].sort_values('churn', ascending=False)
print(veh.to_string())

print("\n  Veículo × Churn por mês:")
for m in months_order:
    sm = df_ssp17[df_ssp17['REFERENCE_MONTH'] == m]
    g = sm.groupby('vehicle_type')['is_churn'].agg(['mean','count']).rename(columns={'mean':'churn','count':'n'})
    g = g[g['n'] >= 10].sort_values('churn', ascending=False)
    print(f"\n  {str(m)[:7]}:")
    print(g.to_string())

# ══ 4. ORH deviation × churn ══
print("\n=== 4. SSP17 vs MLB — ORH deviation médio ===")
print(f"  SSP17 avg_orh_dev_pct: {df_ssp17['avg_orh_dev_pct'].median():.1f}% (mediana)")
print(f"  MLB    avg_orh_dev_pct: {df_all['avg_orh_dev_pct'].median():.1f}% (mediana)")

print("\n  SSP17 — ORH deviation por bucket × churn:")
orh_buckets = [(-100,-30),(-30,-10),(-10,0),(0,10),(10,20),(20,35),(35,60),(60,200)]
orh_labels  = ['<-30%','-30/-10%','-10/0%','0/10%','10/20%','20/35%','35/60%','>60%']
for (lo,hi),lbl in zip(orh_buckets,orh_labels):
    g = df_ssp17[(df_ssp17['avg_orh_dev_pct']>=lo) & (df_ssp17['avg_orh_dev_pct']<hi)]
    if len(g) >= 10:
        print(f"    ORH {lbl:12s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

print("\n  MLB (excl SSP17) — ORH deviation por bucket × churn:")
for (lo,hi),lbl in zip(orh_buckets,orh_labels):
    g = df_rest[(df_rest['avg_orh_dev_pct']>=lo) & (df_rest['avg_orh_dev_pct']<hi)]
    if len(g) >= 10:
        print(f"    ORH {lbl:12s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ══ 5. Paradas × churn ══
print("\n=== 5. SSP17 — Paradas × Churn ===")
print(f"  avg_stops SSP17: {df_ssp17['avg_stops'].median():.1f} | MLB: {df_all['avg_stops'].median():.1f}")

stop_buckets = [(0,20),(20,30),(30,40),(40,50),(50,60),(60,70),(70,99)]
stop_labels  = ['<20','20-30','30-40','40-50','50-60','60-70','>70']
print("\n  SSP17 — paradas/rota × churn:")
for (lo,hi),lbl in zip(stop_buckets,stop_labels):
    g = df_ssp17[(df_ssp17['avg_stops']>=lo) & (df_ssp17['avg_stops']<hi)]
    if len(g) >= 10:
        print(f"    Stops {lbl:6s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")

# ══ 6. Dia da semana × churn ══
print("\n=== 6. SSP17 — Dia da semana × Churn ===")
dow_map = {1:'Dom',2:'Seg',3:'Ter',4:'Qua',5:'Qui',6:'Sex',7:'Sáb'}
g = df_ssp17.groupby('mode_dow')['is_churn'].agg(['mean','count']).rename(columns={'mean':'churn','count':'n'})
g.index = [dow_map.get(int(x), x) for x in g.index]
print(g.sort_values('churn', ascending=False).to_string())

print("\n  SSP17 % fim de semana × churn:")
wk_buckets = [(0,0.05),(0.05,0.15),(0.15,0.25),(0.25,0.40),(0.40,1.01)]
wk_labels  = ['<5%','5-15%','15-25%','25-40%','>40%']
for (lo,hi),lbl in zip(wk_buckets,wk_labels):
    g2 = df_ssp17[(df_ssp17['pct_weekend']>=lo) & (df_ssp17['pct_weekend']<hi)]
    if len(g2) >= 10:
        print(f"    FDS {lbl:8s}  n={len(g2):5d}  churn={g2['is_churn'].mean():.1%}")

# ══ 7. DPPH planejado × churn ══
print("\n=== 7. SSP17 vs MLB — DPPH planejado × churn ===")
dpph_buckets = [(0,8),(8,10),(10,12),(12,14),(14,16),(16,18),(18,22),(22,99)]
dpph_labels  = ['<8','8-10','10-12','12-14','14-16','16-18','18-22','>22']
print("\n  SSP17:")
for (lo,hi),lbl in zip(dpph_buckets,dpph_labels):
    g = df_ssp17[(df_ssp17['dpph_planned']>=lo) & (df_ssp17['dpph_planned']<hi)]
    if len(g) >= 10:
        print(f"    DPPH {lbl:6s}  n={len(g):5d}  churn={g['is_churn'].mean():.1%}")
print(f"\n  SSP17 médias: retidos dpph={df_ssp17[~df_ssp17['is_churn']]['dpph_planned'].mean():.2f}  churned={df_ssp17[df_ssp17['is_churn']]['dpph_planned'].mean():.2f}")
print(f"  MLB   médias: retidos dpph={df_all[~df_all['is_churn']]['dpph_planned'].mean():.2f}  churned={df_all[df_all['is_churn']]['dpph_planned'].mean():.2f}")

# ══ 8. Ciclo × churn ══
print("\n=== 8. SSP17 — Ciclo × Churn ===")
g = df_ssp17.groupby('cycle_planned')['is_churn'].agg(['mean','count']).rename(columns={'mean':'churn','count':'n'})
g = g[g['n'] >= 15].sort_values('churn', ascending=False)
print(g.to_string())

# ══ 9. Correlações point-biserial ══
print("\n=== 9. Correlações point-biserial: SSP17 vs MLB ===")
metric_cols = ['avg_stops','avg_stops_planned','avg_orh_dev_pct','pct_orh_over20',
               'dpph_planned','avg_spr_planned','avg_headroom_planned',
               'avg_km','n_routes','active_days','pct_weekend',
               'avg_orh_abs','avg_orh_planned','avg_m3_planned']
results = []
for m in metric_cols:
    row = {'metric': m}
    for label, sub in [('SSP17', df_ssp17), ('MLB', df_all)]:
        valid = sub[sub[m].notna() & np.isfinite(sub[m])]
        if len(valid) >= 30:
            r, p = stats.pointbiserialr(valid['is_churn'].astype(int), valid[m])
            row[label] = round(r, 4)
            row[f'{label}_p'] = round(p, 6)
        else:
            row[label] = None
    results.append(row)
df_corr = pd.DataFrame(results).sort_values('SSP17', key=abs, ascending=False)
print(df_corr[['metric','SSP17','SSP17_p','MLB']].to_string(index=False))
df_corr.to_csv('/tmp/ssp17_correlations.csv', index=False)

# ══ 10. Comparação de médias SSP17 vs MLB ══
print("\n=== 10. Médias chave: SSP17 vs MLB (retidos vs churned) ===")
compare_cols = ['avg_stops','avg_orh_dev_pct','dpph_planned','avg_spr_planned',
                'avg_headroom_planned','avg_km','n_routes','active_days']
for m in compare_cols:
    ssp_ret = df_ssp17[~df_ssp17['is_churn']][m].mean()
    ssp_chu = df_ssp17[df_ssp17['is_churn']][m].mean()
    mlb_ret = df_all[~df_all['is_churn']][m].mean()
    mlb_chu = df_all[df_all['is_churn']][m].mean()
    print(f"  {m:28s}  SSP17 ret={ssp_ret:.2f} chu={ssp_chu:.2f} Δ={ssp_chu-ssp_ret:+.2f}  |  MLB ret={mlb_ret:.2f} chu={mlb_chu:.2f} Δ={mlb_chu-mlb_ret:+.2f}")

print("\nDone!")
