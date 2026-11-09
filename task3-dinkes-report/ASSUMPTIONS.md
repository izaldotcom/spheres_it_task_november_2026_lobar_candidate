# Assumptions & Data Anomalies Report

## Data Anomalies Discovered

During the extraction of the Dinkes Monthly Report from `clinic_data.sqlite`, several critical data anomalies and inconsistencies were identified:

1.  **Duplicate/Migrated Facility IDs:** The `clinics` table contains an entry for `PKM-002-OLD` (Puskesmas Gunungsari) alongside the active `PKM-002` (Puskesmas Gunungsari Timur). Registration records are split between these two IDs.
2.  **Invalid Visit Statuses:** The `registrations` table includes statuses such as `cancelled`, `batal`, and `no_show`. If we simply count the total rows in the `visits` or `registrations` tables, we would falsely inflate the reporting numbers with patients who never actually received care.
3.  **Ambiguous "Visit" Definition:** A patient might be registered (`registrations` table) but not have a corresponding record in the `visits` table (e.g., they registered but left before seeing the doctor), or they might have multiple visits in a single day across different polyclinics.

## Applied Assumptions & Decisions

To ensure the monthly dashboard displays accurate and actionable metrics, the following assumptions were made when constructing the SQL query/data extraction:

1.  **Merged Clinic IDs:** I assumed that `PKM-002-OLD` is a legacy identifier for `PKM-002`. All registrations linked to `PKM-002-OLD` were dynamically mapped and merged into `PKM-002` to prevent fragmented reporting for the same facility.
2.  **Filtered Invalid Statuses:** I assumed that Dinkes only wants to see _completed_ or _active_ visits. Any registration with a status of `cancelled`, `no_show`, or `batal` was strictly excluded from the calculation.
3.  **Source of Truth for "Visits":** I used the `registrations` table as the baseline for counting visits (rather than the `visits` table), under the assumption that the dashboard measures facility-level foot traffic based on the initial administrative registration, ensuring that cross-referral within the clinic isn't double-counted.
4.  **Timeframe Constraint:** The report strictly filters data from January 1, 2026, to June 30, 2026, as requested.

## Result Table (January - June 2026)

| Puskesmas                  | Bulan   | Total_Visits | Unique_Patients |
| :------------------------- | :------ | -----------: | --------------: |
| Puskesmas Gunungsari Timur | 2026-01 |          751 |             489 |
| Puskesmas Gunungsari Timur | 2026-02 |          804 |             480 |
| Puskesmas Gunungsari Timur | 2026-03 |          791 |             486 |
| Puskesmas Gunungsari Timur | 2026-04 |          766 |             493 |
| Puskesmas Gunungsari Timur | 2026-05 |          669 |             451 |
| Puskesmas Gunungsari Timur | 2026-06 |          778 |             493 |
| Puskesmas Kalimanah Wetan  | 2026-01 |          824 |             518 |
| Puskesmas Kalimanah Wetan  | 2026-02 |          656 |             429 |
| Puskesmas Kalimanah Wetan  | 2026-03 |          743 |             495 |
| Puskesmas Kalimanah Wetan  | 2026-04 |          738 |             475 |
| Puskesmas Kalimanah Wetan  | 2026-05 |          792 |             492 |
| Puskesmas Kalimanah Wetan  | 2026-06 |          724 |             474 |
| Puskesmas Sandik Raya      | 2026-01 |         1467 |             954 |
| Puskesmas Sandik Raya      | 2026-02 |         1469 |             948 |
| Puskesmas Sandik Raya      | 2026-03 |         1409 |             922 |
| Puskesmas Sandik Raya      | 2026-04 |         1456 |             954 |
| Puskesmas Sandik Raya      | 2026-05 |         1549 |             997 |
| Puskesmas Sandik Raya      | 2026-06 |         1658 |            1015 |

## Clarifying Question for Dinkes (Stakeholders)

Before publishing this data to the live dashboard, I would need to ask Dinkes the following clarifying question:

> **"When calculating 'Unique Patients' per month, how should we handle duplicate patient profiles? Currently, the query counts unique `patient_id` UUIDs. However, if a single person has two different UUIDs (e.g., one profile with a valid NIK and another emergency profile with an empty NIK `0000000000000000`), should the system attempt to deduplicate them based on matching names and birthdates, or should we strictly follow the database UUIDs?"**

_Reasoning: This question changes the outcome. If Dinkes requires strict NIK-based deduplication, the Unique Patients metric will decrease significantly compared to just counting UUIDs._
