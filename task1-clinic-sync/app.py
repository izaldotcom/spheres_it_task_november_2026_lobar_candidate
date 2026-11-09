import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks

from config import CLAIM_API_URL
from database import init_db, get_db
from models import BatchPayload, BatchResponse, HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clinic_sync")

_token: str | None = None


# ── Token management ────────────────────────────────────────────────────

def get_token() -> str:
    global _token
    if _token is None:
        resp = httpx.post(f"{CLAIM_API_URL}/token", json={"client_id": "clinic-sync"})
        resp.raise_for_status()
        data = resp.json()
        _token = data["access_token"]
        logger.info("Obtained claim API token")
    return _token


# ── Patient matching ────────────────────────────────────────────────────

def get_or_create_patient(conn, patient) -> str:
    row = conn.execute(
        "SELECT id FROM patients WHERE nik = ?",
        (patient.nik,),
    ).fetchone()
    if row:
        return row["id"]

    patient_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO patients (id, nik, name, birth_date, gender, address, clinic_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_id, patient.nik, patient.name, patient.birth_date,
         patient.gender, patient.address, patient.clinic_id, now),
    )
    logger.info(
        "Created patient %s (NIK: %s, name: %s, DOB: %s) at clinic %s",
        patient_id, patient.nik, patient.name, patient.birth_date, patient.clinic_id,
    )
    return patient_id


# ── Visit recording ─────────────────────────────────────────────────────

def record_visit(conn, visit, patient_id: str) -> str:
    visit_id = str(uuid.uuid4())
    reg_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    conn.execute(
        "INSERT INTO registrations (id, registration_number, patient_id, clinic_id, registered_at, status, polyclinic) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (reg_id, f"REG-{uuid.uuid4().hex[:8].upper()}", patient_id,
         visit.clinic_id, now, visit.polyclinic),
    )
    conn.execute(
        "INSERT INTO visits (id, registration_id, patient_id, clinic_id, polyclinic, visit_date, diagnosis, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (visit_id, reg_id, patient_id, visit.clinic_id,
         visit.polyclinic, visit.visit_date, visit.diagnosis, visit.notes),
    )
    logger.info(
        "Recorded visit %s for patient %s (%s), diagnosis: %s",
        visit_id, patient_id, visit.patient_nik, visit.diagnosis,
    )
    return visit_id


# ── Dispensing ───────────────────────────────────────────────────────────

def dispense_medicine(conn, record, patient_id: str):
    # Fix 3: Race Condition. Use atomic update directly in the database.
    # It will only succeed (rowcount > 0) if the ID exists AND the stock is sufficient.
    cursor = conn.execute(
        "UPDATE medicines SET stock = stock - ? WHERE id = ? AND stock >= ?",
        (record.quantity, record.medicine_id, record.quantity),
    )
    
    if cursor.rowcount == 0:
        raise ValueError(f"Insufficient stock or medicine not found for {record.medicine_id}")

    disp_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO dispensing (id, visit_id, patient_id, medicine_id, quantity, dispensed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (disp_id, record.visit_id, patient_id, record.medicine_id, record.quantity, now),
    )
    logger.info(
        "Dispensed %d x %s to patient %s (NIK: %s)",
        record.quantity, record.medicine_id, patient_id, record.patient_nik
    )


# ── Billing / claims ────────────────────────────────────────────────────

def create_bill_and_claim(conn, bill, patient_id: str):
    bill_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    conn.execute(
        "INSERT INTO bills (id, visit_id, patient_id, amount, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (bill_id, bill.visit_id, patient_id, bill.amount, bill.description, now),
    )
    conn.execute(
        "INSERT INTO claims (id, bill_id, patient_id, claim_amount, status, synced, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', 0, ?)",
        (claim_id, bill_id, patient_id, bill.amount, now),
    )


# ── Claim submission ────────────────────────────────────────────────────

def submit_claim_to_api(claim_id: str, claim_amount: float, patient_id: str) -> dict | None:
    global _token # Fix 2: Declare global so we can reset the token if it expires
    
    for attempt in range(3):
        token = get_token()
        try:
            resp = httpx.post(
                f"{CLAIM_API_URL}/claims",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "claim_id": claim_id,
                    "amount": claim_amount,
                    "patient_id": patient_id,
                },
                timeout=10.0,
            )
            
            # Fix 2: Handle Token Expiry (401 Unauthorized)
            if resp.status_code == 401:
                logger.warning("Token expired (401), clearing token and retrying...")
                _token = None # Clear the token so get_token() fetches a new one in the next iteration
                continue
                
            resp.raise_for_status()
            return resp.json()

        except Exception as e:
            logger.warning("Claim submission issue for %s (attempt %d): %s", claim_id, attempt + 1, e)
            time.sleep(1)

    return None


