# Open Public Health Data Bank

A small pipeline that downloads three open public health sources and combines them into one local analytical database (DuckDB + Parquet), linked by ICD-10 codes.

| Source | What is pulled | Link key |
|---|---|---|
| CDC NHANES 2021-2023 (DEMO_L, GHB_L, BMX_L) | Individual-level demographics, HbA1c, BMI | ICD-10 E11 (diabetes) |
| WHO Global Health Observatory (OData API) | TB incidence per 100k and raised fasting blood glucose, by country and year | ICD-10 A15, E11 |
| NCBI ClinVar (E-utilities) | Up to 25 pathogenic variants per gene | ICD-10 A15, E11 |

Genes queried: HNF1A, GCK, KCNJ11 (early and monogenic diabetes) and IFNGR1, IL12RB1, STAT1 (susceptibility to mycobacterial infection, including TB).

## Layers

- **bronze**: raw downloads as received (XPT, JSON).
- **silver**: cleaned tables in Parquet (`nhanes_clinical`, `who_epidemiology`, `clinvar_genomics`).
- **gold**: `public_health_bank.duckdb` with an ICD-10 ontology table and a summary view joining the three sources.

## Run

```bash
pip install -r requirements.txt
DATABANK_DIR=./data python src/build_databank.py
```

Works in Google Colab too (set `DATABANK_DIR` to a Drive folder to keep the output).

## Tested state

Run end to end on 2026-11-21 on a clean Python environment: all three sources responded, and the gold database built.

| Table | Rows |
|---|---|
| NHANES clinical (silver) | 6,715 |
| WHO epidemiology (silver) | 3,111 |
| ClinVar variants (silver) | 150 |

Total download is about 8 MB and the run takes under a minute.

## Limits

- This is a data-engineering scaffold, not a validated clinical or predictive tool. Nothing here is for diagnosis or medical decisions.
- ClinVar pulls are capped at 25 variants per gene, so counts are a sample, not complete gene totals.
- The summary view mixes sources with different populations, years and definitions (for example the NHANES diabetes share is derived from an HbA1c threshold, and WHO values are national estimates for different years). Do not read it as one cohort.
- Not tested: scheduled refreshes, other NHANES cycles, larger gene lists.

## Data terms

This repository contains code only. Downloaded data is not included. Each source has its own terms; check them before redistributing any output.

- NHANES (CDC/NCHS): public-use files, for statistical reporting and analysis only. Do not attempt to identify participants. https://www.cdc.gov/nchs/policy/data-user-agreement.html
- WHO (data.who.int terms): datasets are provided under CC BY 4.0 with additional WHO terms unless an indicator says otherwise. Attribution is required, you may not imply WHO endorsement or use the WHO name or emblem, and you may not misrepresent the data. Check the metadata of each indicator before redistributing. https://data.who.int/about/data/terms-and-conditions
- ClinVar (NCBI): not for direct diagnostic use or medical decisions without review by a genetics professional. https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/
