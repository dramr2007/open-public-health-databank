import io
import os
import time
import json
import requests
import pandas as pd
import duckdb
from pathlib import Path

# اختياري: لربط Google Drive لحفظ البيانات بشكل دائم، أزل علامة # من السطرين التاليين
# from google.colab import drive
# drive.mount('/content/drive')
# BASE_DIR = Path('/content/drive/MyDrive/open_health_data_bank')

BASE_DIR = Path(os.environ.get('DATABANK_DIR', './data'))
BRONZE_DIR = BASE_DIR / 'bronze'
SILVER_DIR = BASE_DIR / 'silver'
GOLD_DIR   = BASE_DIR / 'gold'

for d in [BRONZE_DIR, SILVER_DIR, GOLD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

HEADERS = {'User-Agent': 'OpenPublicHealthDataBank/1.0 (Research & Public Health)'}
print(f"✅ تم تجهيز هيكل بنك البيانات بنجاح في: {BASE_DIR}")
def fetch_nhanes_xpt(filename: str) -> pd.DataFrame:
    """يسحب ملف XPT من خوادم CDC مع مسار احتياطي ويحفظ نسخة خام في طبقة Bronze."""
    candidate_urls = [
        f"https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/{filename}.xpt",
        f"https://wwwn.cdc.gov/Nchs/Nhanes/2021-2022/{filename}.XPT",
    ]
    for url in candidate_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=45)
            if resp.status_code == 200 and len(resp.content) > 1000:
                raw_path = BRONZE_DIR / f"nhanes_{filename}.xpt"
                raw_path.write_bytes(resp.content)
                df = pd.read_sas(io.BytesIO(resp.content), format='xport')
                print(f"  ✓ تم سحب {filename} ({len(df):,} صف) من {url}")
                return df
        except Exception as e:
            print(f"  ⚠️ تعذر المسار {url}: {e}")
    raise RuntimeError(f"تعذر تحميل ملف NHANES: {filename}")

print("🔄 جاري سحب البيانات السريرية من CDC NHANES (دورة 2021-2023)...")
demo = fetch_nhanes_xpt("DEMO_L")[['SEQN', 'RIAGENDR', 'RIDAGEYR']]
ghb  = fetch_nhanes_xpt("GHB_L")[['SEQN', 'LBXGH']]       # السكر التراكمي HbA1c (%)
bmx  = fetch_nhanes_xpt("BMX_L")[['SEQN', 'BMXBMI']]      # مؤشر كتلة الجسم BMI

# الدمج والتنظيف وتوحيد المعايير (Silver Layer)
nhanes_df = (
    demo.merge(ghb, on='SEQN', how='inner')
        .merge(bmx, on='SEQN', how='left')
        .dropna(subset=['LBXGH', 'RIDAGEYR'])
        .copy()
)

nhanes_df.rename(columns={
    'SEQN': 'subject_id',
    'RIAGENDR': 'sex_code',
    'RIDAGEYR': 'age_years',
    'LBXGH': 'hba1c_pct',       # LOINC: 4548-4
    'BMXBMI': 'bmi_kg_m2'       # LOINC: 39156-5
}, inplace=True)

nhanes_df['sex'] = nhanes_df['sex_code'].map({1.0: 'Male', 2.0: 'Female'})
nhanes_df['glycemic_status'] = pd.cut(
    nhanes_df['hba1c_pct'],
    bins=[0, 5.69, 6.49, 100],
    labels=['Normal', 'Prediabetes', 'Diabetes']
).astype(str)

# ربط الأنطولوجيا المعيارية للربط مع بقية المصادر
nhanes_df['icd10_code'] = 'E11'
nhanes_df['loinc_primary'] = '4548-4'
nhanes_df['source_system'] = 'CDC_NHANES_2021_2023'

