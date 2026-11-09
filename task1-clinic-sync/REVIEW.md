# Code Review & Issue Prioritization

Here is a list of the issues found in the Clinic Sync Service, ranked by severity from the most critical to the least.

### 1. SEVERITY: CRITICAL - Failed Claims Silently Marked as Synced (Phantom Claims)

- **Location:** `app.py` in the `sync_pending_claims` function.
- **Issue:** The function catches exceptions when calling `submit_claim_to_api`, but the code proceeds to execute `UPDATE claims SET synced = 1` regardless of whether the external insurance API returned a success, failure, or timeout.
- **Real-World Impact:** The Finance team and Dinkes (Health Agency) will see reports showing hundreds of claims as "synced" and expect the funds to be disbursed. However, the insurance company never actually received the data. During the end-of-month reconciliation process across 40 clinics, this will cause discrepancies amounting to billions of Rupiah, requiring painstaking manual correction for every single phantom claim.

### 2. SEVERITY: HIGH - Token Expiry is Unhandled (Total Service Outage)

- **Location:** `app.py` in the `get_token` and `submit_claim_to_api` functions.
- **Issue:** The `_token` variable is cached globally and is only re-fetched if its value is `None`. The mock insurance API enforces a token expiry (default 3600 seconds). After 1 hour of runtime, all subsequent insurance claim requests will be rejected with a 401 Unauthorized error.
- **Real-World Impact:** One hour after the system goes live across 40 clinics, the entire insurance claim submission process will silently fail (compounded by Issue #1). The IT team would have to manually restart the service (Docker/server) every single hour just to keep the application functioning.

### 3. SEVERITY: HIGH - Race Condition on Medicine Stock Deduction

- **Location:** `app.py` in the `dispense_medicine` function.
- **Issue:** The application reads the stock (`SELECT stock`), calculates the new stock in Python (`current_stock - record.quantity`), and then saves it (`UPDATE medicines SET stock = ?`). Because there is no database lock (like an atomic transaction), if there are 2 concurrent requests dispensing the same medicine, one of the calculations will be overwritten.
- **Real-World Impact:** The physical stock of medicines in the clinic's pharmacy will desync from the system's stock. The system might show 50 units remaining when the physical stock is actually depleted. This will disrupt patient care (patients prescribed medicine that is out of stock) and ruin pharmacy stock audit reports.

### 4. SEVERITY: MEDIUM - Synchronous API Calls Blocked by Timeouts

- **Location:** `app.py` in the `sync_pending_claims` function.
- **Issue:** This function iterates through `for row in rows` and calls the external API sequentially. The Mock API has a `MOCK_TIMEOUT_RATE` configuration that introduces a 15-second delay randomly.
- **Real-World Impact:** If there are 1000 pending claims from 40 clinics, and 5% of them hit the timeout, this background job will hang for hours. Other patients' claims will be delayed and not submitted on time, significantly slowing down the cash flow of the clinics.

### 5. SEVERITY: MEDIUM - Database Foreign Keys are Disabled

- **Location:** `database.py` in the `get_db` function.
- **Issue:** The connection executes `conn.execute("PRAGMA foreign_keys=OFF")`.
- **Real-World Impact:** If there is a bug in the application or if manual database modifications occur, it is highly possible to create visit or bill records that reference a non-existent `patient_id` or `clinic_id`. This will cause silent data corruption and break reporting dashboards used by the Health Agency.

---

## What I Deliberately Did Not Fix

_(Note: Based on the task requirements, only the top 3 issues were fixed in the code. The following issues were intentionally left unresolved for the MVP.)_

- **Synchronous API Calls (Issue #4):** While timeouts will slow down the background job, the data will eventually be processed. Refactoring this to use asynchronous background queues (like Celery or AsyncIO tasks) would require massive architectural changes that are too risky to implement just one week before the 40-clinic go-live. We are prioritizing data integrity (fixing phantom claims) over speed for now.
- **Foreign Keys Disabled (Issue #5):** I did not enable `PRAGMA foreign_keys=ON` because doing so right before launch might unexpectedly break existing edge-case tests or legacy data inserts that inadvertently rely on this loose constraint. This requires a deeper database migration strategy planned for a future sprint.