# ── Sync pending claims ─────────────────────────────────────────────────

def sync_pending_claims():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, claim_amount, patient_id FROM claims WHERE synced = 0"
        ).fetchall()

        if not rows:
            return

        logger.info("Syncing %d pending claims", len(rows))

        for row in rows:
            try:
                result = submit_claim_to_api(row["id"], row["claim_amount"], row["patient_id"])
                
                # Fix 1: Phantom Claims. ONLY update status if the API returns a result (not failed/timeout)
                if result:
                    logger.info("Claim %s submitted, result: %s", row["id"], result)
                    conn.execute(
                        "UPDATE claims SET synced = 1, status = 'synced', submitted_at = ? WHERE id = ?",
                        (datetime.now().isoformat(), row["id"]),
                    )
                else:
                    logger.warning("Claim %s failed to submit, skipping update", row["id"])
                    
            except Exception as e:
                logger.warning("Error syncing claim %s: %s", row["id"], e)

        conn.commit()


# ── FastAPI app ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Clinic Sync Service", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/batch", response_model=BatchResponse)
def receive_batch(payload: BatchPayload):
    logger.info(
        "Received batch %s from clinic %s with %d patients, %d visits, %d dispensing, %d bills",
        payload.batch_id, payload.clinic_id,
        len(payload.patients), len(payload.visits),
        len(payload.dispensing), len(payload.bills),
    )

    patients_created = 0
    patients_existing = 0
    visits_recorded = 0
    dispensing_recorded = 0
    bills_created = 0
    claims_created = 0
    errors = []

    with get_db() as conn:
        clinic = conn.execute(
            "SELECT id FROM clinics WHERE id = ?", (payload.clinic_id,)
        ).fetchone()
        if not clinic:
            raise HTTPException(status_code=400, detail=f"Unknown clinic: {payload.clinic_id}")

        patient_map: dict[str, str] = {}

        for p in payload.patients:
            try:
                existing = conn.execute(
                    "SELECT id FROM patients WHERE nik = ?", (p.nik,)
                ).fetchone()

                if existing:
                    patient_map[p.nik] = existing["id"]
                    patients_existing += 1
                else:
                    pid = get_or_create_patient(conn, p)
                    patient_map[p.nik] = pid
                    patients_created += 1
            except Exception as e:
                errors.append(f"Patient error: {e}")

        for v in payload.visits:
            try:
                pid = patient_map.get(v.patient_nik)
                if not pid:
                    errors.append(f"Visit skipped: unknown patient NIK {v.patient_nik}")
                    continue
                record_visit(conn, v, pid)
                visits_recorded += 1
            except Exception as e:
                errors.append(f"Visit recording error: {e}")

        for d in payload.dispensing:
            try:
                pid = patient_map.get(d.patient_nik)
                if not pid:
                    errors.append(f"Dispensing skipped: unknown patient")
                    continue
                dispense_medicine(conn, d, pid)
                dispensing_recorded += 1
            except ValueError as e:
                errors.append(str(e))

        for b in payload.bills:
            try:
                pid = patient_map.get(b.patient_nik)
                if not pid:
                    errors.append(f"Bill skipped: unknown patient")
                    continue
                create_bill_and_claim(conn, b, pid)
                bills_created += 1
                claims_created += 1
            except Exception as e:
                errors.append(f"Bill creation error: {e}")

        conn.commit()

    return BatchResponse(
        batch_id=payload.batch_id,
        patients_created=patients_created,
        patients_existing=patients_existing,
        visits_recorded=visits_recorded,
        dispensing_recorded=dispensing_recorded,
        bills_created=bills_created,
        claims_created=claims_created,
        errors=errors,
    )


@app.post("/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_pending_claims)
    return {"status": "sync_triggered"}


@app.get("/claims")
def list_claims(status: str | None = None):
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM claims WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM claims").fetchall()
    return [dict(r) for r in rows]


@app.get("/patients")
def list_patients(clinic_id: str | None = None):
    with get_db() as conn:
        if clinic_id:
            rows = conn.execute(
                "SELECT * FROM patients WHERE clinic_id = ?", (clinic_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM patients").fetchall()
    return [dict(r) for r in rows]


@app.get("/stock")
def list_stock():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM medicines").fetchall()
    return [dict(r) for r in rows]
