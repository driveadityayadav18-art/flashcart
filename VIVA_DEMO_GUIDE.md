# ⚡ FlashCart Viva Demonstration & Examiner Q&A Guide

> **Project Title:** FlashCart – Dark Store Inventory Management & 10-Minute SLA Delivery Engine  
> **Tech Stack:** MySQL 8.0 (InnoDB, Triggers, Stored Procedures, Views), Python 3.10+, Streamlit, Playwright

---

## 🚀 1. Quickstart Commands for Your Viva Presentation

Keep these terminal commands handy during your demonstration:

| Objective | Command | Notes |
| :--- | :--- | :--- |
| **Start Web Application** | `.\start_app.bat` or `streamlit run app.py` | Opens dashboard at `http://localhost:8501` |
| **Run Visual UI Robot Demo** | `python test_playwright_ui.py --headed` | **Top recommendation:** Opens a visible browser and clicks through the entire demo automatically! |
| **Run Full-Stack DBMS Assertions** | `python test_e2e_fullstack.py` | Proves all 10 schema, procedure, and trigger invariants in terminal |
| **1-Click Reset to Clean State** | `python reset_viva_state.py` | Restores pristine seed inventory, resets wallets, clears test orders |
| **Run Unit Test Suites** | `python test_auth.py`<br>`python test_crud_inventory.py`<br>`python test_inventory_restock.py` | Validates authentication, batch restock, and catalog CRUD |

---

## 🎬 2. The 3-Minute High-Impact Live Demonstration Script

Follow this script step-by-step when showing your project to your teacher or external examiner:

### Step 1: Customer Login & FEFO Inventory (45 Seconds)
1. Open `http://localhost:8501`.
2. Click **"👤 Login as Aarav Sharma (₹50 Wallet)"** (or log in via email `aarav@flashcart.com` / `flashcart123`).
3. Point to the sidebar:
   > *"Notice the customer profile is loaded with an initial wallet balance of ₹50.00."*
4. In the **"🛒 Order Groceries (Storefront)"** tab, point to the inventory table:
   > *"This inventory uses our FEFO (First-Expired, First-Out) protocol. Notice how batches with shorter expiration dates (e.g. fresh milk with 2 days left) are sorted first to prevent grocery spoilage."*
5. Select **Amul Taaza Fresh Milk 500ml**, quantity **2**, and click **"⚡ Place 10-Minute Order"**.
6. Show the **Live SLA Countdown Card**:
   > *"An order is created with a 12-minute SLA promise window (`DATE_ADD(NOW(), INTERVAL 12 MINUTE)`). Our custom JavaScript component runs a live countdown timer. Simultaneously, MySQL Trigger `trg_deduct_stock_after_order_item` autonomously decremented the inventory batch quantity inside the database."*

---

### Step 2: Dark Store Manager Dispatch (45 Seconds)
1. In the sidebar, click **"🚪 Sign Out"**.
2. Click the **"🏬 Dark Store Manager Login"** tab.
3. Click **"🏬 Manager Login: Indiranagar Hub"** (or use passkey `admin123`).
4. In the **"🛵 Hub Dispatch Console"** tab, point to the **Active Orders Queue**:
   > *"Here is the order #FC-X placed by Aarav Sharma. The status is currently `PLACED`."*
5. Select an IDLE rider (e.g. *Vikram Singh*) from the dropdown and click **"🛵 Dispatch Order"**.
6. Point to the notification and table:
   > *"When the order transitioned to `DISPATCHED`, MySQL Trigger `trg_sync_rider_status_on_order_update` automatically switched Vikram Singh's `duty_status` from `IDLE` to `DELIVERING`. No application-level manual update was needed."*

---

### Step 3: SLA Breach & Autonomous Refund (60 Seconds) - THE WOW FACTOR
1. Under the dispatched order, click **"🚨 Simulate SLA Breach (Late Delivery)"**.
2. Point to the **SLA Breach Alert Banner**:
   > *"We just simulated a late delivery where `actual_delivered_at > promised_by`. Watch what happened entirely within the database engine:"*
   - **Trigger 2:** Switched rider *Vikram Singh* back to `IDLE` ready for new deliveries.
   - **Trigger 3 (`trg_check_sla_breach_and_refund`):** Computed the delay minutes, created an immutable row in `sla_violations`, and **automatically credited ₹50.00** into Aarav Sharma's digital wallet!
3. Click the **"📊 Control Tower Analytics"** tab:
   > *"Notice the SLA Violations Audit Ledger: Order #FC-X is recorded with exact delay minutes and ₹50 compensation."*

---

### Step 4: Verification of Wallet Balance (30 Seconds)
1. Click **"🚪 Sign Out"** in the sidebar.
2. Click **"👤 Login as Aarav Sharma"**.
3. Point to the sidebar:
   > *"Aarav's wallet balance was ₹50.00. It is now **₹100.00** because of the autonomous ₹50 SLA compensation trigger!"*