nhanes_silver_path = SILVER_DIR / 'nhanes_clinical.parquet'
nhanes_df.to_parquet(nhanes_silver_path, index=False)
print(f"✅ تم حفظ طبقة الفضة لـ NHANES: {len(nhanes_df):,} سجل سريري.")
nhanes_df.head(5)
def fetch_who_indicator(indicator_code: str, icd10: str, metric_name: str, min_year: int = 2015) -> pd.DataFrame:
    """يسحب مؤشر وبائي من WHO Global Health Observatory API ويحفظه في Bronze."""
    url = f"https://ghoapi.azureedge.net/api/{indicator_code}"
    params = {"$filter": f"TimeDim ge {min_year}"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    payload = resp.json()

    (BRONZE_DIR / f"who_{indicator_code}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8'
    )

    records = payload.get('value', [])
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame()

    # تصفية النتائج لأخذ إجمالي الجنسين (Both Sexes) عند توفر التقسيم لمنع التكرار
    if 'Dim1' in df.columns and df['Dim1'].notna().any():
        if 'SEX_BTSX' in df['Dim1'].values:
            df = df[df['Dim1'] == 'SEX_BTSX']

    df = df[df['SpatialDimType'] == 'COUNTRY'].copy()
    clean = pd.DataFrame({
        'country_iso3': df['SpatialDim'],
        'year': pd.to_numeric(df['TimeDim'], errors='coerce'),
        'metric_name': metric_name,
        'indicator_code': indicator_code,
        'value': pd.to_numeric(df['NumericValue'], errors='coerce'),
        'low_ci': pd.to_numeric(df.get('Low'), errors='coerce'),
        'high_ci': pd.to_numeric(df.get('High'), errors='coerce'),
        'icd10_code': icd10,
        'source_system': 'WHO_GHO_API'
    }).dropna(subset=['value', 'year'])

    print(f"  ✓ تم سحب {metric_name} ({len(clean):,} سجل دولة/سنة)")
    return clean

print("🔄 جاري سحب المؤشرات الوبائية من منظمة الصحة العالمية (WHO)...")
who_tb = fetch_who_indicator(
    indicator_code="MDG_0000000020",
    icd10="A15",
    metric_name="TB_Incidence_per_100k",
    min_year=2015
)
who_dm = fetch_who_indicator(
    indicator_code="NCD_GLUC_04",
    icd10="E11",
    metric_name="Raised_Fasting_Blood_Glucose_Pct",
    min_year=2010
)

who_df = pd.concat([who_tb, who_dm], ignore_index=True)
who_silver_path = SILVER_DIR / 'who_epidemiology.parquet'
who_df.to_parquet(who_silver_path, index=False)
print(f"✅ تم حفظ طبقة الفضة لـ WHO: {len(who_df):,} سجل وبائي.")
who_df[who_df['country_iso3'].isin(['SAU', 'USA', 'GBR'])].sort_values(['icd10_code', 'year'], ascending=False).head(8)
def fetch_clinvar_gene_variants(gene_symbol: str, icd10: str, disease_domain: str, max_records: int = 25) -> pd.DataFrame:
    """يستعلم عن المتغيرات الجينية المرضية (Pathogenic) في ClinVar عبر NCBI E-utilities."""
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # 1. البحث عن معرفات المتغيرات (esearch)
    search_params = {
        "db": "clinvar",
        "term": f'{gene_symbol}[gene] AND "clinsig pathogenic"[Properties]',
        "retmax": max_records,
        "retmode": "json"
    }
    s_resp = requests.get(f"{base_url}/esearch.fcgi", params=search_params, headers=HEADERS, timeout=30)
    s_resp.raise_for_status()
    ids = s_resp.json().get("esearchresult", {}).get("idlist", [])

    if not ids:
        print(f"  ⚠️ لم يتم العثور على طفرات للجين {gene_symbol}")
        return pd.DataFrame()

    time.sleep(0.4)  # احترام حد الطلبات المجاني لـ NCBI (3 طلبات/ثانية)

    # 2. جلب التفاصيل الجينية (esummary)
    summary_params = {
        "db": "clinvar",
        "id": ",".join(ids),
        "retmode": "json"
    }
    sum_resp = requests.get(f"{base_url}/esummary.fcgi", params=summary_params, headers=HEADERS, timeout=30)
    sum_resp.raise_for_status()
    summary_data = sum_resp.json()

    (BRONZE_DIR / f"clinvar_{gene_symbol}.json").write_text(
        json.dumps(summary_data, ensure_ascii=False), encoding='utf-8'
    )

    result_block = summary_data.get("result", {})
    rows = []
    for uid in ids:
        item = result_block.get(uid, {})
        if not isinstance(item, dict):
            continue

        # التعامل مع صيغة ClinVar الحديثة والقديمة للتصنيف السريري
        germ_cls = item.get("germline_classification") or item.get("clinical_significance") or {}
        clinsig = germ_cls.get("description", "Pathogenic")
        review_status = germ_cls.get("review_status", "Unknown")

        # استخراج أسماء الحالات المرضية المرتبطة
        traits = germ_cls.get("trait_set", []) or item.get("trait_set", [])
        trait_names = "; ".join([t.get("trait_name", "") for t in traits if t.get("trait_name")])

        rows.append({
            "clinvar_uid": str(uid),
            "accession": item.get("accession", ""),
            "gene_symbol": gene_symbol,
            "variant_title": item.get("title", ""),
            "variant_type": item.get("obj_type", ""),
            "clinical_significance": clinsig,
            "review_status": review_status,
            "associated_conditions": trait_names[:250],
            "disease_domain": disease_domain,
            "icd10_code": icd10,
            "source_system": "NCBI_ClinVar_API"
        })

    print(f"  ✓ الجين {gene_symbol} ({disease_domain}): تم سحب {len(rows)} طفرة مرضية")
    return pd.DataFrame(rows)

print("🔄 جاري سحب المتغيرات الجينية من NCBI ClinVar...")
target_genes = [
    ("HNF1A",   "E11", "Monogenic/Early Diabetes"),
    ("GCK",     "E11", "Monogenic/Early Diabetes"),
    ("KCNJ11",  "E11", "Neonatal Diabetes"),
    ("IFNGR1",  "A15", "TB & Mycobacterial Susceptibility"),
    ("IL12RB1", "A15", "TB & Mycobacterial Susceptibility"),
    ("STAT1",   "A15", "TB & Mycobacterial Susceptibility"),
]

clinvar_frames = [fetch_clinvar_gene_variants(g, code, dom) for g, code, dom in target_genes]
clinvar_df = pd.concat(clinvar_frames, ignore_index=True)

clinvar_silver_path = SILVER_DIR / 'clinvar_genomics.parquet'
clinvar_df.to_parquet(clinvar_silver_path, index=False)
print(f"✅ تم حفظ طبقة الفضة لـ ClinVar: {len(clinvar_df):,} متغير جيني.")
clinvar_df[['gene_symbol', 'accession', 'clinical_significance', 'icd10_code', 'associated_conditions']].head(6)
db_path = GOLD_DIR / 'public_health_bank.duckdb'
con = duckdb.connect(str(db_path))

# 1. تسجيل ملفات Parquet في قاعدة البيانات
con.execute(f"""
    CREATE OR REPLACE TABLE nhanes_clinical AS
    SELECT * FROM read_parquet('{SILVER_DIR}/nhanes_clinical.parquet');

    CREATE OR REPLACE TABLE who_epidemiology AS
    SELECT * FROM read_parquet('{SILVER_DIR}/who_epidemiology.parquet');

    CREATE OR REPLACE TABLE clinvar_genomics AS
    SELECT * FROM read_parquet('{SILVER_DIR}/clinvar_genomics.parquet');
""")

# 2. إنشاء جدول جسر الأنطولوجيا الطبية (Ontology Bridge)
con.execute("""
    CREATE OR REPLACE TABLE ontology_diseases (
        icd10_code VARCHAR PRIMARY KEY,
        disease_name_en VARCHAR,
        disease_name_ar VARCHAR,
        preventive_window VARCHAR
    );

    INSERT INTO ontology_diseases VALUES
        ('E11', 'Type 2 & Monogenic Diabetes Spectrum', 'طيف السكري والسكري المبكر', 'Prenatal to Adolescence'),
        ('A15', 'Tuberculosis & Mycobacterial Susceptibility', 'الدرن والقابلية المناعية للعدوى', 'Newborn / Pre-BCG Screening');
""")

# 3. بناء عرض تحليلي موحد (Unified Multi-Modal Public Health View)
con.execute("""
    CREATE OR REPLACE VIEW gold_unified_disease_intelligence AS
    WITH clinvar_stats AS (
        SELECT
            icd10_code,
            COUNT(DISTINCT gene_symbol) AS tracked_genes,
            COUNT(*) AS pathogenic_variants_indexed
        FROM clinvar_genomics
        GROUP BY icd10_code
    ),
    who_latest_sa AS (
        SELECT
            icd10_code,
            metric_name,
            ARG_MAX(value, year) AS latest_sau_value,
            MAX(year) AS latest_year
        FROM who_epidemiology
        WHERE country_iso3 = 'SAU'
        GROUP BY icd10_code, metric_name
    ),
    nhanes_stats AS (
        SELECT
            icd10_code,
            COUNT(*) AS clinical_cohort_n,
            ROUND(AVG(hba1c_pct), 2) AS mean_hba1c,
            ROUND(100.0 * SUM(CASE WHEN glycemic_status = 'Diabetes' THEN 1 ELSE 0 END) / COUNT(*), 2) AS cohort_diabetes_pct
        FROM nhanes_clinical
        GROUP BY icd10_code
    )
    SELECT
        o.icd10_code,
        o.disease_name_ar,
        o.preventive_window,
        COALESCE(c.tracked_genes, 0) AS tracked_genes,
        COALESCE(c.pathogenic_variants_indexed, 0) AS pathogenic_variants,
        w.metric_name AS who_metric,
        ROUND(w.latest_sau_value, 2) AS sau_latest_benchmark,
        w.latest_year AS who_year,
        n.clinical_cohort_n,
        n.mean_hba1c,
        n.cohort_diabetes_pct
    FROM ontology_diseases o
    LEFT JOIN clinvar_stats c ON o.icd10_code = c.icd10_code
    LEFT JOIN who_latest_sa w ON o.icd10_code = w.icd10_code
    LEFT JOIN nhanes_stats  n ON o.icd10_code = n.icd10_code;
""")

# تشغيل الاستعلام الموحّد
unified_report = con.execute("SELECT * FROM gold_unified_disease_intelligence").df()
print("🏆 تقرير نواة بنك البيانات الصحية الموحد (Gold Layer):")
print(unified_report.to_string())

# تصدير جدول جاهز للذكاء الاصطناعي (Feature Store)
con.execute(f"""
    COPY (SELECT * FROM gold_unified_disease_intelligence)
    TO '{GOLD_DIR}/unified_intelligence_summary.parquet' (FORMAT PARQUET);
""")
con.close()
print(f"\n✅ قاعدة البيانات الموحدة جاهزة للاستخدام والترحيل في: {db_path}")
