# API Reference

**🔴 Live Interactive Docs (Swagger):** [https://vaultpay-uvf2.onrender.com/docs](https://vaultpay-uvf2.onrender.com/docs)

All endpoints return a `StandardResponse[T]` envelope:

```json
{
  "success": true,
  "message": "Wallet retrieved successfully",
  "data": { ... },
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

On error:
```json
{
  "success": false,
  "message": "Wallet not found",
  "error_code": "WALLET_NOT_FOUND",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

All authenticated endpoints require:
```
Authorization: Bearer <jwt_token>
```

---

## 1. Wallet Management

### `POST /wallets/create`
Create a wallet for the authenticated user.

**Auth:** `user+`

**Request:**
```json
{ "currency": "INR" }
```

**Response `201`:** Wallet object  
**Errors:** `WALLET_ALREADY_EXISTS (409)`

---

### `GET /wallets/me`
Get the authenticated user's wallet.

**Auth:** `user+`

**Response `200`:**
```json
{
  "wallet_id": "VPY-A1B2C3",
  "balance": "1250.00",
  "currency": "INR",
  "status": "active",
  "kyc_verified": true,
  "created_at": "2024-01-15T10:30:00Z"
}
```
**Errors:** `WALLET_NOT_FOUND (404)`

---

### `GET /wallets/{wallet_id}`
Look up any wallet by its public wallet ID (e.g. for P2P recipient lookup).

**Auth:** `user+`

**Response `200`:** Public wallet info (no balance)  
**Errors:** `WALLET_NOT_FOUND (404)`

---

### `GET /wallets/balance`
Get the authenticated user's current balance.

**Auth:** `user+`

**Response `200`:** `{ "balance": "1250.00", "currency": "INR" }`

---

### `POST /wallets/freeze`
Freeze own wallet (self-service).

**Auth:** `user+`

**Response `200`:** Updated wallet object  
**Errors:** `WALLET_ALREADY_FROZEN (409)`, `WALLET_ALREADY_CLOSED (409)`

---

### `POST /wallets/unfreeze`
Unfreeze own wallet (self-service).

**Auth:** `user+`

**Response `200`:** Updated wallet object  
**Errors:** `WALLET_NOT_FROZEN (409)`

---

### `DELETE /wallets/me`
Close the authenticated user's wallet (soft delete, sets status to `closed`).

**Auth:** `user+`

**Constraints:** Balance must be 0 before closing.  
**Response `200`:** Confirmation message  
**Errors:** `WALLET_HAS_BALANCE (409)`, `WALLET_ALREADY_CLOSED (409)`

---

## 2. Transactions

### `POST /transactions/topup`
Top up wallet with external funds.

**Auth:** `user+` + **KYC required**

**Request:**
```json
{
  "amount": "500.00",
  "payment_method": "upi",
  "metadata": { "upi_ref": "UPI123456" }
}
```

**Response `201`:** Transaction object  
**Errors:** `KYC_NOT_VERIFIED (403)`, `WALLET_FROZEN (403)`, `DAILY_LIMIT_EXCEEDED (429)`, `PER_TRANSACTION_LIMIT_EXCEEDED (429)`

---

### `POST /transactions/send`
Send money to another VaultPay wallet.

**Auth:** `user+` + **KYC required** + **PIN required**

**Request:**
```json
{
  "recipient_wallet_id": "VPY-X9Y8Z7",
  "amount": "200.00",
  "description": "Lunch split",
  "pin": "1234"
}
```

**Response `201`:** Transaction objects for both sender (debit) and receiver (credit)  
**Errors:** `INVALID_PIN (401)`, `INSUFFICIENT_BALANCE (402)`, `WALLET_FROZEN (403)`, `RECIPIENT_WALLET_NOT_FOUND (404)`, `DAILY_LIMIT_EXCEEDED (429)`

---

### `POST /transactions/withdraw`
Withdraw funds to an external account.

**Auth:** `user+` + **KYC required** + **PIN required**

**Request:**
```json
{
  "amount": "1000.00",
  "bank_account": "XXXXXXXXXXXX",
  "ifsc": "SBIN0001234",
  "pin": "1234"
}
```

**Response `201`:** Transaction object  
**Errors:** `INSUFFICIENT_BALANCE (402)`, `INVALID_PIN (401)`, `DAILY_LIMIT_EXCEEDED (429)`

---

### `GET /transactions/history`
Paginated list of the authenticated user's transactions.

**Auth:** `user+`

**Query params:** `page`, `per_page`, `type` (credit|debit), `category`, `start_date`, `end_date`

**Response `200`:** `PaginatedResponse[Transaction]`

---

### `GET /transactions/{transaction_id}`
Get a specific transaction by UUID.

**Auth:** `user+` (own transactions only)

**Response `200`:** Transaction object  
**Errors:** `TRANSACTION_NOT_FOUND (404)`, `INSUFFICIENT_PERMISSIONS (403)`

---

### `GET /transactions/reference/{reference_id}`
Look up a transaction by its human-readable reference (e.g. `VP-TXN-A1B2C3`).

**Auth:** `user+`

**Response `200`:** Transaction object

---

## 3. Transaction Limits

### `GET /limits`
Get the authenticated user's transaction limits.

**Auth:** `user+`

**Response `200`:** Array of limit objects per action type

---

### `POST /limits`
Set a custom limit for a specific action.

**Auth:** `user+`

**Request:**
```json
{
  "action": "send_money",
  "daily_limit": "5000.00",
  "per_transaction_limit": "2000.00"
}
```

**Response `201`:** Created limit object  
**Errors:** `LIMIT_ALREADY_EXISTS (409)` — use PATCH to update

---

### `PATCH /limits/{action}`
Update an existing limit.

**Auth:** `user+`

**Request:** (partial update — any of the limit fields)  
**Response `200`:** Updated limit object

---

### `DELETE /limits/{action}`
Remove a custom limit (reverts to system defaults).

**Auth:** `user+`

**Response `200`:** Confirmation message

---

## 4. PIN Management

### `POST /pin/set`
Set PIN for the first time.

**Auth:** `user+`

**Request:** `{ "pin": "1234", "confirm_pin": "1234" }`

**Response `201`:** Success  
**Errors:** `PIN_ALREADY_SET (409)` — use /pin/change

---

### `POST /pin/verify`
Verify PIN (used before sensitive operations).

**Auth:** `user+`

**Request:** `{ "pin": "1234" }`

**Response `200`:** `{ "valid": true }`  
**Errors:** `INVALID_PIN (401)`, `PIN_LOCKED (423)` — after 5 failed attempts

---

### `POST /pin/change`
Change existing PIN.

**Auth:** `user+`

**Request:** `{ "current_pin": "1234", "new_pin": "5678", "confirm_new_pin": "5678" }`

**Response `200`:** Success  
**Errors:** `INVALID_PIN (401)`

---

### `POST /pin/reset`
Reset PIN via OTP verification (no current PIN needed).

**Auth:** `user+`

**Request:** `{ "otp": "123456", "new_pin": "5678", "confirm_new_pin": "5678" }`

**Response `200`:** Success

---

## 5. KYC

### `POST /kyc/submit`
Submit KYC document for verification.

**Auth:** `user+`

**Request:**
```json
{
  "doc_type": "aadhar",
  "doc_number": "1234 5678 9012"
}
```

`doc_number` is encrypted server-side before storage.

**Response `201`:** KYC submission object  
**Errors:** `KYC_ALREADY_SUBMITTED (409)`, `KYC_ALREADY_VERIFIED (409)`

---

### `GET /kyc/status`
Check the authenticated user's KYC verification status.

**Auth:** `user+`

**Response `200`:** `{ "status": "pending|verified|rejected", "rejection_reason": null }`

---

## 6. Disputes

### `POST /disputes`
Raise a dispute against a transaction.

**Auth:** `user+`

**Request:**
```json
{
  "transaction_id": "uuid-here",
  "reason": "Did not authorize this transaction"
}
```

**Response `201`:** Dispute object  
**Errors:** `TRANSACTION_NOT_FOUND (404)`, `DISPUTE_ALREADY_EXISTS (409)`

---

### `GET /disputes`
List the authenticated user's disputes.

**Auth:** `user+`

**Response `200`:** `PaginatedResponse[Dispute]`

---

### `GET /disputes/{dispute_id}`
Get a specific dispute.

**Auth:** `user+` (own disputes only)

**Response `200`:** Dispute object

---

## 7. Notifications

### `GET /notifications`
Get all notifications for the authenticated user.

**Auth:** `user+`

**Query params:** `page`, `per_page`, `unread_only` (bool)

**Response `200`:** `PaginatedResponse[Notification]`

---

### `POST /notifications/{notification_id}/read`
Mark a notification as read.

**Auth:** `user+`

**Response `200`:** Updated notification

---

### `POST /notifications/read-all`
Mark all unread notifications as read.

**Auth:** `user+`

**Response `200`:** Count of notifications marked

---

### `GET /notifications/unread-count`
Fast endpoint returning the unread notification count.

**Auth:** `user+`

**Response `200`:** `{ "count": 3 }`

---

## 8. Admin Endpoints

All admin endpoints require `moderator`, `admin`, or `super_admin` role as specified.

---

### `GET /admin/wallets`
List all wallets with filters.

**Auth:** `moderator+`

**Query params:** `page`, `per_page`, `status`, `kyc_verified`, `user_id`

**Response `200`:** `PaginatedResponse[Wallet]`

---

### `GET /admin/wallets/{wallet_id}`
Get full wallet details including balance history.

**Auth:** `moderator+`

**Response `200`:** Extended wallet object

---

### `POST /admin/wallets/{wallet_id}/freeze`
Freeze any wallet.

**Auth:** `moderator+`

**Request:** `{ "reason": "Suspicious activity detected" }`

**Response `200`:** Updated wallet + audit log entry created

---

### `POST /admin/wallets/{wallet_id}/unfreeze`
Unfreeze any wallet.

**Auth:** `moderator+`

**Response `200`:** Updated wallet + audit log entry created

---

### `GET /admin/transactions`
List all transactions across all wallets.

**Auth:** `moderator+`

**Query params:** `page`, `per_page`, `type`, `category`, `status`, `wallet_id`, `start_date`, `end_date`

**Response `200`:** `PaginatedResponse[Transaction]`

---

### `GET /admin/kyc`
List all KYC submissions with filters.

**Auth:** `moderator+`

**Query params:** `page`, `per_page`, `status`

**Response `200`:** `PaginatedResponse[KYCSubmission]`

---

### `GET /admin/kyc/{submission_id}`
Get a specific KYC submission. **Decrypts** `doc_number` in response.

**Auth:** `moderator+`

**Response `200`:** Full KYC submission with decrypted document number

---

### `POST /admin/kyc/{submission_id}/approve`
Approve a KYC submission. Sets wallet's `kyc_verified = true`.

**Auth:** `moderator+`

**Response `200`:** Updated submission + notification sent to user

---

### `POST /admin/kyc/{submission_id}/reject`
Reject a KYC submission with a reason.

**Auth:** `moderator+`

**Request:** `{ "rejection_reason": "Document expired" }`

**Response `200`:** Updated submission + notification sent to user

---

### `GET /admin/disputes`
List all disputes.

**Auth:** `moderator+`

**Response `200`:** `PaginatedResponse[Dispute]`

---

### `POST /admin/disputes/{dispute_id}/resolve`
Resolve a dispute.

**Auth:** `moderator+`

**Request:** `{ "resolution_notes": "Transaction confirmed as authorized. Dispute rejected." }`

**Response `200`:** Updated dispute

---

### `GET /admin/audit-logs`
View the immutable audit log.

**Auth:** `admin+`

**Query params:** `page`, `per_page`, `actor_id`, `target_type`, `action`, `start_date`, `end_date`

**Response `200`:** `PaginatedResponse[AuditLog]`

---

### `GET /admin/limits`
View all custom transaction limits across all users.

**Auth:** `admin+`

**Response `200`:** `PaginatedResponse[TransactionLimit]`

---

### `POST /admin/limits/{wallet_id}`
Override transaction limits for a specific wallet.

**Auth:** `admin+`

**Response `201`:** Created limit object

---

### `GET /admin/settings`
View all system settings.

**Auth:** `super_admin`

**Response `200`:** Array of system settings

---

### `PATCH /admin/settings/{key}`
Update a system setting.

**Auth:** `super_admin`

**Request:** `{ "value": "10000.00" }`

**Response `200`:** Updated setting

---

### `POST /admin/users/{user_id}/deactivate`
Deactivate a user account by calling AuthShield's admin API.

**Auth:** `admin+`

**Request:** `{ "reason": "Policy violation" }`

**Response `200`:** Confirmation (AuthShield handles the actual deactivation)

---

### `GET /admin/stats`
High-level system statistics.

**Auth:** `super_admin`

**Response `200`:**
```json
{
  "total_wallets": 1500,
  "active_wallets": 1432,
  "total_transaction_volume": "45230000.00",
  "pending_kyc_count": 23,
  "open_disputes": 7
}
```

---

## Error Code Reference

| Code | HTTP Status | Description |
|---|---|---|
| `WALLET_NOT_FOUND` | 404 | No wallet exists for this user/ID |
| `WALLET_ALREADY_EXISTS` | 409 | User already has a wallet |
| `WALLET_FROZEN` | 403 | Wallet is frozen — operation blocked |
| `WALLET_ALREADY_FROZEN` | 409 | Wallet is already frozen |
| `WALLET_NOT_FROZEN` | 409 | Cannot unfreeze a non-frozen wallet |
| `WALLET_ALREADY_CLOSED` | 409 | Wallet has been closed |
| `WALLET_HAS_BALANCE` | 409 | Cannot close wallet with remaining balance |
| `INSUFFICIENT_BALANCE` | 402 | Balance too low for this transaction |
| `KYC_NOT_VERIFIED` | 403 | Operation requires verified KYC |
| `KYC_ALREADY_SUBMITTED` | 409 | KYC submission already exists |
| `KYC_ALREADY_VERIFIED` | 409 | KYC already verified |
| `INVALID_PIN` | 401 | Incorrect PIN |
| `PIN_ALREADY_SET` | 409 | Use /pin/change to update |
| `PIN_NOT_SET` | 400 | PIN must be set before this operation |
| `PIN_LOCKED` | 423 | Too many failed PIN attempts |
| `DAILY_LIMIT_EXCEEDED` | 429 | Transaction exceeds daily limit |
| `PER_TRANSACTION_LIMIT_EXCEEDED` | 429 | Transaction exceeds per-transaction limit |
| `DISPUTE_ALREADY_EXISTS` | 409 | Dispute already filed for this transaction |
| `TOKEN_EXPIRED` | 401 | JWT has expired |
| `TOKEN_INVALID` | 401 | JWT is malformed or tampered |
| `ACCOUNT_DISABLED` | 403 | User account is deactivated |
| `INSUFFICIENT_PERMISSIONS` | 403 | Role not authorized for this operation |
| `AUTHSHIELD_UNAVAILABLE` | 503 | AuthShield service not responding |
