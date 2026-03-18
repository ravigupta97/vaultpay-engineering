# Feature Flows

Detailed sequence diagrams for the 3 critical financial flows in VaultPay.

---

## 1. P2P Send Money

**Pre-conditions:** Both wallets active, sender KYC verified, PIN set

```mermaid
sequenceDiagram
    participant C as Client
    participant VP as VaultPay API
    participant R as Redis
    participant DB as PostgreSQL
    participant AS as AuthShield

    C->>VP: POST /transactions/send<br/>{recipient_wallet_id, amount, pin}
    
    Note over VP: Authenticate
    VP->>AS: GET /internal/validate-token (JWT)
    AS-->>VP: {user_id, role, is_active}
    
    Note over VP: Check PIN lockout
    VP->>R: GET vp:pin:locked:{wallet_id}
    R-->>VP: (null = not locked)
    
    Note over VP: Load & validate sender wallet
    VP->>DB: SELECT wallet WHERE user_id = ?
    DB-->>VP: sender_wallet{status: active, kyc: verified, balance: 1500}
    
    Note over VP: Validate PIN
    VP->>VP: bcrypt.check(pin, pin_hash)
    alt PIN invalid
        VP->>R: INCR vp:pin:attempts:{wallet_id}
        VP-->>C: 401 INVALID_PIN
    end
    
    Note over VP: Load recipient wallet
    VP->>DB: SELECT wallet WHERE wallet_id = VPY-XXXXX
    DB-->>VP: recipient_wallet{status: active}
    
    Note over VP: Check daily limit
    VP->>R: GET vp:txn:daily:{wallet_id}:send_money:2024-01-15
    R-->>VP: "500.00" (accumulated today)
    alt Accumulated + amount > daily limit
        VP-->>C: 429 DAILY_LIMIT_EXCEEDED
    end
    
    Note over VP: Execute transaction (DB transaction)
    VP->>DB: BEGIN TRANSACTION
    VP->>DB: SELECT balance FOR UPDATE (sender)
    VP->>DB: SELECT balance FOR UPDATE (recipient)
    alt Insufficient balance
        VP->>DB: ROLLBACK
        VP-->>C: 402 INSUFFICIENT_BALANCE
    end
    VP->>DB: UPDATE wallet SET balance = balance - amount (sender)
    VP->>DB: UPDATE wallet SET balance = balance + amount (recipient)
    VP->>DB: INSERT transactions (debit row for sender)
    VP->>DB: INSERT transactions (credit row for recipient)
    VP->>DB: COMMIT
    
    Note over VP: Post-transaction
    VP->>R: INCRBYFLOAT vp:txn:daily:{wallet_id}:send_money:2024-01-15, amount
    VP->>R: DELETE vp:pin:attempts:{wallet_id}
    VP->>DB: INSERT notifications (for recipient)
    
    VP-->>C: 201 {debit_transaction, credit_transaction}
```

---

## 2. KYC Submission & Approval

**Two-phase flow**: User submits, Admin reviews

