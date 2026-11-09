import pytest
import httpx
from app import get_db
import app as app_module

# ---------------------------------------------------------
# Test Fix 1: Phantom Claims (Silent Failure)
# ---------------------------------------------------------
def test_sync_pending_claims_failure_handling(monkeypatch):
    # Mock submit_claim_to_api to always return None (simulating API failure/timeout)
    def mock_submit(*args, **kwargs):
        return None
    monkeypatch.setattr("app.submit_claim_to_api", mock_submit)
    
    # Insert dummy claim data with pending status (synced = 0)
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO patients (id, nik, name, birth_date, gender, created_at) VALUES ('p_test', '123', 'Test Patient', '1990', 'M', '2023')")
        conn.execute("INSERT OR IGNORE INTO bills (id, patient_id, amount, created_at) VALUES ('b_test', 'p_test', 100, '2023')")
        conn.execute("INSERT OR REPLACE INTO claims (id, bill_id, patient_id, claim_amount, status, synced, created_at) VALUES ('c_test', 'b_test', 'p_test', 100, 'pending', 0, '2023')")
        conn.commit()
        
    # Run the synchronization background job
    from app import sync_pending_claims
    sync_pending_claims()
    
    # Ensure the claim REMAINS pending and is not marked as synced because the API failed
    with get_db() as conn:
        row = conn.execute("SELECT synced, status FROM claims WHERE id = 'c_test'").fetchone()
        assert row["synced"] == 0
        assert row["status"] == "pending"


# ---------------------------------------------------------
# Test Fix 2: Token Expiry 
# ---------------------------------------------------------
def test_token_expiry_retry(monkeypatch):
    # Set an old token assumed to be expired
    app_module._token = "expired_token_123"
    
    call_counts = {"401": 0, "success": 0}
    
    class MockResponse:
        def __init__(self, status_code, is_token_endpoint=False):
            self.status_code = status_code
            self.is_token_endpoint = is_token_endpoint
            
        def raise_for_status(self):
            if self.status_code != 200:
                raise httpx.HTTPStatusError("Error", request=None, response=self)
                
        def json(self):
            if self.is_token_endpoint:
                return {"access_token": "new_fresh_token_456"}
            return {"status": "accepted"}

    def mock_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/token"):
            return MockResponse(200, is_token_endpoint=True) # Mock fetching a new token successfully
        
        # If the header uses the old token, reject with 401
        if "expired_token_123" in headers.get("Authorization", ""):
            call_counts["401"] += 1
            return MockResponse(401)
        else:
            # If it uses the new token, succeed
            call_counts["success"] += 1
            return MockResponse(200)
            
    monkeypatch.setattr("httpx.post", mock_post)
    
    result = app_module.submit_claim_to_api("c_test", 100.0, "p_test")
    
    assert result is not None
    assert call_counts["401"] == 1 # Must be rejected with 401 exactly once
    assert call_counts["success"] == 1 # Must succeed after the token is refreshed
    assert app_module._token == "new_fresh_token_456" # The global token must be updated to the new one


# ---------------------------------------------------------
# Test Fix 3: Race Condition on Medicine Stock
# ---------------------------------------------------------
def test_dispense_medicine_insufficient_stock():
    from app import dispense_medicine
    
    class DummyRecord:
        medicine_id = 'MED-001'
        quantity = 999999 # Extremely large prescription quantity, far exceeding available stock
        visit_id = 'v1'
        patient_nik = '123'
        
    with get_db() as conn:
        # Atomic stock check should fail and raise a ValueError
        with pytest.raises(ValueError, match="Insufficient stock or medicine not found"):
            dispense_medicine(conn, DummyRecord(), 'p_test')