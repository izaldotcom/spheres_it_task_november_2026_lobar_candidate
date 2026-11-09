# DESIGN.md: Offline-First Sync Architecture

This document outlines the architecture and design decisions for the offline-first synchronization system used by healthcare workers in low-connectivity environments.

## 1. Exactly-once delivery

**Strategy:** Client-side generated UUIDs for idempotency.
Every time a record (patient registration, visit, or dispensing) is created offline, the device generates a unique `client_uuid` (UUIDv4). During synchronization, the server checks if this `client_uuid` already exists in the database.

- **If it does not exist:** The server saves the record and returns an `accepted` status.

- **If it already exists** (e.g., the device lost connection during the first response and retried sending the payload): The server ignores the insert operation but still returns an HTTP 200 with a `duplicate` status.
  This ensures that data is never duplicated on the server, even if the same batch is transmitted multiple times.

## 2. Duplicate patients

**Strategy:** Deterministic matching combined with Human-in-the-Loop resolution.

- **Automatic Action:** The server first checks the NIK. If a valid NIK perfectly matches an existing record, the server automatically merges the incoming data into the existing patient profile.

- **Flagging for Review:** If the NIK is missing, the server performs fuzzy string matching on the name (e.g., "Siti Aminah" vs "St. Aminah") combined with `birth_date` and `gender`. If a high probability match is found, the server **accepts** the new record so that medical care is not interrupted, but flags it as `flagged_possible_duplicate_patient`.

- **Human Resolution:** Flagged records are sent to the Review Queue. A central administrator can manually review the queue and decide whether to merge the profiles or keep them separate.

## 3. Stock conflicts

**Strategy:** Prioritize patient care over strict inventory limits (Eventual Consistency).
In a medical setting, you cannot refuse a patient medicine because of a delayed database synchronization.

- **Mechanism:** The server will **always accept** dispensing records from any device. If two devices dispense the last remaining units simultaneously, the server will allow the stock count to drop into negative numbers (e.g., -5).

- **Reconciliation:** The system will then automatically trigger a `negative_stock` alert and add it to the Review Queue, prompting a pharmacist to perform a physical stock reconciliation.

## 4. Wrong device clock

**Strategy:** Decouple device time from chronological truth.
Device clocks on low-end Android phones cannot be trusted, as they may reset to 2019 if the battery dies.

- **Mechanism:** The server will rely on its own timestamp (`server_received_at`) as the definitive chronological truth for the database.

- **Transaction Order:** The `device_recorded_at` timestamp is saved purely as historical context. To determine the sequence of events (e.g., Registration -> Visit -> Dispensing) that happened while offline, the server will process the records based on their sequential array index order within the JSON payload, rather than relying on the device's clock.

## 5. Lost device

**Strategy:** Local encryption and enforced sync timeouts.

- **What leaks:** Unsynced local databases contain Protected Health Information (PHI) such as patient names, NIKs, and diagnoses. To mitigate data leaks, the local mobile database (e.g., SQLite/Realm) must be encrypted at rest (e.g., using SQLCipher) tied to the device's biometric or PIN authentication.

- **What can be recovered:** Unsynced data is permanently lost from the digital system since it never reached the server. Recovery would rely entirely on physical paper registers if the clinic uses them as backups. To minimize the volume of lost data, the app must enforce a "Maximum Offline Window" (e.g., blocking new entries if the device hasn't synced in 3 days), forcing health workers to find a connection.

## 6. Schema migration

**Strategy:** Backward Compatibility via Backend Adapters.
Every payload explicitly includes a `schema_version` (e.g., version 1). Since the server cannot force all offline devices to update their apps simultaneously, the API must support backward compatibility.

- **Handling:** When the backend database is upgraded to version 2, the Golang sync endpoint will implement an adapter that dynamically maps version 1 payloads to version 2 format (filling new fields with sensible defaults or nulls).

- **Future Rejection:** If the server receives a completely unknown schema version (e.g., version 99), it will reject those specific records with an `"unsupported schema_version"` reason to protect database integrity.

## 7. MVP scope

**Strategy:** Simple but Functional.
For a 2-month Minimum Viable Product (MVP), the following features will **NOT** be built:

- **Complex Machine Learning for Deduplication:** We will avoid deploying AI models for patient matching. We will rely on simple SQL string matching (e.g., `ILIKE` or basic Levenshtein distance) and delegate the rest to humans (via the Review Queue).

- **Real-time Bidirectional Sync (WebSockets):** The server will not actively push data to offline devices. Devices will use a simple REST pull mechanism (GET requests) whenever they detect an internet connection.

- **Granular Role-Based Access Control (RBAC):** We will not implement complex permissions for doctors, nurses, and cashiers. The MVP will only have two roles: "Field Worker" (Mobile App) and "Central Admin" (Web Dashboard).