```mermaid
sequenceDiagram
    participant U as User (Client)
    participant VP as VaultPay API
    participant DB as PostgreSQL
    participant KYC as KYC Crypto Module
    participant A as Admin (Client)

    Note over U,VP: Phase 1: User Submission
    
    U->>VP: POST /kyc/submit<br/>{doc_type: "aadhar", doc_number: "1234 5678 9012"}
    VP->>DB: SELECT kyc WHERE wallet_id = ?
    alt KYC already exists
        VP-->>U: 409 KYC_ALREADY_SUBMITTED
    end
    
    VP->>KYC: encrypt(doc_number, KYC_ENCRYPTION_KEY)
    KYC-->>VP: ciphertext
    
    VP->>DB: INSERT kyc_submissions<br/>{wallet_id, doc_type, doc_number_encrypted: ciphertext,<br/>status: "pending"}
    VP->>DB: INSERT notifications (admin: "New KYC submission")
    
    VP-->>U: 201 {status: "pending", submitted_at: ...}

    Note over A,VP: Phase 2: Admin Review
    
    A->>VP: GET /admin/kyc/{submission_id}
    VP->>DB: SELECT kyc WHERE id = ?
    VP->>KYC: decrypt(doc_number_encrypted, KYC_ENCRYPTION_KEY)
    KYC-->>VP: "1234 5678 9012"
    VP->>DB: INSERT audit_logs<br/>{action: "VIEWED_KYC_DOCUMENT", target_id: submission_id}
    VP-->>A: 200 {doc_type, doc_number: "1234 5678 9012", status: "pending"}
    
    alt Admin approves
        A->>VP: POST /admin/kyc/{submission_id}/approve
        VP->>DB: BEGIN TRANSACTION
        VP->>DB: UPDATE kyc_submissions SET status = "verified", reviewed_by = admin_id
        VP->>DB: UPDATE wallets SET kyc_verified = true WHERE id = wallet_id
        VP->>DB: INSERT audit_logs {action: "APPROVED_KYC", before: pending, after: verified}
        VP->>DB: INSERT notifications (user: "KYC verified!")
        VP->>DB: COMMIT
        VP-->>A: 200 {status: "verified"}
    else Admin rejects
        A->>VP: POST /admin/kyc/{submission_id}/reject<br/>{rejection_reason: "Document expired"}
        VP->>DB: UPDATE kyc_submissions SET status = "rejected",<br/>rejection_reason = "Document expired"
        VP->>DB: INSERT audit_logs {action: "REJECTED_KYC"}
        VP->>DB: INSERT notifications (user: "KYC rejected: Document expired")
        VP-->>A: 200 {status: "rejected"}
    end
```

---

## 3. Admin Wallet Freeze

**Actor:** Moderator or Admin  
**Effect:** Wallet is frozen — all outgoing operations blocked

```mermaid
sequenceDiagram
    participant M as Moderator (Client)
    participant VP as VaultPay API
    participant DB as PostgreSQL
    participant AS as AuthShield

    M->>VP: POST /admin/wallets/{wallet_id}/freeze<br/>{reason: "Suspicious activity"}
    
    Note over VP: Authenticate + enforce role
    VP->>AS: GET /internal/validate-token
    AS-->>VP: {role: "moderator"}
    alt role is "user"
        VP-->>M: 403 INSUFFICIENT_PERMISSIONS
    end
    
    VP->>DB: SELECT wallet WHERE wallet_id = VPY-XXXXX
    DB-->>VP: wallet{status: "active"}
    
    alt Already frozen
        VP-->>M: 409 WALLET_ALREADY_FROZEN
    end
    
    alt Already closed
        VP-->>M: 409 WALLET_ALREADY_CLOSED
    end
    
    Note over VP: Snapshot before state
    VP->>VP: before_state = {status: "active"}
    
    VP->>DB: BEGIN TRANSACTION
    VP->>DB: UPDATE wallets SET status = "frozen"
    VP->>DB: INSERT audit_logs {<br/>actor_id: moderator_id,<br/>action: "FREEZE_WALLET",<br/>target_id: wallet_id,<br/>before_state: {status: "active"},<br/>after_state: {status: "frozen"},<br/>reason: "Suspicious activity"<br/>}
    VP->>DB: INSERT notifications (user: "Your wallet has been frozen. Contact support.")
    VP->>DB: COMMIT
    
    VP-->>M: 200 {wallet_id, status: "frozen", message: "Wallet frozen successfully"}
```

---

## Guard Conditions Summary

| Operation | Wallet Must Be | KYC | PIN | Balance Check |
|---|---|---|---|---|
| View balance | Any | No | No | No |
| Top up | Active | Yes | No | No |
| Send money | Active | Yes | Yes | Yes |
| Withdraw | Active | Yes | Yes | Yes |
| Freeze (self) | Active or Active | No | No | No |
| Unfreeze (self) | Frozen | No | No | No |
| Close wallet | Active | No | No | Zero balance |
| Admin freeze | Any non-closed | No | No | No |