4. Click **"👤 My Account & Profile"** tab to show customer details, password change, and soft account deletion.

---

## 🧠 3. High-Probability Examiner Questions & Ready Answers

### Q1: "Why did you use Database Triggers instead of handling stock deduction and refunds in Python?"
> **Answer:** *"Handling business rules in Python creates vulnerability to concurrency bugs, multiple client bypasses, and application crashes. By enforcing stock deduction (`trg_deduct_stock_after_order_item`) and SLA compensation (`trg_check_sla_breach_and_refund`) inside the MySQL engine, the database invariants are guaranteed 100% of the time, regardless of which client, API, or worker touches the table."*

### Q2: "How is your database schema normalized to 3NF?"
> **Answer:** *"The schema consists of 8 core tables:
> 1. **1NF:** Every column contains atomic values, with designated primary keys.
> 2. **2NF:** No partial dependencies. All non-key attributes in associative tables (`order_items`, `inventory_batches`) depend on the whole composite/primary key.
> 3. **3NF:** No transitive dependencies. For example, dark store details (`hub_name`, `pincode_zone`) are stored in `dark_stores`, not duplicated in `orders`. Product details are in `products`, not `order_items`."*

### Q3: "What is FEFO, and how does it differ from standard FIFO?"
> **Answer:** *"FIFO (First-In, First-Out) sorts by arrival date (`mfg_date`). In quick-commerce perishables (milk, bread, fresh produce), a newer batch could arrive with a closer expiration date due to supplier variations. FEFO (First-Expired, First-Out) sorts strictly by `expiry_date ASC` using `DATEDIFF(expiry_date, CURDATE())`, ensuring the closest expiring stock is dispatched first to minimize spoilage waste."*

### Q4: "How does the system prevent race conditions when two customers buy the last item simultaneously?"
> **Answer:** *"We designed the atomic stored procedure `sp_place_order_atomic` using MySQL row-level locking (`SELECT stock_qty ... FROM inventory_batches WHERE batch_id = p_batch_id FOR UPDATE`). The first transaction locks the batch row. The second transaction waits until the first commits. If remaining stock drops below the requested quantity, the procedure aborts with `ERROR_INSUFFICIENT_STOCK` and rolls back."*

### Q5: "Why did you implement Soft Deletion for customer accounts?"
> **Answer:** *"In 3NF relational modeling, the `orders` table holds a foreign key referencing `customer_id` with `ON DELETE RESTRICT` to preserve historical financial records and SLA audit trails. A physical `DELETE` would fail or delete historical orders. Soft deletion sets `is_active = FALSE`, which immediately blocks login and deactivates the account while keeping all historical audit rows intact."*

---

## 📜 4. The 3 Core MySQL Event Triggers (Code Reference)

### Trigger 1: Autonomous Stock Depletion
```sql
CREATE TRIGGER trg_deduct_stock_after_order_item 
AFTER INSERT ON order_items 
FOR EACH ROW 
BEGIN
    UPDATE inventory_batches
    SET stock_qty = stock_qty - NEW.quantity
    WHERE batch_id = NEW.batch_id;
END;
```

### Trigger 2: Fleet State Machine
```sql
CREATE TRIGGER trg_sync_rider_status_on_order_update 
AFTER UPDATE ON orders 
FOR EACH ROW 
BEGIN
    IF NEW.order_status = 'DISPATCHED' AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'DELIVERING'
        WHERE rider_id = NEW.rider_id;
    END IF;

    IF NEW.order_status IN ('DELIVERED', 'CANCELLED') AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'IDLE'
        WHERE rider_id = NEW.rider_id;
    END IF;
END;
```

### Trigger 3: SLA Breach Detection & Auto-Compensation
```sql
CREATE TRIGGER trg_check_sla_breach_and_refund 
AFTER UPDATE ON orders 
FOR EACH ROW 
BEGIN
    DECLARE v_promised_mins INT;
    DECLARE v_actual_mins INT;
    DECLARE v_delay_mins INT;
    DECLARE v_refund_amount DECIMAL(10, 2) DEFAULT 50.00;

    IF NEW.order_status = 'DELIVERED' 
       AND OLD.order_status <> 'DELIVERED' 
       AND NEW.actual_delivered_at IS NOT NULL THEN
        
        SET v_promised_mins = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.promised_by);
        SET v_actual_mins   = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.actual_delivered_at);
        
        IF v_actual_mins > v_promised_mins THEN
            SET v_delay_mins = v_actual_mins - v_promised_mins;
            
            INSERT INTO sla_violations (
                order_id, promised_mins, actual_mins_taken, delay_minutes, refund_amount, logged_at
            ) VALUES (
                NEW.order_id, v_promised_mins, v_actual_mins, v_delay_mins, v_refund_amount, NOW()
            );
            
            UPDATE customers
            SET wallet_balance = wallet_balance + v_refund_amount
            WHERE customer_id = NEW.customer_id;
        END IF;
    END IF;
END;
```
