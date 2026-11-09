import sqlite3
import pandas as pd

def main():
    # Open connection to the SQLite database
    conn = sqlite3.connect('clinic_data.sqlite')

    print("=== DATA INVESTIGATION ===")
    
    # Check the list of clinics (Are there any duplicate/old clinics?)
    print("\n1. Clinic List:")
    clinics_df = pd.read_sql("SELECT * FROM clinics", conn)
    print(clinics_df.to_string(index=False))

    # Check registration statuses (Are there any cancelled appointments?)
    print("\n2. Registration Status Distribution:")
    status_df = pd.read_sql("SELECT status, COUNT(*) as count FROM registrations GROUP BY status", conn)
    print(status_df.to_string(index=False))

    print("\n========================\n")

    print("=== DINKES MONTHLY REPORT (JAN - JUN 2026) ===")
    
    # MAIN QUERY FOR THE REPORT
    # Assumptions applied in the query:
    # 1. PKM-002-OLD is merged (considered the same) with PKM-002.
    # 2. 'cancelled', 'no_show', or 'batal' statuses are not counted as valid visits.
    # 3. Filter the timeframe strictly from January 1, 2026, to June 30, 2026.
    
    query = """
    WITH CleanedRegistrations AS (
        SELECT 
            -- Map PKM-002-OLD so it falls under PKM-002
            CASE 
                WHEN r.clinic_id = 'PKM-002-OLD' THEN 'PKM-002'
                ELSE r.clinic_id 
            END AS final_clinic_id,
            r.patient_id,
            -- Extract Year and Month only (e.g., 2026-01)
            strftime('%Y-%m', r.registered_at) AS visit_month
        FROM registrations r
        WHERE 
            r.registered_at >= '2026-01-01' 
            AND r.registered_at < '2026-07-01'
            -- Ignore patients who cancelled or did not show up
            AND r.status NOT IN ('cancelled', 'no_show', 'batal') 
    )
    SELECT 
        c.name AS Puskesmas,
        d.visit_month AS Bulan,
        COUNT(d.patient_id) AS Total_Visits,
        COUNT(DISTINCT d.patient_id) AS Unique_Patients
    FROM CleanedRegistrations d
    JOIN clinics c ON d.final_clinic_id = c.id
    GROUP BY 
        c.name, 
        d.visit_month
    ORDER BY 
        c.name ASC, 
        d.visit_month ASC;
    """
    
    report_df = pd.read_sql(query, conn)
    print(report_df.to_string(index=False))
    
    # (Optional) Export the result to CSV for easy copy-pasting into Markdown
    report_df.to_csv('dinkes_report.csv', index=False)

    conn.close()

if __name__ == "__main__":
    main()