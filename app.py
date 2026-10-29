"""
app.py: FlashCart 10-Min SLA Engine - Streamlit Application.
Provides role-based authentication (Customer & Dark Store Manager), self-serve registration,
profile editing, account deletion, storefront ordering with live SLA tracking, and hub dispatch operations.
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any, Optional
import pandas as pd
import streamlit as st
from mysql.connector.errors import Error as MySQLError

from db import (
    run_query,
    place_order,
    load_active_orders_for_store,
    load_idle_riders_for_store,
    load_all_riders_for_store,
    dispatch_order,
    deliver_order_ontime,
    deliver_order_sla_breach,
    load_sla_violations,
    load_fefo_perishables,
    load_hub_sla_performance,
    load_hourly_order_volume,
    load_store_batches_all,
    update_batch_stock,
    restock_batch,
    deduct_batch_stock,
    reset_demo_data,
    authenticate_customer,
    register_customer,
    update_customer_profile,
    delete_customer_account,
    get_customer_by_id,
    load_all_products,
    load_all_categories,
    create_product,
    update_product,
    delete_product,
    create_batch,
    delete_batch,
    load_raw_table_data,
    load_all_tables_summary,
    update_customer_wallet,
    top_up_customer_wallet,
)

# Set Streamlit page configuration
st.set_page_config(
    page_title="FlashCart 10-Min SLA Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .order-card {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1.5rem;
        color: #F8FAFC;
        margin-bottom: 1.8rem;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.2);
    }
    .order-title {
        font-size: 1.25rem;
        font-weight: 700;
        color: #38BDF8;
    }
    .sla-timer {
        font-size: 1.5rem;
        font-weight: 800;
        color: #FACC15;
    }
    .badge-active {
        background-color: #059669;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-role-customer {
        background-color: #0284C7;
        color: white;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 700;
    }
    .badge-role-admin {
        background-color: #7C3AED;
        color: white;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 700;
    }
    .trigger-proof-box {
        background: #F0FDF4;
        border: 1px solid #86EFAC;
        border-left: 5px solid #16A34A;
        border-radius: 8px;
        padding: 14px 18px;
        margin-top: 10px;
        margin-bottom: 15px;
    }
    .danger-box {
        background: #FFF1F2;
        border: 1px solid #FECDD3;
        border-left: 5px solid #E11D48;
        border-radius: 8px;
        padding: 16px;
        margin-top: 15px;
        margin-bottom: 15px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_customers() -> pd.DataFrame:
    """Fetch active customers from the database."""
    query = """
        SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
        FROM customers 
        WHERE is_active = TRUE
        ORDER BY name ASC
    """
    return run_query(query)


def load_active_stores() -> pd.DataFrame:
    """Fetch dark stores where operating_status is ACTIVE."""
    query = """
        SELECT store_id, hub_name, pincode_zone, operating_status 
        FROM dark_stores 
        WHERE operating_status = 'ACTIVE' 
        ORDER BY hub_name ASC
    """
    return run_query(query)


def load_store_inventory(store_id: int) -> pd.DataFrame:
    """
    Fetch available inventory for a dark store by joining inventory_batches and products.
    Calculates shelf life days remaining.
    """
    query = """
        SELECT 
            b.batch_id,
            p.product_id,
            p.name AS `Product Name`,
            p.category AS `Category`,
            p.unit_price AS `Unit Price`,
            b.stock_qty AS `Stock Qty`,
            b.expiry_date AS `Expiry Date`,
            DATEDIFF(b.expiry_date, CURDATE()) AS `shelf_life_days`
        FROM inventory_batches b
        JOIN products p ON b.product_id = p.product_id
        WHERE b.store_id = %s AND b.stock_qty > 0
        ORDER BY b.expiry_date ASC, p.name ASC
    """
    return run_query(query, (store_id,))


def compute_shelf_life_badge(days: Any) -> str:
    """Formats shelf life days into a badge string."""
    if days is None or pd.isna(days):
        return "⚪ Unknown"
    days = int(days)
    if days < 0:
        return f"🔴 Expired ({abs(days)}d ago)"
    elif days == 0:
        return "⚠️ Expires Today"
    elif days <= 3:
        return f"🟡 {days}d (Critical)"
    elif days <= 14:
        return f"🟠 {days}d (Short)"
    else:
        return f"🟢 {days}d (Fresh)"


# Initialize session state variables
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None


# ==========================================
# AUTHENTICATION & REGISTRATION PORTAL
# ==========================================
if st.session_state["auth_user"] is None:
    st.markdown('<div class="main-header">⚡ FlashCart – 10-Minute SLA Engine</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Relational DBMS Quick-Commerce Architecture • FEFO Inventory • Autonomous MySQL Triggers</div>',
        unsafe_allow_html=True,
    )

    if "delete_notice" in st.session_state:
        st.info(st.session_state["delete_notice"])
        del st.session_state["delete_notice"]

    if "action_notice" in st.session_state:
        st.success(st.session_state["action_notice"])
        del st.session_state["action_notice"]

    tab_login, tab_register, tab_admin = st.tabs([
        "👤 Customer Sign In",
        "📝 Register New Customer",
        "🏬 Dark Store Manager Login",
    ])

    # 1. Customer Sign In
    with tab_login:
        st.markdown("### Sign In to Your FlashCart Account")
        st.caption("Access your grocery cart, active orders, and live wallet balance.")

        col_form, col_quick = st.columns([3, 2])

        with col_form:
            with st.form("customer_login_form"):
                login_ident = st.text_input("Email Address or Mobile Number", placeholder="e.g. aarav@flashcart.com or 9876543210")
                login_pwd = st.text_input("Password", type="password", placeholder="Enter your password")
                submit_login = st.form_submit_button("Sign In", type="primary", use_container_width=True)

                if submit_login:
                    if not login_ident or not login_pwd:
                        st.error("Please enter both your email/phone and password.")
                    else:
                        cust = authenticate_customer(login_ident, login_pwd)
                        if cust:
                            st.session_state["auth_user"] = {
                                "role": "customer",
                                **cust,
                            }
                            st.session_state["action_notice"] = f"Welcome back, {cust['name']}! Ready for ultra-fast 10-minute delivery."
                            st.rerun()
                        else:
                            st.error("Invalid credentials or account deactivated. Please check your login details.")

        with col_quick:
            st.markdown(
                """
                <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px; padding:16px;">
                    <h4 style="margin:0 0 8px 0; color:#0F172A; font-size:1rem;">⚡ Quick Viva Demo Logins</h4>
                    <p style="margin:0 0 12px 0; color:#64748B; font-size:0.85rem;">One-click login for pre-seeded viva test accounts (Password: <code>flashcart123</code>):</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.write("")
            if st.button("👤 Login as Aarav Sharma (₹50 Wallet)", use_container_width=True, key="demo_aarav"):
                cust = authenticate_customer("aarav@flashcart.com", "flashcart123")
                if cust:
                    st.session_state["auth_user"] = {"role": "customer", **cust}
                    st.rerun()
            if st.button("👤 Login as Diya Nair (₹0 Wallet)", use_container_width=True, key="demo_diya"):
                cust = authenticate_customer("diya@flashcart.com", "flashcart123")
                if cust:
                    st.session_state["auth_user"] = {"role": "customer", **cust}
                    st.rerun()
            if st.button("👤 Login as Rohan Verma (₹120 Wallet)", use_container_width=True, key="demo_rohan"):
                cust = authenticate_customer("rohan@flashcart.com", "flashcart123")
                if cust:
                    st.session_state["auth_user"] = {"role": "customer", **cust}
                    st.rerun()

    # 2. Register New Customer
    with tab_register:
        st.markdown("### Create a New FlashCart Customer Account")
        st.caption("Sign up now and receive an instant **₹100 Welcome Bonus** credited to your digital wallet!")

        with st.form("customer_register_form"):
            reg_name = st.text_input("Full Name", placeholder="e.g. Priya Sharma")
            col_reg_c1, col_reg_c2 = st.columns(2)
            with col_reg_c1:
                reg_email = st.text_input("Email Address", placeholder="e.g. priya@example.com")
            with col_reg_c2:
                reg_phone = st.text_input("Mobile Phone (10 digits)", placeholder="e.g. 9876500001")

            reg_address = st.text_area("Delivery Address (Flat / House, Building, Area, Pincode)", placeholder="Flat 304, Green Woods, Indiranagar, Bangalore - 560038")

            col_pwd1, col_pwd2 = st.columns(2)
            with col_pwd1:
                reg_pwd = st.text_input("Create Password", type="password", placeholder="Minimum 6 characters")
            with col_pwd2:
                reg_pwd_confirm = st.text_input("Confirm Password", type="password", placeholder="Re-type password")

            submit_register = st.form_submit_button("🚀 Create Account & Claim ₹100 Welcome Bonus", type="primary", use_container_width=True)

            if submit_register:
                if not reg_name or not reg_email or not reg_phone or not reg_address or not reg_pwd:
                    st.error("Please fill in all registration fields.")
                elif reg_pwd != reg_pwd_confirm:
                    st.error("Passwords do not match. Please re-enter your password.")
                elif len(reg_pwd) < 4:
                    st.error("Password should be at least 4 characters long.")
                else:
                    try:
                        new_cust = register_customer(
                            name=reg_name,
                            email=reg_email,
                            phone=reg_phone,
                            address=reg_address,
                            raw_password=reg_pwd,
                            initial_wallet=100.00,
                        )
                        st.session_state["auth_user"] = {
                            "role": "customer",
                            **new_cust,
                        }
                        st.session_state["action_notice"] = f"🎉 Welcome to FlashCart, {new_cust['name']}! ₹100 Welcome Credit has been deposited into your wallet."
                        st.rerun()
                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception as err:
                        st.error(f"Failed to register account: {err}")

    # 3. Dark Store Manager Login
    with tab_admin:
        st.markdown("### Dark Store Operations Control Tower")
        st.caption("Fulfillment hub manager access: dispatch orders, monitor rider fleet, and audit SLA violations.")

        stores_df = load_active_stores()
        if stores_df.empty:
            st.error("No active dark stores available in database.")
        else:
            col_admin_form, col_admin_quick = st.columns([3, 2])

            with col_admin_form:
                store_choices = {
                    int(row["store_id"]): f"{row['hub_name']} (PIN: {row['pincode_zone']})"
                    for _, row in stores_df.iterrows()
                }

                with st.form("admin_login_form"):
                    chosen_store_id = st.selectbox(
                        "Select Dark Store Hub",
                        options=list(store_choices.keys()),
                        format_func=lambda sid: store_choices[sid],
                    )
                    admin_passkey = st.text_input("Store Manager Passkey", type="password", placeholder="Default demo passkey: admin123")
                    submit_admin = st.form_submit_button("🔑 Enter Store Operations Control Tower", type="primary", use_container_width=True)

                    if submit_admin:
                        if admin_passkey.strip() == "admin123":
                            sel_store_row = stores_df[stores_df["store_id"] == chosen_store_id].iloc[0]
                            st.session_state["auth_user"] = {
                                "role": "admin",
                                "store_id": chosen_store_id,
                                "hub_name": sel_store_row["hub_name"],
                                "pincode_zone": sel_store_row["pincode_zone"],
                            }
                            st.session_state["action_notice"] = f"Logged in as Hub Manager for {sel_store_row['hub_name']}."
                            st.rerun()
                        else:
                            st.error("Invalid Manager Passkey. (Hint: Use 'admin123' for demo access)")

            with col_admin_quick:
                st.markdown(
                    """
                    <div style="background:#F5F3FF; border:1px solid #DDD6FE; border-radius:10px; padding:16px;">
                        <h4 style="margin:0 0 8px 0; color:#5B21B6; font-size:1rem;">⚡ 1-Click Demo Manager</h4>
                        <p style="margin:0 0 12px 0; color:#6D28D9; font-size:0.85rem;">Instantly enter the control tower as Manager for Indiranagar Dark Store Hub:</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.write("")
                if st.button("🏬 Manager Login: Indiranagar Hub", use_container_width=True, key="quick_admin_indira"):
                    indira_row = stores_df[stores_df["store_id"] == 1].iloc[0] if (stores_df["store_id"] == 1).any() else stores_df.iloc[0]
                    st.session_state["auth_user"] = {
                        "role": "admin",
                        "store_id": int(indira_row["store_id"]),
                        "hub_name": indira_row["hub_name"],
                        "pincode_zone": indira_row["pincode_zone"],
                    }
                    st.rerun()

    # Stop execution when unauthenticated
    st.stop()


# ==========================================
# AUTHENTICATED APPLICATION
# ==========================================
auth_user = st.session_state["auth_user"]
is_customer = auth_user.get("role") == "customer"
is_admin = auth_user.get("role") == "admin"

# If customer, reload fresh profile from DB to reflect real-time wallet credits / updates
if is_customer:
    fresh_cust = get_customer_by_id(auth_user["customer_id"])
    if fresh_cust is None or not fresh_cust.get("is_active"):
        st.session_state["auth_user"] = None
        st.session_state["delete_notice"] = "Your account is no longer active."
        st.rerun()
    # Update session customer state
    auth_user.update(fresh_cust)
    st.session_state["auth_user"] = auth_user


# ==========================================
# SIDEBAR
# ==========================================
with st.sidebar:
    st.title("⚡ FlashCart Engine")
    st.caption("10-Minute Instant Delivery System")
    st.divider()

    # Viva Feature: Reset Demo Data
    st.subheader("🛠️ Viva Demonstration Tools")
    st.caption("Instantly reset orders, inventory, riders, and customer wallets for re-testing during viva evaluation.")
    if st.button("🔄 Reset Demo Data", type="secondary", use_container_width=True, help="Clears orders, resets inventory to seed, restores rider status to IDLE, and resets wallets."):
        try:
            reset_result = reset_demo_data()
            if "confirmed_order" in st.session_state:
                del st.session_state["confirmed_order"]
            if "sla_breach_banner" in st.session_state:
                del st.session_state["sla_breach_banner"]
            if "order_placed_proof" in st.session_state:
                del st.session_state["order_placed_proof"]
            st.session_state["action_notice"] = "🔄 Demo database successfully reset! All orders cleared, inventory re-seeded, riders IDLE, and customer wallets restored to initial values."
            st.rerun()
        except Exception as e:
            st.error(f"Failed to reset demo data: {e}")

    st.divider()

    try:
        stores_df = load_active_stores()
    except Exception as e:
        st.error(f"Failed to load dark stores: {e}")
        stores_df = pd.DataFrame()

    # 1. Profile / Identity Card in Sidebar
    if is_customer:
        st.markdown(
            f"""
            <div style="background:#F0F9FF; border:1px solid #BAE6FD; border-radius:10px; padding:14px; margin-bottom:12px;">
                <span class="badge-role-customer">CUSTOMER</span>
                <h4 style="margin:6px 0 2px 0; color:#0369A1;">{auth_user['name']}</h4>
                <p style="margin:0; font-size:0.8rem; color:#0284C7;">✉️ {auth_user.get('email', 'N/A')}</p>
                <p style="margin:2px 0 0; font-size:0.8rem; color:#0284C7;">📞 {auth_user.get('phone', 'N/A')}</p>
                <p style="margin:4px 0 0; font-size:0.8rem; color:#475569;">📍 {auth_user.get('delivery_address', 'N/A')}</p>
                <hr style="margin:8px 0; border:0; border-top:1px solid #BAE6FD;"/>
                <p style="margin:0; font-size:0.95rem; font-weight:700; color:#0F766E;">💰 Wallet: ₹{float(auth_user.get('wallet_balance', 0.0)):,.2f}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif is_admin:
        st.markdown(
            f"""
            <div style="background:#F5F3FF; border:1px solid #DDD6FE; border-radius:10px; padding:14px; margin-bottom:12px;">
                <span class="badge-role-admin">STORE MANAGER</span>
                <h4 style="margin:6px 0 2px 0; color:#5B21B6;">{auth_user.get('hub_name', 'Dark Store Hub')}</h4>
                <p style="margin:0; font-size:0.85rem; color:#6D28D9;">Pincode Zone: <strong>{auth_user.get('pincode_zone', 'N/A')}</strong></p>
                <p style="margin:4px 0 0; font-size:0.8rem; color:#7C3AED;">Operational Dispatch & SLA Audit Access</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. Dark Store Hub Selector
    selected_store = None
    if not stores_df.empty:
        st.subheader("🏬 Active Fulfillment Hub")
        store_options = {
            int(row["store_id"]): f"{row['hub_name']} (PIN: {row['pincode_zone']})"
            for _, row in stores_df.iterrows()
        }

        # Default to user's assigned store if admin
        default_store_id = auth_user.get("store_id", list(store_options.keys())[0])
        default_index = list(store_options.keys()).index(int(default_store_id)) if int(default_store_id) in store_options else 0

        selected_store_id = st.selectbox(
            "Select Dark Store",
            options=list(store_options.keys()),
            format_func=lambda sid: store_options[sid],
            index=default_index,
            key="selected_store_id",
        )
        selected_store = stores_df[stores_df["store_id"] == selected_store_id].iloc[0]

        if is_admin and selected_store_id != auth_user.get("store_id"):
            auth_user["store_id"] = selected_store_id
            auth_user["hub_name"] = selected_store["hub_name"]
            auth_user["pincode_zone"] = selected_store["pincode_zone"]
            st.session_state["auth_user"] = auth_user

    st.write("")
    col_so1, col_so2 = st.columns(2)
    with col_so1:
        if st.button("🚪 Sign Out", type="secondary", use_container_width=True):
            st.session_state["auth_user"] = None
            if "confirmed_order" in st.session_state:
                del st.session_state["confirmed_order"]
            st.rerun()
    with col_so2:
        if st.button("🔄 Reset Demo", type="secondary", use_container_width=True, help="Wipes test orders & restores pristine seed state"):
            res = reset_demo_data()
            if res.get("status") == "success":
                st.session_state["action_notice"] = "⚡ Database restored to clean viva demo state!"
                st.rerun()


# ==========================================
# MAIN CONTENT AREA
# ==========================================
st.markdown('<div class="main-header">⚡ FlashCart 10-Minute SLA Engine</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Ultra-fast micro-fulfillment powered by MySQL triggers & real-time SLA tracking</div>',
    unsafe_allow_html=True,
)

if "action_notice" in st.session_state:
    st.success(st.session_state["action_notice"])
    del st.session_state["action_notice"]


# ----------------------------------------------------
# A. CUSTOMER EXPERIENCE (Storefront & Profile)
# ----------------------------------------------------
if is_customer:
    tab_storefront, tab_profile = st.tabs([
        "🛒 Order Groceries (Storefront)",
        "👤 My Account & Profile",
    ])

    with tab_storefront:
        # Confirmation Card & Live Countdown
        if "confirmed_order" in st.session_state:
            order_info = st.session_state["confirmed_order"]
            promised_dt = order_info.get("promised_by")
            order_id = order_info.get("order_id")
            product_name = order_info.get("product_name", "Product")
            quantity = order_info.get("quantity", 1)
            total_amount = float(order_info.get("total_amount", 0.0))

            if promised_dt:
                if isinstance(promised_dt, str):
                    try:
                        promised_dt = datetime.fromisoformat(promised_dt)
                    except Exception:
                        pass

                now_dt = datetime.now()
                time_diff = (promised_dt - now_dt).total_seconds() if hasattr(promised_dt, "__sub__") else 720
                remaining_seconds = max(0, int(time_diff))
                deadline_str = (
                    promised_dt.strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(promised_dt, "strftime")
                    else str(promised_dt)
                )

                card_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                <meta charset="utf-8"/>
                <style>
                  * {{
                    box-sizing: border-box;
                    margin: 0;
                    padding: 0;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                  }}
                  body {{
                    background: transparent;
                    padding: 4px;
                  }}
                  .order-card {{
                    background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
                    border: 1px solid #334155;
                    border-radius: 12px;
                    padding: 16px 20px;
                    color: #F8FAFC;
                    box-shadow: 0 8px 20px -4px rgba(0, 0, 0, 0.35);
                  }}
                  .header-row {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                  }}
                  .badge-confirmed {{
                    background: #0284C7;
                    color: white;
                    padding: 3px 10px;
                    border-radius: 12px;
                    font-size: 0.75rem;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                    display: inline-block;
                  }}
                  .order-title {{
                    font-size: 1.2rem;
                    font-weight: 700;
                    color: #38BDF8;
                    margin-top: 4px;
                  }}
                  .deadline-label {{
                    font-size: 0.8rem;
                    color: #94A3B8;
                  }}
                  .deadline-value {{
                    font-size: 1rem;
                    font-weight: 700;
                    color: #38BDF8;
                  }}
                  hr {{
                    border: 0;
                    border-top: 1px solid #334155;
                    margin: 12px 0;
                  }}
                  .body-row {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                  }}
                  .item-desc {{
                    font-size: 0.9rem;
                    color: #CBD5E1;
                    margin-bottom: 3px;
                  }}
                  .countdown-box {{
                    text-align: right;
                  }}
                  .countdown-label {{
                    font-size: 0.75rem;
                    color: #FCD34D;
                    font-weight: 600;
                    letter-spacing: 0.5px;
                  }}
                  .sla-timer {{
                    font-size: 1.6rem;
                    font-weight: 800;
                    color: #FACC15;
                    font-variant-numeric: tabular-nums;
                    margin-top: 2px;
                  }}
                </style>
                </head>
                <body>
                  <div class="order-card">
                    <div class="header-row">
                      <div>
                        <span class="badge-confirmed">ORDER CONFIRMED</span>
                        <div class="order-title">Order ID: #FC-{order_id}</div>
                      </div>
                      <div style="text-align: right;">
                        <div class="deadline-label">10-Min SLA Deadline:</div>
                        <div class="deadline-value">{deadline_str}</div>
                      </div>
                    </div>
                    <hr/>
                    <div class="body-row">
                      <div>
                        <div class="item-desc">Item: <strong>{product_name}</strong> (Qty: {quantity})</div>
                        <div class="item-desc">Amount: <strong>₹{total_amount:,.2f}</strong></div>
                      </div>
                      <div class="countdown-box">
                        <div id="sla-status-label" class="countdown-label">⚡ LIVE SLA COUNTDOWN</div>
                        <div id="sla-timer" class="sla-timer">⏱️ calculating...</div>
                      </div>
                    </div>
                  </div>

                  <script>
                    (function() {{
                      const remainingSecs = {remaining_seconds};
                      const endTime = Date.now() + (remainingSecs * 1000);
                      const timerEl = document.getElementById("sla-timer");
                      const statusLabel = document.getElementById("sla-status-label");

                      function updateCountdown() {{
                        const now = Date.now();
                        const diffMs = endTime - now;
                        const diffSec = Math.max(0, Math.floor(diffMs / 1000));

                        const mins = Math.floor(diffSec / 60);
                        const secs = diffSec % 60;
                        const pad = (n) => String(n).padStart(2, '0');

                        if (diffSec <= 0) {{
                          timerEl.innerHTML = "🚨 00:00 (SLA BREACHED)";
                          timerEl.style.color = "#EF4444";
                          if (statusLabel) {{
                            statusLabel.innerHTML = "⚠️ SLA BREACHED";
                            statusLabel.style.color = "#EF4444";
                          }}
                        }} else {{
                          timerEl.innerHTML = "⏱️ " + pad(mins) + ":" + pad(secs);
                          if (diffSec <= 120) {{
                            timerEl.style.color = "#EF4444";
                            if (statusLabel) {{
                              statusLabel.innerHTML = "⚠️ SLA CRITICAL";
                              statusLabel.style.color = "#EF4444";
                            }}
                          }} else if (diffSec <= 300) {{
                            timerEl.style.color = "#F59E0B";
                          }} else {{
                            timerEl.style.color = "#FACC15";
                          }}
                        }}
                      }}

                      updateCountdown();
                      setInterval(updateCountdown, 1000);
                    }})();
                  </script>
                </body>
                </html>
                """

                col_card, col_close = st.columns([5, 1])
                with col_card:
                    import streamlit.components.v1 as components
                    components.html(card_html, height=170)

                with col_close:
                    st.write("")
                    st.write("")
                    if st.button("✕ Dismiss", key="dismiss_order_card", use_container_width=True):
                        del st.session_state["confirmed_order"]
                        st.rerun()

        # Trigger proof message
        if "order_placed_proof" in st.session_state:
            proof = st.session_state["order_placed_proof"]
            st.success(
                f"⚡ **Live Trigger Proof (Inventory Deduction):** MySQL Trigger `trg_deduct_stock_after_order_item` "
                f"executed autonomously inside the MySQL engine. Stock for Batch #{proof.get('batch_id')} ({proof.get('product_name')}) "
                f"was automatically decremented by {proof.get('quantity')} units!"
            )
            if st.button("✕ Dismiss Trigger Proof", key="dismiss_placed_proof_btn"):
                del st.session_state["order_placed_proof"]
                st.rerun()

        if selected_store is None:
            st.warning("⚠️ Please select an Active Dark Store from the sidebar.")
        else:
            st.subheader(f"📦 Available Fresh Inventory at {selected_store['hub_name']}")

            try:
                inventory_df = load_store_inventory(int(selected_store["store_id"]))
            except Exception as e:
                st.error(f"Error loading inventory: {e}")
                inventory_df = pd.DataFrame()

            if inventory_df.empty:
                st.warning("⚠️ No available inventory found in this Dark Store.")
            else:
                display_df = inventory_df.copy()
                display_df["Unit Price"] = display_df["Unit Price"].apply(lambda p: float(p) if p is not None else 0.0)
                display_df["Stock Qty"] = display_df["Stock Qty"].fillna(0).astype(int)
                display_df["Shelf Life"] = display_df["shelf_life_days"].apply(compute_shelf_life_badge)

                columns_to_show = [
                    "Product Name",
                    "Category",
                    "Unit Price",
                    "Stock Qty",
                    "Expiry Date",
                    "Shelf Life",
                ]

                st.dataframe(
                    display_df[columns_to_show],
                    column_config={
                        "Unit Price": st.column_config.NumberColumn("Unit Price", format="₹%.2f"),
                        "Stock Qty": st.column_config.NumberColumn("Stock Qty", format="%d units"),
                        "Expiry Date": st.column_config.DateColumn("Expiry Date", format="YYYY-MM-DD"),
                        "Shelf Life": st.column_config.TextColumn("Shelf Life (Badge)"),
                    },
                    use_container_width=True,
                    hide_index=True,
                )

                st.divider()

                # Order Placement Form (Pre-bound to authenticated customer)
                st.subheader("🚀 Place Instant 10-Minute Order")

                with st.container(border=True):
                    col_prod, col_qty = st.columns([3, 1])

                    product_choices = {}
                    for _, row in inventory_df.iterrows():
                        u_price = float(row['Unit Price']) if row['Unit Price'] is not None else 0.0
                        label = (
                            f"{row['Product Name']} ({row['Category']}) — ₹{u_price:.2f} "
                            f"| In Stock: {row['Stock Qty']} | Expiry: {row['Expiry Date']}"
                        )
                        product_choices[row["batch_id"]] = {
                            "label": label,
                            "batch_id": int(row["batch_id"]),
                            "product_id": int(row["product_id"]),
                            "product_name": row["Product Name"],
                            "unit_price": u_price,
                            "stock_qty": int(row["Stock Qty"]),
                        }

                    with col_prod:
                        selected_batch_id = st.selectbox(
                            "Select Product",
                            options=list(product_choices.keys()),
                            format_func=lambda bid: product_choices[bid]["label"],
                            key="storefront_select_batch_id",
                        )

                    chosen_item = product_choices[selected_batch_id]
                    max_qty = max(1, chosen_item["stock_qty"])

                    with col_qty:
                        order_quantity = st.number_input(
                            "Enter Quantity",
                            min_value=1,
                            max_value=max_qty,
                            value=1,
                            step=1,
                            help=f"Maximum available in this batch: {max_qty}",
                            key=f"storefront_order_qty_{selected_batch_id}",
                        )

                    total_cost = float(order_quantity) * float(chosen_item["unit_price"])

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Unit Price", f"₹{float(chosen_item['unit_price']):.2f}")
                    c2.metric("Order Quantity", f"{order_quantity} unit(s)")
                    c3.metric("Total Order Amount", f"₹{total_cost:,.2f}")

                    st.caption(f"📍 Delivering to: **{auth_user['name']}** at `{auth_user['delivery_address']}`")
                    st.caption("⚡ Delivery Guarantee: Promised delivery in 10-12 minutes. Autonomous MySQL triggers guarantee inventory consistency and SLA auditing.")

                    submit_order = st.button(
                        "⚡ Place 10-Minute Order",
                        type="primary",
                        use_container_width=True,
                        key="place_order_submit_btn",
                    )

                    if submit_order:
                        if order_quantity > chosen_item["stock_qty"]:
                            st.error(f"Insufficient stock! Requested: {order_quantity}, Available: {chosen_item['stock_qty']}")
                        else:
                            try:
                                with st.spinner("Dispatching order and locking inventory..."):
                                    created_order = place_order(
                                        customer_id=int(auth_user["customer_id"]),
                                        store_id=int(selected_store["store_id"]),
                                        batch_id=chosen_item["batch_id"],
                                        quantity=int(order_quantity),
                                        item_price=float(chosen_item["unit_price"]),
                                    )

                                st.session_state["confirmed_order"] = {
                                    "order_id": created_order["order_id"],
                                    "customer_id": created_order["customer_id"],
                                    "store_id": created_order["store_id"],
                                    "rider_id": created_order["rider_id"],
                                    "order_placed_at": created_order["order_placed_at"],
                                    "promised_by": created_order["promised_by"],
                                    "product_name": chosen_item["product_name"],
                                    "quantity": order_quantity,
                                    "total_amount": total_cost,
                                }

                                st.session_state["order_placed_proof"] = {
                                    "batch_id": chosen_item["batch_id"],
                                    "product_name": chosen_item["product_name"],
                                    "quantity": order_quantity,
                                }

                                st.success(f"Order #FC-{created_order['order_id']} placed successfully!")
                                st.rerun()

                            except MySQLError as db_err:
                                st.error(f"Database error while placing order: {db_err}")
                            except ValueError as val_err:
                                st.warning(str(val_err))
                            except Exception as err:
                                st.error(f"Unexpected error: {err}")

    # Tab 2: Profile & Account Management
    with tab_profile:
        st.subheader("👤 My Account Profile & Settings")
        st.caption("Manage your profile credentials, delivery address, and account lifecycle.")

        # Account Details Summary Card
        m_since = str(auth_user.get("created_at", "N/A"))
        c_wallet = float(auth_user.get("wallet_balance", 0.0))

        st.markdown(
            f"""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:12px; padding:20px; margin-bottom:20px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <h3 style="margin:0; color:#0F172A;">{auth_user['name']}</h3>
                        <p style="margin:4px 0 0; color:#64748B; font-size:0.9rem;">Customer ID: <strong>#CUST-{auth_user['customer_id']}</strong> • Member Since: {m_since[:10] if len(m_since)>=10 else m_since}</p>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-size:0.85rem; color:#64748B;">Digital Wallet Balance</span>
                        <div style="font-size:1.5rem; font-weight:800; color:#0F766E;">₹{c_wallet:,.2f}</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Digital Wallet Management & Top-Up
        st.markdown("#### 💳 Digital Wallet Management")
        with st.container(border=True):
            col_w_info, col_w_inputs, col_w_action = st.columns([2, 2, 1])
            with col_w_info:
                st.markdown(f"**Current Balance:** `₹{c_wallet:,.2f}`")
                st.caption("Add funds to your digital wallet for instant 10-minute checkouts or manage your account balance.")
            with col_w_inputs:
                wallet_opt = st.radio(
                    "Select Action",
                    ["➕ Top-Up Wallet (+₹)", "⚙️ Set Exact Balance (=₹)"],
                    horizontal=True,
                    key="wallet_mgmt_radio",
                )
                if "Top-Up" in wallet_opt:
                    w_input_val = st.number_input(
                        "Amount to Add (₹)",
                        min_value=10.0,
                        max_value=50000.0,
                        value=100.0,
                        step=50.0,
                        format="%.2f",
                        key="wallet_add_input",
                    )
                else:
                    w_input_val = st.number_input(
                        "New Balance (₹)",
                        min_value=0.0,
                        max_value=100000.0,
                        value=c_wallet,
                        step=50.0,
                        format="%.2f",
                        key="wallet_set_input",
                    )
            with col_w_action:
                st.write("")
                st.write("")
                if "Top-Up" in wallet_opt:
                    if st.button("💳 Add Funds", type="primary", use_container_width=True, key="btn_topup_funds"):
                        try:
                            updated_cust = top_up_customer_wallet(int(auth_user["customer_id"]), float(w_input_val))
                            st.session_state["auth_user"] = {
                                "role": "customer",
                                **updated_cust,
                            }
                            st.session_state["action_notice"] = f"💳 Successfully credited ₹{float(w_input_val):,.2f} to your digital wallet! New Balance: ₹{float(updated_cust['wallet_balance']):,.2f}"
                            st.rerun()
                        except Exception as w_err:
                            st.error(f"Failed to top up wallet: {w_err}")
                else:
                    if st.button("💾 Set Balance", type="primary", use_container_width=True, key="btn_set_exact_bal"):
                        try:
                            updated_cust = update_customer_wallet(int(auth_user["customer_id"]), float(w_input_val))
                            st.session_state["auth_user"] = {
                                "role": "customer",
                                **updated_cust,
                            }
                            st.session_state["action_notice"] = f"💾 Wallet balance updated to ₹{float(updated_cust['wallet_balance']):,.2f}!"
                            st.rerun()
                        except Exception as w_err:
                            st.error(f"Failed to update balance: {w_err}")

        st.markdown("---")

        # Edit Profile Form
        st.markdown("#### ✏️ Edit Profile Details")
        with st.form("edit_profile_form"):
            edit_name = st.text_input("Full Name", value=auth_user.get("name", ""))
            edit_email_display = st.text_input("Email Address (Primary Identity)", value=auth_user.get("email", ""), disabled=True, help="Email is your unique identifier.")
            edit_phone = st.text_input("Mobile Phone Number", value=auth_user.get("phone", ""))
            edit_address = st.text_area("Delivery Address", value=auth_user.get("delivery_address", ""))
            
            st.markdown("##### 🔒 Change Password (Optional)")
            new_pwd = st.text_input("New Password", type="password", placeholder="Leave blank if you do not wish to change password")
            new_pwd_confirm = st.text_input("Confirm New Password", type="password", placeholder="Re-type new password")

            save_btn = st.form_submit_button("💾 Save Profile Changes", type="primary", use_container_width=True)

            if save_btn:
                if not edit_name.strip() or not edit_phone.strip() or not edit_address.strip():
                    st.error("Name, Phone, and Delivery Address cannot be empty.")
                elif new_pwd and new_pwd != new_pwd_confirm:
                    st.error("New passwords do not match.")
                elif new_pwd and len(new_pwd) < 4:
                    st.error("New password should be at least 4 characters long.")
                else:
                    try:
                        updated_user = update_customer_profile(
                            customer_id=int(auth_user["customer_id"]),
                            name=edit_name,
                            phone=edit_phone,
                            address=edit_address,
                            new_password=new_pwd if new_pwd else None,
                        )
                        st.session_state["auth_user"] = {
                            "role": "customer",
                            **updated_user,
                        }
                        st.session_state["action_notice"] = "✅ Profile updated successfully!"
                        st.rerun()
                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception as err:
                        st.error(f"Failed to update profile: {err}")

        st.markdown("---")

        # Danger Zone: Delete Account
        st.markdown("#### ⚠️ Danger Zone — Deactivate Account")
        st.markdown(
            """
            <div class="danger-box">
                <h4 style="margin:0 0 6px 0; color:#9F1239;">Deactivate & Delete My Account Profile</h4>
                <p style="margin:0; color:#881337; font-size:0.88rem; line-height:1.5;">
                    Deleting your account will set your profile status to <code>is_active = FALSE</code> and immediately sign you out.
                    Your past order records and SLA violation audits are safely preserved in the database to guarantee 3NF relational integrity.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("🗑️ Proceed with Account Deletion", expanded=False):
            confirm_del = st.checkbox(f"Yes, I understand that deleting my account ({auth_user['name']}) will revoke all access.", key="chk_confirm_delete")
            if st.button("🚨 Confirm & Delete My Account", type="primary", disabled=not confirm_del, use_container_width=True):
                try:
                    success = delete_customer_account(int(auth_user["customer_id"]))
                    if success:
                        st.session_state["auth_user"] = None
                        if "confirmed_order" in st.session_state:
                            del st.session_state["confirmed_order"]
                        st.session_state["delete_notice"] = "✅ Your FlashCart account has been successfully deactivated."
                        st.rerun()
                    else:
                        st.error("Account deletion failed.")
                except Exception as err:
                    st.error(f"Error during account deletion: {err}")


# ----------------------------------------------------
# B. DARK STORE MANAGER / ADMIN EXPERIENCE
# ----------------------------------------------------
elif is_admin:
    tab_dispatch, tab_inventory, tab_analytics, tab_dbms_inspector = st.tabs([
        "🛵 Hub Dispatch & SLA Engine",
        "📦 Inventory & Restock Management",
        "📊 Operations Analytics & Audit",
        "🔍 Live DBMS Table Inspector",
    ])

    with tab_dispatch:
        if selected_store is None:
            st.warning("👈 Please select an Active Dark Store from the sidebar.")
        else:
            current_store_id = int(selected_store["store_id"])
            hub_name = selected_store["hub_name"]

            # Prominent SLA breach success banner
            if "sla_breach_banner" in st.session_state:
                breach_alert = st.session_state["sla_breach_banner"]
                st.markdown(
                    f"""
                    <div style="background: linear-gradient(135deg, #064E3B 0%, #047857 100%); 
                                border: 2px solid #34D399; border-radius: 12px; padding: 20px 24px; 
                                margin-bottom: 20px; color: #FFFFFF; box-shadow: 0 8px 25px rgba(16, 185, 129, 0.3);">
                        <div style="display: flex; align-items: flex-start; justify-content: space-between;">
                            <div style="flex: 1;">
                                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                                    <span style="background: #10B981; color: white; padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.5px;">MYSQL TRIGGER FIRED</span>
                                    <span style="color: #A7F3D0; font-size: 0.85rem; font-weight: 600;">⚡ trg_check_sla_breach_and_refund</span>
                                </div>
                                <h2 style="margin: 0 0 8px 0; color: #FFFFFF; font-size: 1.35rem; font-weight: 800; letter-spacing: -0.3px;">
                                    SLA Breach Detected by MySQL Trigger! ₹50 compensation automatically credited to customer wallet.
                                </h2>
                                <p style="margin: 0; color: #D1FAE5; font-size: 0.95rem; line-height: 1.5;">
                                    Order <strong>#FC-{breach_alert.get('order_id')}</strong> for <strong>{breach_alert.get('customer_name')}</strong> was delivered past the promised SLA deadline (5-minute simulated delay). 
                                    MySQL Trigger <code>trg_check_sla_breach_and_refund</code> automatically logged an entry into <code>sla_violations</code> 
                                    and credited <strong>₹50.00</strong> to the customer's wallet balance. 
                                    Trigger <code>trg_sync_rider_status_on_order_update</code> also automatically restored the delivery rider's duty status to <strong>IDLE</strong>.
                                </p>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("✕ Dismiss Notification", key="dismiss_sla_breach_btn", use_container_width=False):
                    del st.session_state["sla_breach_banner"]
                    st.rerun()

            try:
                active_orders_df = load_active_orders_for_store(current_store_id)
            except Exception as e:
                st.error(f"Error loading active orders: {e}")
                active_orders_df = pd.DataFrame()

            try:
                idle_riders_df = load_idle_riders_for_store(current_store_id)
            except Exception as e:
                st.error(f"Error loading idle riders: {e}")
                idle_riders_df = pd.DataFrame()

            try:
                all_riders_df = load_all_riders_for_store(current_store_id)
            except Exception as e:
                all_riders_df = pd.DataFrame()

            try:
                sla_violations_df = load_sla_violations()
            except Exception as e:
                st.error(f"Error loading SLA violations: {e}")
                sla_violations_df = pd.DataFrame()

            # Metrics row
            total_active = len(active_orders_df)
            dispatched_count = len(active_orders_df[active_orders_df["order_status"] == "DISPATCHED"]) if not active_orders_df.empty else 0
            placed_count = len(active_orders_df[active_orders_df["order_status"] == "PLACED"]) if not active_orders_df.empty else 0
            idle_count = len(idle_riders_df)
            total_violations = len(sla_violations_df)

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Active Hub Orders", total_active, help="Orders not DELIVERED or CANCELLED")
            m2.metric("Orders Dispatched", dispatched_count, help="In transit with delivering riders")
            m3.metric("Pending Dispatch", placed_count, help="Orders awaiting rider assignment")
            m4.metric("Idle Riders Available", idle_count, help="Riders with duty_status = IDLE at this hub")
            m5.metric("Total SLA Breaches", total_violations, help="Total violations logged across system")

            st.markdown("---")

            # Active Orders Display
            st.subheader(f"📦 Active Orders Queue — {hub_name}")
            st.caption("Orders requiring rider dispatch or delivery completion at this dark store hub.")

            if active_orders_df.empty:
                st.info(f"ℹ️ No active orders found for **{hub_name}**. Customers can log in and place new 10-minute orders.")
            else:
                display_active_df = active_orders_df.copy()
                display_active_df["Order ID"] = display_active_df["order_id"].apply(lambda oid: f"#FC-{oid}")
                display_active_df["Customer"] = display_active_df["customer_name"]
                display_active_df["Phone"] = display_active_df["customer_phone"]
                display_active_df["Status"] = display_active_df["order_status"]
                
                display_active_df["Ordered At"] = pd.to_datetime(display_active_df["order_placed_at"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")
                display_active_df["Promised By"] = pd.to_datetime(display_active_df["promised_by"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")
                
                if "actual_delivered_at" in display_active_df.columns:
                    display_active_df["Delivered At"] = pd.to_datetime(display_active_df["actual_delivered_at"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("In Transit / Pending")
                else:
                    display_active_df["Delivered At"] = "In Transit / Pending"

                display_active_df["Assigned Rider"] = display_active_df["rider_name"].fillna("Unassigned")
                display_active_df["Rider Duty Status"] = display_active_df["rider_status"].fillna("N/A")

                table_cols = [
                    "Order ID",
                    "Customer",
                    "Phone",
                    "Status",
                    "Ordered At",
                    "Promised By",
                    "Delivered At",
                    "Assigned Rider",
                    "Rider Duty Status",
                ]

                st.dataframe(
                    display_active_df[table_cols],
                    use_container_width=True,
                    hide_index=True,
                )

                st.markdown("#### ⚡ Order Dispatch & Delivery Action Controls")
                st.caption("Assign idle riders and test real-time MySQL trigger automation (rider status sync & SLA breach refunds).")

                for _, order_row in active_orders_df.iterrows():
                    oid = int(order_row["order_id"])
                    cust_name = order_row["customer_name"]
                    cust_phone = order_row["customer_phone"]
                    ord_at = str(order_row["order_placed_at"]) if pd.notna(order_row["order_placed_at"]) else "N/A"
                    prom_by = str(order_row["promised_by"]) if pd.notna(order_row["promised_by"]) else "N/A"
                    curr_status = order_row["order_status"]
                    r_id = order_row["rider_id"]
                    r_name = order_row["rider_name"]

                    status_color = "#38BDF8" if curr_status == "DISPATCHED" else "#F59E0B"
                    status_label = "🛵 DISPATCHED (IN TRANSIT)" if curr_status == "DISPATCHED" else "🟡 PLACED (PENDING DISPATCH)"

                    with st.container(border=True):
                        head_col, status_col = st.columns([3, 1])
                        with head_col:
                            st.markdown(f"### Order #FC-{oid} — {cust_name}")
                        with status_col:
                            st.markdown(
                                f"""<div style="text-align:right;">
                                    <span style="background:{status_color}; color:#0F172A; padding:4px 10px; border-radius:12px; font-size:0.8rem; font-weight:700;">
                                        {status_label}
                                    </span>
                                </div>""",
                                unsafe_allow_html=True,
                            )

                        c_details1, c_details2, c_details3, c_details4 = st.columns(4)
                        c_details1.markdown(f"**Order ID:** `#FC-{oid}`")
                        c_details2.markdown(f"**Customer:** {cust_name} ({cust_phone})")
                        c_details3.markdown(f"**Ordered At:** `{ord_at}`")
                        c_details4.markdown(f"**Promised By:** `{prom_by}`")

                        st.markdown("<hr style='margin: 8px 0 12px 0; border-color: #334155;'/>", unsafe_allow_html=True)

                        if curr_status != "DISPATCHED":
                            col_rider_sel, col_dispatch_btn = st.columns([3, 1])

                            with col_rider_sel:
                                if idle_riders_df.empty:
                                    st.warning("⚠️ No riders with `duty_status = 'IDLE'` currently available at this hub.")
                                    chosen_rider_id = None
                                else:
                                    rider_options = {
                                        int(r["rider_id"]): f"{r['name']} (ID: {r['rider_id']}, Phone: {r['phone']}) — Status: {r['duty_status']}"
                                        for _, r in idle_riders_df.iterrows()
                                    }
                                    default_idx = 0
                                    if r_id and not pd.isna(r_id) and int(r_id) in rider_options:
                                        default_idx = list(rider_options.keys()).index(int(r_id))

                                    chosen_rider_id = st.selectbox(
                                        "Rider Assignment (duty_status = 'IDLE')",
                                        options=list(rider_options.keys()),
                                        format_func=lambda rid: rider_options[rid],
                                        index=default_idx,
                                        key=f"rider_select_{oid}",
                                    )

                            with col_dispatch_btn:
                                st.write("")
                                st.write("")
                                dispatch_btn = st.button(
                                    "🛵 Dispatch Order",
                                    key=f"btn_dispatch_{oid}",
                                    type="primary",
                                    use_container_width=True,
                                    disabled=chosen_rider_id is None,
                                )

                                if dispatch_btn and chosen_rider_id is not None:
                                    try:
                                        dispatch_order(oid, chosen_rider_id)
                                        rider_label = rider_options.get(chosen_rider_id, f"Rider #{chosen_rider_id}")
                                        st.session_state["action_notice"] = (
                                            f"🛵 Order #FC-{oid} successfully dispatched to {rider_label}! "
                                            f"MySQL Trigger 'trg_sync_rider_status_on_order_update' automatically updated rider duty_status to 'DELIVERING'."
                                        )
                                        st.rerun()
                                    except Exception as err:
                                        st.error(f"Failed to dispatch order: {err}")

                        else:
                            rider_display = f"{r_name} (ID: #{int(r_id)})" if r_name and not pd.isna(r_id) else "Assigned Rider"
                            st.info(f"🛵 **Assigned Rider:** {rider_display} | **Duty Status:** `DELIVERING` (automatically set by trigger)")

                            st.markdown("**Viva Highlight Feature (Delivery Completion Simulation):**")
                            col_deliver_a, col_deliver_b = st.columns(2)

                            with col_deliver_a:
                                btn_a = st.button(
                                    "✅ Deliver Now (On-Time)",
                                    key=f"btn_ontime_{oid}",
                                    use_container_width=True,
                                    help="Sets order_status = 'DELIVERED' and actual_delivered_at = NOW(). Trigger restores rider to IDLE.",
                                )
                                if btn_a:
                                    try:
                                        deliver_order_ontime(oid)
                                        st.session_state["action_notice"] = (
                                            f"✅ Order #FC-{oid} delivered ON-TIME! "
                                            f"MySQL Trigger 'trg_sync_rider_status_on_order_update' automatically set rider duty_status back to 'IDLE'."
                                        )
                                        st.rerun()
                                    except Exception as err:
                                        st.error(f"Failed to mark delivered: {err}")

                            with col_deliver_b:
                                btn_b = st.button(
                                    "🚨 Simulate SLA Breach (Late Delivery)",
                                    key=f"btn_breach_{oid}",
                                    type="primary",
                                    use_container_width=True,
                                    help="Sets order_status = 'DELIVERED' and actual_delivered_at = DATE_ADD(promised_by, INTERVAL 5 MINUTE). Triggers SLA violation log & ₹50 refund!",
                                )
                                if btn_b:
                                    try:
                                        deliver_order_sla_breach(oid)
                                        st.session_state["sla_breach_banner"] = {
                                            "order_id": oid,
                                            "customer_name": cust_name,
                                        }
                                        st.rerun()
                                    except Exception as err:
                                        st.error(f"Failed to simulate SLA breach: {err}")

            st.markdown("---")

            # SLA Violations Table
            st.subheader("📋 Live SLA Violations Audit Table")
            st.caption("Real-time table of `sla_violations` joined with `orders` and `customers`. Populated automatically by MySQL trigger `trg_check_sla_breach_and_refund`.")

            if sla_violations_df.empty:
                st.info("ℹ️ No SLA violations recorded yet. Click **'Simulate SLA Breach (Late Delivery)'** on any dispatched order above to simulate an SLA breach and view the trigger in action!")
            else:
                v_display = sla_violations_df.copy()
                v_display["Violation ID"] = v_display["violation_id"].apply(lambda vid: f"#VIO-{vid}")
                v_display["Order ID"] = v_display["order_id"].apply(lambda oid: f"#FC-{oid}")
                v_display["Customer"] = v_display["customer_name"]
                v_display["Phone"] = v_display["customer_phone"]
                v_display["Dark Store"] = v_display["store_hub"].fillna("N/A")
                v_display["Order Placed At"] = pd.to_datetime(v_display["order_placed_at"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")
                v_display["Promised By"] = pd.to_datetime(v_display["promised_by"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")
                v_display["Delivered At"] = pd.to_datetime(v_display["actual_delivered_at"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")
                v_display["Promised (Mins)"] = v_display["promised_mins"].fillna(0).astype(int)
                v_display["Actual Taken (Mins)"] = v_display["actual_mins_taken"].fillna(0).astype(int)
                v_display["Delay (Mins)"] = v_display["delay_minutes"].fillna(0).astype(int)
                v_display["Compensation Refund"] = v_display["refund_amount"].apply(lambda val: float(val) if val is not None else 0.0)
                v_display["Trigger Logged At"] = pd.to_datetime(v_display["logged_at"]).dt.strftime("%Y-%m-%d %H:%M:%S").fillna("N/A")

                v_cols = [
                    "Violation ID",
                    "Order ID",
                    "Customer",
                    "Phone",
                    "Dark Store",
                    "Order Placed At",
                    "Promised By",
                    "Delivered At",
                    "Promised (Mins)",
                    "Actual Taken (Mins)",
                    "Delay (Mins)",
                    "Compensation Refund",
                    "Trigger Logged At",
                ]

                st.dataframe(
                    v_display[v_cols],
                    column_config={
                        "Compensation Refund": st.column_config.NumberColumn("Compensation", format="₹%.2f"),
                        "Delay (Mins)": st.column_config.NumberColumn("Delay", format="%d mins"),
                        "Promised (Mins)": st.column_config.NumberColumn("Promised", format="%d mins"),
                        "Actual Taken (Mins)": st.column_config.NumberColumn("Actual Taken", format="%d mins"),
                    },
                    use_container_width=True,
                    hide_index=True,
                )

            # Rider Fleet Status Expander
            with st.expander(f"🛵 Dark Store Rider Fleet Live Status — {hub_name} (Monitor Trigger Transitions)", expanded=False):
                st.caption("Watch rider `duty_status` flip from `IDLE` ➔ `DELIVERING` on dispatch, and back to `IDLE` on delivery completion via MySQL trigger `trg_sync_rider_status_on_order_update`.")
                if all_riders_df.empty:
                    st.info("No riders registered for this store.")
                else:
                    rf_display = all_riders_df.copy()
                    rf_display["Status Indicator"] = rf_display["duty_status"].apply(
                        lambda s: "🟢 IDLE (AVAILABLE)" if s == "IDLE" else "🛵 DELIVERING (BUSY)"
                    )
                    st.dataframe(
                        rf_display[["rider_id", "name", "phone", "duty_status", "Status Indicator"]],
                        use_container_width=True,
                        hide_index=True,
                    )

    # ----------------------------------------------------
    # ----------------------------------------------------
    # TAB 2: INVENTORY & RESTOCK MANAGEMENT (CRUD)
    # ----------------------------------------------------
    with tab_inventory:
        if selected_store is None:
            st.warning("👈 Please select an Active Dark Store from the sidebar.")
        else:
            current_store_id = int(selected_store["store_id"])
            hub_name = selected_store["hub_name"]

            st.markdown(f"### 📦 Dark Store Inventory & Catalog Management — {hub_name}")
            st.caption(
                "Full CRUD management suite: browse FEFO batches, create new products and inbound shipments, "
                "update pricing/stock counts, and execute safe deletions."
            )

            if "inventory_last_notice" in st.session_state:
                st.success(f"✅ {st.session_state['inventory_last_notice']}")
                del st.session_state["inventory_last_notice"]
            if "inventory_error_notice" in st.session_state:
                st.warning(f"⚠️ {st.session_state['inventory_error_notice']}")
                del st.session_state["inventory_error_notice"]

            try:
                batches_df = load_store_batches_all(current_store_id)
            except Exception as e:
                st.error(f"Error loading inventory batches: {e}")
                batches_df = pd.DataFrame()

            inv_sub1, inv_sub2, inv_sub3, inv_sub4 = st.tabs([
                "📋 View Inventory & FEFO Batches",
                "➕ Add Product / Intake Batch",
                "✏️ Edit Product Details & Stock",
                "🗑️ Delete / Archive Items",
            ])

            # ----------------------------------------------------
            # SUB-TAB 1: READ (Inventory & Batches)
            # ----------------------------------------------------
            with inv_sub1:
                if batches_df.empty:
                    st.info(f"ℹ️ No inventory batches found for **{hub_name}**. Use the **'➕ Add Product / Intake Batch'** tab above to add batches.")
                else:
                    # 1. Summary Metrics
                    total_batches = len(batches_df)
                    total_units = int(batches_df["stock_qty"].sum())
                    depleted_batches = int((batches_df["stock_qty"] == 0).sum())
                    in_stock_batches = total_batches - depleted_batches
                    critical_batches = int(((batches_df["days_to_expiry"] <= 2) & (batches_df["is_perishable"] == 1)).sum())

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Batches Tracked", total_batches, help="Total SKU batches registered at this hub")
                    m2.metric("Total Stock On Hand", f"{total_units:,} units", help="Combined physical unit inventory")
                    m3.metric(
                        "Out of Stock (Depleted)",
                        depleted_batches,
                        delta=f"{depleted_batches} Out of Stock" if depleted_batches > 0 else "All In Stock",
                        delta_color="inverse" if depleted_batches > 0 else "normal",
                        help="Batches with stock_qty = 0 requiring restocking",
                    )
                    m4.metric(
                        "Critical Perishables (≤ 2d)",
                        critical_batches,
                        delta=f"{critical_batches} Urgent" if critical_batches > 0 else "0",
                        delta_color="inverse" if critical_batches > 0 else "normal",
                        help="Perishable batches expiring within 2 days",
                    )

                    st.markdown("---")

                    col_filter_cat, col_search_name = st.columns([1, 2])
                    with col_filter_cat:
                        all_batch_cats = ["All Categories"] + sorted(batches_df["category"].dropna().unique().tolist())
                        filter_cat = st.selectbox("Filter by Category", all_batch_cats, key="fefo_filter_category")
                    with col_search_name:
                        search_term = st.text_input("🔍 Search Product Name / Batch ID", "", key="fefo_search_name", placeholder="e.g. Milk, Bread, 1")

                    filtered_batches = batches_df.copy()
                    if filter_cat != "All Categories":
                        filtered_batches = filtered_batches[filtered_batches["category"] == filter_cat]
                    if search_term.strip():
                        term = search_term.strip().lower()
                        filtered_batches = filtered_batches[
                            filtered_batches["product_name"].str.lower().str.contains(term) |
                            filtered_batches["batch_id"].astype(str).str.contains(term)
                        ]

                    st.markdown(f"#### 📋 All Batches at {hub_name} ({len(filtered_batches)} items)")
                    st.caption("Batches are automatically ordered by `expiry_date ASC` to enforce First-Expired, First-Out logistics.")

                    if filtered_batches.empty:
                        st.info("No batches match the applied filters.")
                    else:
                        display_batches = filtered_batches.copy()
                        display_batches["Batch ID"] = display_batches["batch_id"].apply(lambda bid: f"#BATCH-{bid}")
                        display_batches["Product Name"] = display_batches["product_name"]
                        display_batches["Category"] = display_batches["category"]
                        display_batches["Unit Price"] = display_batches["unit_price"].apply(lambda p: f"₹{float(p):,.2f}")
                        display_batches["Stock On Hand"] = display_batches["stock_qty"].astype(int)
                        display_batches["Stock Status"] = display_batches["stock_qty"].apply(
                            lambda q: "🔴 Out of Stock (0 units)" if q == 0 else f"🟢 In Stock ({q})"
                        )
                        display_batches["Mfg Date"] = pd.to_datetime(display_batches["mfg_date"]).dt.strftime("%Y-%m-%d").fillna("N/A")
                        display_batches["Expiry Date"] = pd.to_datetime(display_batches["expiry_date"]).dt.strftime("%Y-%m-%d").fillna("N/A")
                        display_batches["Days Remaining"] = display_batches["days_to_expiry"].astype(int)

                        def get_fefo_badge(row):
                            if not row["is_perishable"]:
                                return "⚪ Shelf-Stable (Non-Perishable)"
                            days = int(row["days_to_expiry"])
                            if days < 0:
                                return f"🔴 Expired ({abs(days)}d ago)"
                            elif days == 0:
                                return "⚠️ Expires Today"
                            elif days <= 2:
                                return f"🚨 Critical ({days}d left)"
                            elif days <= 5:
                                return f"⚠️ Warning ({days}d left)"
                            else:
                                return f"🟢 Fresh ({days}d left)"

                        display_batches["FEFO Status"] = display_batches.apply(get_fefo_badge, axis=1)

                        table_batch_cols = [
                            "Batch ID",
                            "Product Name",
                            "Category",
                            "Unit Price",
                            "Stock On Hand",
                            "Stock Status",
                            "Mfg Date",
                            "Expiry Date",
                            "Days Remaining",
                            "FEFO Status",
                        ]

                        st.dataframe(
                            display_batches[table_batch_cols],
                            column_config={
                                "Stock On Hand": st.column_config.NumberColumn("Physical Stock", format="%d units"),
                                "Days Remaining": st.column_config.NumberColumn("Days Left", format="%d days"),
                                "Stock Status": st.column_config.TextColumn("Availability Status"),
                                "FEFO Status": st.column_config.TextColumn("FEFO Shelf-Life Priority"),
                            },
                            use_container_width=True,
                            hide_index=True,
                        )

                with st.expander("📚 View Master Product Catalog (All Products across categories)"):
                    try:
                        all_prods_df = load_all_products()
                        if not all_prods_df.empty:
                            all_prods_display = all_prods_df.copy()
                            all_prods_display["Product ID"] = all_prods_display["product_id"].apply(lambda pid: f"SKU-{pid:03d}")
                            all_prods_display["Unit Price"] = all_prods_display["unit_price"].apply(lambda p: f"₹{float(p):,.2f}")
                            all_prods_display["Type"] = all_prods_display["is_perishable"].apply(lambda isp: "🥬 Perishable (FEFO)" if isp else "📦 Shelf-Stable")
                            st.dataframe(
                                all_prods_display[["Product ID", "name", "category", "Unit Price", "Type"]].rename(columns={"name": "Product Name", "category": "Category"}),
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.info("No master products registered.")
                    except Exception as err:
                        st.error(f"Error loading master products: {err}")

            # ----------------------------------------------------
            # SUB-TAB 2: CREATE (Product & Batch)
            # ----------------------------------------------------
            with inv_sub2:
                st.markdown("#### ➕ Add New Items (Create)")
                st.caption("Register a brand-new SKU into the master product catalog, or intake a fresh supplier batch for this dark store hub.")

                create_mode = st.radio(
                    "Creation Mode",
                    [
                        "🏷️ Create New Master Product (Catalog)",
                        "📦 Intake New Batch (Hub Inventory)",
                    ],
                    horizontal=True,
                    key="inv_create_mode_radio",
                )

                if "Create New Master Product" in create_mode:
                    with st.form("form_create_product", border=True):
                        st.markdown("##### 🏷️ New Master Product Details")
                        new_p_name = st.text_input("Product Name", placeholder="e.g. Organic Almond Butter 250g")

                        try:
                            existing_cats = load_all_categories()
                        except Exception:
                            existing_cats = ["Dairy & Eggs", "Bakery", "Beverages", "Snacks", "Fruits & Veggies"]
                        
                        cat_options = existing_cats + ["➕ Add New Category..."]
                        selected_cat_opt = st.selectbox("Category", cat_options, key="create_prod_cat_sel")

                        if selected_cat_opt == "➕ Add New Category...":
                            custom_cat = st.text_input("Enter New Category Name", placeholder="e.g. Health & Wellness")
                            final_category = custom_cat.strip()
                        else:
                            final_category = selected_cat_opt

                        col_price, col_perish = st.columns([1, 1])
                        with col_price:
                            new_p_price = st.number_input("Unit Price (₹)", min_value=1.0, value=50.0, step=5.0, format="%.2f")
                        with col_perish:
                            st.write("")
                            st.write("")
                            new_p_perish = st.checkbox("Perishable Item (Enforces FEFO shelf-life tracking)", value=False)

                        st.markdown("---")
                        st.markdown(f"###### 📦 Initial Dark Store Stock Intake ({hub_name})")
                        intake_initial_batch = st.checkbox(
                            f"Immediately add stock batch to **{hub_name}** (makes item ready to order in Storefront)",
                            value=True,
                            help="In FlashCart's relational schema, customers order batches from fulfillment hubs. Checking this immediately registers warehouse stock.",
                            key="create_prod_intake_checkbox",
                        )
                        col_init_qty, col_init_exp = st.columns(2)
                        with col_init_qty:
                            init_stock_qty = st.number_input("Initial Received Stock Units", min_value=1, max_value=10000, value=50, step=10, key="create_prod_init_qty")
                        with col_init_exp:
                            init_expiry_date = st.date_input("Batch Expiry Date", value=date.today() + timedelta(days=14 if new_p_perish else 90), key="create_prod_init_exp")

                        submit_new_prod = st.form_submit_button("✨ Create Master Product & Stock Batch", type="primary", use_container_width=True)
                        if submit_new_prod:
                            if not new_p_name.strip():
                                st.error("Please enter a valid product name.")
                            elif not final_category:
                                st.error("Please specify a valid category.")
                            else:
                                try:
                                    res = create_product(
                                        name=new_p_name.strip(),
                                        category=final_category,
                                        unit_price=float(new_p_price),
                                        is_perishable=new_p_perish,
                                    )
                                    if intake_initial_batch and res.get("product_id"):
                                        batch_res = create_batch(
                                            store_id=current_store_id,
                                            product_id=res["product_id"],
                                            mfg_date=str(date.today()),
                                            expiry_date=str(init_expiry_date),
                                            stock_qty=int(init_stock_qty),
                                        )
                                        st.session_state["inventory_last_notice"] = (
                                            f"✨ Created product '{new_p_name.strip()}' AND registered Batch #{batch_res.get('batch_id')} "
                                            f"({init_stock_qty} units) at {hub_name}! It is now available to order in the Storefront."
                                        )
                                    else:
                                        st.session_state["inventory_last_notice"] = (
                                            f"✨ {res['message']} (Note: Physical batch not intaked; switch to 'Intake New Batch' to add warehouse stock)."
                                        )
                                    st.rerun()
                                except Exception as err:
                                    st.error(f"Failed to create product: {err}")

                else:  # Intake New Batch
                    try:
                        all_prods = load_all_products()
                    except Exception as e:
                        all_prods = pd.DataFrame()

                    if all_prods.empty:
                        st.warning("⚠️ No master products exist yet. Please create a master product first using the option above.")
                    else:
                        with st.form("form_create_batch", border=True):
                            st.markdown(f"##### 📦 Intake Batch for Hub: {hub_name}")
                            prod_choices = {
                                int(r["product_id"]): f"{r['name']} ({r['category']} • ₹{float(r['unit_price']):,.2f})"
                                for _, r in all_prods.iterrows()
                            }
                            chosen_pid = st.selectbox(
                                "Select Product to Intake",
                                options=list(prod_choices.keys()),
                                format_func=lambda pid: prod_choices[pid],
                                key="intake_batch_prod_sel",
                            )

                            col_mfg, col_exp = st.columns(2)
                            with col_mfg:
                                mfg_val = st.date_input("Manufacturing Date", value=date.today())
                            with col_exp:
                                exp_val = st.date_input("Expiry Date", value=date.today() + timedelta(days=30))

                            col_qty, col_store_info = st.columns(2)
                            with col_qty:
                                intake_qty = st.number_input("Initial Received Stock Units", min_value=1, max_value=10000, value=50, step=10)
                            with col_store_info:
                                st.text_input("Receiving Hub", value=f"{hub_name} (ID: {current_store_id})", disabled=True)

                            submit_new_batch = st.form_submit_button("📥 Receive & Register Batch", type="primary", use_container_width=True)
                            if submit_new_batch:
                                if exp_val <= mfg_val:
                                    st.error("Expiry date must be after manufacturing date.")
                                else:
                                    try:
                                        res = create_batch(
                                            store_id=current_store_id,
                                            product_id=chosen_pid,
                                            mfg_date=str(mfg_val),
                                            expiry_date=str(exp_val),
                                            stock_qty=int(intake_qty),
                                        )
                                        st.session_state["inventory_last_notice"] = res["message"]
                                        st.rerun()
                                    except Exception as err:
                                        st.error(f"Failed to create batch: {err}")

            # ----------------------------------------------------
            # SUB-TAB 3: UPDATE (Product Details & Stock)
            # ----------------------------------------------------
            with inv_sub3:
                st.markdown("#### ✏️ Update Item & Inventory (Update)")
                st.caption("Update product master specifications (price, naming, category) or adjust physical batch stock counts.")

                edit_mode = st.radio(
                    "Update Target",
                    [
                        "⚙️ Adjust Physical Batch Stock Count",
                        "🏷️ Edit Product Master Details (Price / Name / Category)",
                    ],
                    horizontal=True,
                    key="inv_edit_mode_radio",
                )

                if "Adjust Physical Batch Stock" in edit_mode:
                    if batches_df.empty:
                        st.info(f"No batches available at {hub_name} to adjust.")
                    else:
                        batch_map = {}
                        for _, b_row in batches_df.iterrows():
                            bid = int(b_row["batch_id"])
                            pname = b_row["product_name"]
                            c_stock = int(b_row["stock_qty"])
                            exp_date = str(b_row["expiry_date"])[:10]
                            days_exp = int(b_row["days_to_expiry"])
                            status_text = "🔴 OUT OF STOCK (0 units)" if c_stock == 0 else f"{c_stock} units"
                            perish_tag = f"• {days_exp}d left" if b_row["is_perishable"] else "• Shelf-stable"
                            label = f"[#BATCH-{bid}] {pname} ({status_text} • Exp: {exp_date} {perish_tag})"
                            batch_map[bid] = {
                                "row": b_row,
                                "label": label,
                                "product_name": pname,
                                "stock_qty": c_stock,
                                "expiry_date": exp_date,
                            }

                        with st.container(border=True):
                            col_b_sel, col_mode_sel = st.columns([3, 2])

                            with col_b_sel:
                                selected_b_id = st.selectbox(
                                    "Select Inventory Batch",
                                    options=list(batch_map.keys()),
                                    format_func=lambda bid: batch_map[bid]["label"],
                                    key="restock_batch_selector",
                                )

                            with col_mode_sel:
                                restock_mode = st.radio(
                                    "Action Type",
                                    [
                                        "➕ Add Units (Inbound Restock)",
                                        "➖ Deduct Units (Spoilage / Write-Off)",
                                        "⚙️ Set Exact Stock (Cycle Count Audit)",
                                    ],
                                    horizontal=False,
                                    key="restock_action_mode",
                                )

                            chosen_batch = batch_map[selected_b_id]
                            current_stock_val = chosen_batch["stock_qty"]

                            with st.form(f"inventory_restock_form_{selected_b_id}"):
                                col_curr, col_input, col_proj = st.columns(3)

                                with col_curr:
                                    st.metric("Current Physical Stock", f"{current_stock_val} units")

                                if "➕ Add Units" in restock_mode:
                                    with col_input:
                                        added_units = st.number_input(
                                            "Number of Units to Add (+)",
                                            min_value=1,
                                            max_value=5000,
                                            value=10,
                                            step=5,
                                            help="Positive quantity of newly received stock units to add to this batch.",
                                            key=f"in_add_units_{selected_b_id}",
                                        )
                                    with col_proj:
                                        projected_stock = current_stock_val + added_units
                                        st.metric("New Projected Stock", f"{projected_stock} units", delta=f"+{added_units} units")

                                elif "➖ Deduct Units" in restock_mode:
                                    with col_input:
                                        if current_stock_val == 0:
                                            st.warning("⚠️ Batch is at 0 units. Cannot deduct further.")
                                            deducted_units = 0
                                        else:
                                            deducted_units = st.number_input(
                                                "Number of Units to Remove (-)",
                                                min_value=1,
                                                max_value=int(current_stock_val),
                                                value=min(1, current_stock_val),
                                                step=1,
                                                help="Quantity of damaged or expired units to remove.",
                                                key=f"in_ded_units_{selected_b_id}",
                                            )
                                    with col_proj:
                                        projected_stock = max(0, current_stock_val - (deducted_units if current_stock_val > 0 else 0))
                                        st.metric("New Projected Stock", f"{projected_stock} units", delta=f"-{deducted_units} units" if current_stock_val > 0 else "0 units")

                                else:  # ⚙️ Set Exact Stock
                                    with col_input:
                                        exact_units = st.number_input(
                                            "New Exact Physical Count",
                                            min_value=0,
                                            max_value=10000,
                                            value=int(current_stock_val),
                                            step=1,
                                            help="Exact physical count determined during inventory cycle count.",
                                            key=f"in_exact_units_{selected_b_id}",
                                        )
                                    with col_proj:
                                        delta_val = exact_units - current_stock_val
                                        st.metric("New Confirmed Stock", f"{exact_units} units", delta=f"{delta_val:+d} units")

                                submit_stock_btn = st.form_submit_button(
                                    "💾 Confirm & Apply Stock Update",
                                    type="primary",
                                    use_container_width=True,
                                )

                                if submit_stock_btn:
                                    try:
                                        with st.spinner("Applying inventory update to database..."):
                                            if "➕ Add Units" in restock_mode:
                                                res = restock_batch(selected_b_id, int(added_units))
                                            elif "➖ Deduct Units" in restock_mode:
                                                if current_stock_val == 0:
                                                    st.error("Cannot deduct from an empty batch.")
                                                    res = None
                                                else:
                                                    res = deduct_batch_stock(selected_b_id, int(deducted_units))
                                            else:
                                                res = update_batch_stock(selected_b_id, int(exact_units))

                                        if res is not None:
                                            st.session_state["inventory_last_notice"] = res["message"]
                                            st.session_state["action_notice"] = res["message"]
                                            st.rerun()
                                    except ValueError as ve:
                                        st.error(str(ve))
                                    except MySQLError as db_err:
                                        st.error(f"Database error during stock update: {db_err}")
                                    except Exception as err:
                                        st.error(f"Failed to update batch stock: {err}")

                else:  # Edit Product Master Details
                    try:
                        all_prods = load_all_products()
                    except Exception as e:
                        all_prods = pd.DataFrame()

                    if all_prods.empty:
                        st.info("No master products found to edit.")
                    else:
                        prod_dict = {
                            int(r["product_id"]): r
                            for _, r in all_prods.iterrows()
                        }
                        chosen_edit_pid = st.selectbox(
                            "Select Product to Edit",
                            options=list(prod_dict.keys()),
                            format_func=lambda pid: f"SKU-{pid:03d}: {prod_dict[pid]['name']} ({prod_dict[pid]['category']} • ₹{float(prod_dict[pid]['unit_price']):,.2f})",
                            key="edit_prod_selector",
                        )
                        target_prod = prod_dict[chosen_edit_pid]

                        with st.form(f"form_edit_product_{chosen_edit_pid}", border=True):
                            st.markdown(f"##### ✏️ Edit Specifications for: **{target_prod['name']}**")
                            edit_p_name = st.text_input("Product Name", value=target_prod["name"])

                            try:
                                all_cats = load_all_categories()
                            except Exception:
                                all_cats = ["Dairy & Eggs", "Bakery", "Beverages", "Snacks", "Fruits & Veggies"]

                            curr_cat = target_prod["category"]
                            if curr_cat not in all_cats:
                                all_cats.append(curr_cat)
                            edit_cat_options = all_cats + ["➕ Add New Category..."]
                            default_cat_idx = all_cats.index(curr_cat) if curr_cat in all_cats else 0

                            selected_edit_cat_opt = st.selectbox(
                                "Category",
                                edit_cat_options,
                                index=default_cat_idx,
                                key=f"edit_cat_sel_{chosen_edit_pid}",
                            )
                            if selected_edit_cat_opt == "➕ Add New Category...":
                                edit_custom_cat = st.text_input("Enter New Category Name", key=f"edit_custom_cat_{chosen_edit_pid}")
                                final_edit_cat = edit_custom_cat.strip()
                            else:
                                final_edit_cat = selected_edit_cat_opt

                            col_eprice, col_eperish = st.columns([1, 1])
                            with col_eprice:
                                edit_p_price = st.number_input(
                                    "Unit Price (₹)",
                                    min_value=0.0,
                                    value=float(target_prod["unit_price"]),
                                    step=1.0,
                                    format="%.2f",
                                    key=f"edit_price_{chosen_edit_pid}",
                                )
                            with col_eperish:
                                st.write("")
                                st.write("")
                                edit_p_perish = st.checkbox(
                                    "Perishable Item (FEFO tracked)",
                                    value=bool(target_prod["is_perishable"]),
                                    key=f"edit_perish_{chosen_edit_pid}",
                                )

                            submit_edit_prod = st.form_submit_button("💾 Save Product Changes", type="primary", use_container_width=True)
                            if submit_edit_prod:
                                if not edit_p_name.strip():
                                    st.error("Product name cannot be empty.")
                                elif not final_edit_cat:
                                    st.error("Category cannot be empty.")
                                else:
                                    try:
                                        res = update_product(
                                            product_id=chosen_edit_pid,
                                            name=edit_p_name.strip(),
                                            category=final_edit_cat,
                                            unit_price=float(edit_p_price),
                                            is_perishable=edit_p_perish,
                                        )
                                        st.session_state["inventory_last_notice"] = res["message"]
                                        st.rerun()
                                    except Exception as err:
                                        st.error(f"Failed to update product: {err}")

            # ----------------------------------------------------
            # SUB-TAB 4: DELETE / ARCHIVE
            # ----------------------------------------------------
            with inv_sub4:
                st.markdown("#### 🗑️ Remove / Archive Items (Delete)")
                st.caption("Safely remove inventory batches or catalog products. Includes smart relational safeguards to protect order history.")

                del_mode = st.radio(
                    "Deletion Target",
                    [
                        "📦 Delete Inventory Batch (Hub)",
                        "🏷️ Delete Master Product (Catalog)",
                    ],
                    horizontal=True,
                    key="inv_delete_mode_radio",
                )

                if "Delete Inventory Batch" in del_mode:
                    if batches_df.empty:
                        st.info(f"No batches available at {hub_name} to delete.")
                    else:
                        del_batch_map = {
                            int(r["batch_id"]): f"#BATCH-{r['batch_id']}: {r['product_name']} ({r['stock_qty']} units • Exp: {str(r['expiry_date'])[:10]})"
                            for _, r in batches_df.iterrows()
                        }
                        chosen_del_bid = st.selectbox(
                            "Select Inventory Batch to Remove",
                            options=list(del_batch_map.keys()),
                            format_func=lambda bid: del_batch_map[bid],
                            key="del_batch_selector",
                        )

                        with st.container(border=True):
                            st.warning(
                                "🛡️ **Smart Deletion Safeguard:**\n\n"
                                "- If this batch has **never been ordered**, it will be **permanently deleted** from the database.\n"
                                "- If it is linked to **past customer orders**, it will be **safely deactivated** (stock set to 0) to protect customer purchase history."
                            )
                            confirm_del_batch = st.checkbox(
                                f"I understand and confirm that I want to remove Batch #{chosen_del_bid}.",
                                key=f"confirm_del_b_{chosen_del_bid}",
                            )
                            btn_del_b = st.button("🗑️ Delete Batch", type="primary", disabled=not confirm_del_batch)
                            if btn_del_b:
                                try:
                                    res = delete_batch(chosen_del_bid)
                                    if res["status"] == "success":
                                        st.session_state["inventory_last_notice"] = res["message"]
                                    else:
                                        st.session_state["inventory_error_notice"] = res["message"]
                                    st.rerun()
                                except Exception as err:
                                    st.error(f"Error removing batch: {err}")

                else:  # Delete Master Product
                    try:
                        all_prods = load_all_products()
                    except Exception as e:
                        all_prods = pd.DataFrame()

                    if all_prods.empty:
                        st.info("No master products to delete.")
                    else:
                        del_prod_map = {
                            int(r["product_id"]): f"SKU-{r['product_id']:03d}: {r['name']} ({r['category']} • ₹{float(r['unit_price']):,.2f})"
                            for _, r in all_prods.iterrows()
                        }
                        chosen_del_pid = st.selectbox(
                            "Select Master Product to Remove",
                            options=list(del_prod_map.keys()),
                            format_func=lambda pid: del_prod_map[pid],
                            key="del_prod_selector",
                        )

                        with st.container(border=True):
                            st.warning(
                                "🛡️ **Smart Catalog Deletion:**\n\n"
                                "- If this product has **zero sales history**, it and its unused batches will be **permanently removed**.\n"
                                "- If it has **past orders**, its active inventory batches will be **deactivated and zeroed out**, preventing further sales while preserving transaction logs."
                            )
                            confirm_del_prod = st.checkbox(
                                f"I confirm that I want to remove Product #{chosen_del_pid}.",
                                key=f"confirm_del_p_{chosen_del_pid}",
                            )
                            btn_del_p = st.button("🗑️ Delete Product", type="primary", disabled=not confirm_del_prod)
                            if btn_del_p:
                                try:
                                    res = delete_product(chosen_del_pid)
                                    if res["status"] == "success":
                                        st.session_state["inventory_last_notice"] = res["message"]
                                    else:
                                        st.session_state["inventory_error_notice"] = res["message"]
                                    st.rerun()
                                except Exception as err:
                                    st.error(f"Error removing product: {err}")

    with tab_analytics:
        st.markdown("### 📊 Operations Analytics & Temporal SQL Reporting")
        st.caption("Live decision support engine powered by analytical queries, FEFO inventory tracking, and SLA auditing.")

        scope_options = {"ALL": "🌐 All Dark Stores (Global Aggregation)"}
        if not stores_df.empty:
            for _, s_row in stores_df.iterrows():
                scope_options[int(s_row["store_id"])] = f"🏬 {s_row['hub_name']} (PIN: {s_row['pincode_zone']})"

        selected_scope = st.selectbox(
            "Analytics Scope / Fulfillment Hub Filter",
            options=list(scope_options.keys()),
            format_func=lambda k: scope_options[k],
            key="analytics_scope_selector",
        )
        scope_store_id = None if selected_scope == "ALL" else int(selected_scope)

        st.markdown("---")

        # 1. FEFO Inventory Watchlist
        st.subheader("🥬 1. Perishable FEFO (First-Expired, First-Out) Inventory Watchlist")
        st.caption(
            "Real-time perishable batches sorted by `DATEDIFF(expiry_date, CURDATE()) ASC`. "
            "Critical batches (≤ 2 days to expiry) are visually flagged to prevent food waste."
        )

        try:
            fefo_df = load_fefo_perishables(scope_store_id)
        except Exception as e:
            st.error(f"Error fetching FEFO inventory: {e}")
            fefo_df = pd.DataFrame()

        if fefo_df.empty:
            st.info("ℹ️ No perishable stock batches currently registered in active inventory for this scope.")
        else:
            def get_fefo_risk_tag(days: Any) -> str:
                if days is None or pd.isna(days):
                    return "⚪ Unknown"
                d = int(days)
                if d < 0:
                    return f"🔴 Expired ({abs(d)}d ago)"
                elif d == 0:
                    return "🚨 Expires Today"
                elif d <= 2:
                    return f"🚨 Critical ({d}d left)"
                elif d <= 5:
                    return f"⚠️ Warning ({d}d left)"
                else:
                    return f"🟢 Fresh ({d}d left)"

            fefo_display = fefo_df.copy()
            fefo_display["days_to_expiry"] = fefo_display["days_to_expiry"].fillna(9999).astype(int)
            fefo_display["Risk Status"] = fefo_display["days_to_expiry"].apply(get_fefo_risk_tag)
            fefo_display["Batch"] = fefo_display["batch_id"].apply(lambda bid: f"#BATCH-{bid}")
            fefo_display["Product"] = fefo_display["product_name"]
            fefo_display["Category"] = fefo_display["category"]
            fefo_display["Hub"] = fefo_display["hub_name"]
            fefo_display["Stock Available"] = fefo_display["stock_qty"].astype(int)
            fefo_display["Mfg Date"] = pd.to_datetime(fefo_display["mfg_date"]).dt.strftime("%Y-%m-%d").fillna("N/A")
            fefo_display["Expiry Date"] = pd.to_datetime(fefo_display["expiry_date"]).dt.strftime("%Y-%m-%d").fillna("N/A")
            fefo_display["Days Left"] = fefo_display["days_to_expiry"]

            critical_mask = fefo_display["days_to_expiry"] <= 2
            critical_count = int(critical_mask.sum())
            critical_units = int(fefo_display.loc[critical_mask, "stock_qty"].sum()) if critical_count > 0 else 0
            total_batches = len(fefo_display)
            total_perishable_units = int(fefo_display["stock_qty"].sum())

            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Perishable Batches Tracked", total_batches)
            f2.metric("Critical Batches (≤ 2 Days)", critical_count, delta=f"{critical_count} Urgent" if critical_count > 0 else "0", delta_color="inverse")
            f3.metric("Critical Stock at Risk", f"{critical_units} units")
            f4.metric("Total Perishable Units", f"{total_perishable_units} units")

            if critical_count > 0:
                st.markdown(
                    f"""
                    <div style="background:#FFF1F2; border-left: 4px solid #E11D48; border-radius: 6px; padding: 10px 16px; margin: 10px 0 16px 0;">
                        <span style="color: #9F1239; font-weight: 700; font-size: 0.95rem;">🚨 FEFO Priority Alert:</span>
                        <span style="color: #881337; font-size: 0.9rem;">
                            <strong>{critical_count} batch(es)</strong> ({critical_units} total units) expire within <strong>≤ 2 days</strong>. 
                            FEFO dispatch protocol requires dispatching these batches immediately before fresher batches.
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            fefo_cols = [
                "Batch",
                "Product",
                "Category",
                "Hub",
                "Stock Available",
                "Mfg Date",
                "Expiry Date",
                "Days Left",
                "Risk Status",
            ]

            st.dataframe(
                fefo_display[fefo_cols],
                column_config={
                    "Stock Available": st.column_config.NumberColumn("Stock", format="%d units"),
                    "Days Left": st.column_config.NumberColumn("Days Remaining", format="%d days"),
                    "Risk Status": st.column_config.TextColumn("FEFO Priority / Risk Tag"),
                },
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("---")

        # 2. Hub SLA Performance & Penalty Audit
        st.subheader("⏱️ 2. Hub SLA Performance & Penalty Audit")
        st.caption(
            "Aggregated temporal performance metrics calculating average delivery duration (`AVG(TIMESTAMPDIFF(MINUTE, ...))`), "
            "total order volume, SLA breaches, and financial compensation refunded per hub."
        )

        try:
            hub_perf_df = load_hub_sla_performance(scope_store_id)
        except Exception as e:
            st.error(f"Error fetching Hub SLA Performance: {e}")
            hub_perf_df = pd.DataFrame()

        if hub_perf_df.empty:
            st.info("ℹ️ No hub operational data available for the selected scope.")
        else:
            agg_total_orders = int(hub_perf_df["total_orders"].sum())
            agg_delivered = int(hub_perf_df["delivered_orders"].sum())
            agg_breaches = int(hub_perf_df["total_breaches"].sum())
            agg_penalties = float(hub_perf_df["total_penalty_refunded"].astype(float).sum())
            
            if agg_delivered > 0:
                avg_duration = float((hub_perf_df["avg_delivery_mins"].astype(float) * hub_perf_df["delivered_orders"]).sum() / agg_delivered)
            else:
                avg_duration = 0.0

            compliance_pct = ((agg_delivered - agg_breaches) / agg_delivered * 100.0) if agg_delivered > 0 else 100.0

            p1, p2, p3, p4, p5 = st.columns(5)
            p1.metric("Total Orders Placed", agg_total_orders)
            p2.metric(
                "Avg Delivery Duration",
                f"{avg_duration:.1f} mins",
                delta="Target: ≤10 min" if avg_duration <= 10 else f"+{avg_duration-10:.1f}m over target",
                delta_color="normal" if avg_duration <= 10 else "inverse",
            )
            p3.metric("Total SLA Breaches", agg_breaches)
            p4.metric("Total Penalties Refunded", f"₹{agg_penalties:,.2f}")
            p5.metric("SLA Compliance Rate", f"{compliance_pct:.1f}%")

            hub_display = hub_perf_df.copy()
            hub_display["Dark Store Hub"] = hub_display["hub_name"]
            hub_display["Pincode Zone"] = hub_display["pincode_zone"]
            hub_display["Orders Placed"] = hub_display["total_orders"].astype(int)
            hub_display["Orders Delivered"] = hub_display["delivered_orders"].astype(int)
            hub_display["Avg Delivery Duration"] = hub_display["avg_delivery_mins"].apply(lambda v: float(v) if v is not None else 0.0)
            hub_display["SLA Breaches"] = hub_display["total_breaches"].astype(int)
            hub_display["Penalties Refunded (₹)"] = hub_display["total_penalty_refunded"].apply(lambda v: float(v) if v is not None else 0.0)
            hub_display["Compliance Rate"] = hub_display.apply(
                lambda row: (
                    round(((float(row["delivered_orders"]) - float(row["total_breaches"])) / float(row["delivered_orders"]) * 100.0), 1)
                    if row["delivered_orders"] > 0
                    else 100.0
                ),
                axis=1,
            )

            st.markdown("##### 🏬 Fulfillment Hub Audit Breakdown")
            hub_cols = [
                "Dark Store Hub",
                "Pincode Zone",
                "Orders Placed",
                "Orders Delivered",
                "Avg Delivery Duration",
                "SLA Breaches",
                "Penalties Refunded (₹)",
                "Compliance Rate",
            ]

            st.dataframe(
                hub_display[hub_cols],
                column_config={
                    "Avg Delivery Duration": st.column_config.NumberColumn("Avg Delivery Time", format="%.1f mins"),
                    "Penalties Refunded (₹)": st.column_config.NumberColumn("Penalties Refunded", format="₹%.2f"),
                    "Compliance Rate": st.column_config.NumberColumn("Compliance %", format="%.1f%%"),
                    "Orders Placed": st.column_config.NumberColumn("Orders Placed", format="%d"),
                    "Orders Delivered": st.column_config.NumberColumn("Orders Delivered", format="%d"),
                    "SLA Breaches": st.column_config.NumberColumn("SLA Breaches", format="%d"),
                },
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("---")

        # 3. Peak Rush Traffic Chart
        st.subheader("📈 3. Order Volume by Hour Window (Peak Rush Traffic)")
        st.caption(
            "Hourly order density analysis using subquery grouping on `HOUR(order_placed_at)`. "
            "Identifies peak demand windows for rider capacity planning."
        )

        try:
            hourly_df = load_hourly_order_volume(scope_store_id)
        except Exception as e:
            st.error(f"Error fetching hourly volume: {e}")
            hourly_df = pd.DataFrame()

        hours_template = pd.DataFrame({"hour_window": list(range(24))})
        if not hourly_df.empty:
            merged_chart = hours_template.merge(hourly_df, on="hour_window", how="left").fillna(0)
        else:
            merged_chart = hours_template.copy()
            merged_chart["order_volume"] = 0

        merged_chart["order_volume"] = merged_chart["order_volume"].astype(int)
        merged_chart["Hour Window"] = merged_chart["hour_window"].apply(lambda h: f"{h:02d}:00")
        merged_chart["Hour Range"] = merged_chart["hour_window"].apply(lambda h: f"{h:02d}:00 - {h:02d}:59")

        max_vol = int(merged_chart["order_volume"].max())
        if max_vol > 0:
            peak_row = merged_chart[merged_chart["order_volume"] == max_vol].iloc[0]
            peak_hour_str = str(peak_row["Hour Range"])
        else:
            peak_hour_str = "No orders recorded yet"

        h1, h2, h3 = st.columns(3)
        h1.metric("Peak Rush Hour Window", peak_hour_str)
        h2.metric("Peak Hourly Volume", f"{max_vol} order(s)" if max_vol > 0 else "0 orders")
        h3.metric("Total Tracked Orders in Chart", int(merged_chart["order_volume"].sum()))

        chart_data = merged_chart.set_index("Hour Window")[["order_volume"]]
        chart_data.rename(columns={"order_volume": "Orders Placed"}, inplace=True)

        st.bar_chart(chart_data, use_container_width=True)

        with st.expander("🔍 View Raw Hourly Order Density Data Table", expanded=False):
            st.dataframe(
                merged_chart[["Hour Range", "order_volume"]].rename(columns={"order_volume": "Order Volume"}),
                column_config={
                    "Order Volume": st.column_config.NumberColumn("Order Volume", format="%d orders"),
                },
                use_container_width=True,
                hide_index=True,
            )

    # ----------------------------------------------------
    # 4. Live DBMS Relational Inspector & Trigger Monitor
    # ----------------------------------------------------
    with tab_dbms_inspector:
        st.subheader("🔍 Live DBMS Relational Inspector & Trigger Monitor")
        st.caption("Real-time inspection of MySQL 8.0 base tables, relational invariants, and autonomous event triggers.")

        # 1. High-level metric summary
        try:
            summary_df = load_all_tables_summary()
            total_tables = len(summary_df)
            total_rows = int(summary_df["Live Row Count"].sum())
        except Exception as err:
            st.error(f"Error loading tables summary: {err}")
            summary_df = pd.DataFrame()
            total_tables = 8
            total_rows = 0

        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Total 3NF Tables", total_tables)
        m_col2.metric("Total Relational Rows", f"{total_rows:,}")
        m_col3.metric("Autonomous MySQL Triggers", "3 Active")
        m_col4.metric("Storage Engine", "InnoDB (ACID)")

        st.markdown("<hr style='margin: 12px 0 16px 0; border-color: #334155;'/>", unsafe_allow_html=True)

        # 2. Interactive Raw Table Viewer
        st.markdown("#### 📋 Interactive Table Data Browser")
        st.caption("Select any database table or view to inspect its live records directly from MySQL.")

        table_options = [
            "customers",
            "orders",
            "order_items",
            "inventory_batches",
            "sla_violations",
            "delivery_riders",
            "dark_stores",
            "products",
            "vw_live_operations_summary",
        ]
        table_labels = {
            "customers": "customers (User Accounts, Delivery Addresses, Wallets)",
            "orders": "orders (Order Transactions, Status & 12-Min SLA Timestamps)",
            "order_items": "order_items (Order Line-Items Linked to Batches)",
            "inventory_batches": "inventory_batches (FEFO Stock Lots & Expiry Dates)",
            "sla_violations": "sla_violations (Immutable Audit Log of Breaches & Refunds)",
            "delivery_riders": "delivery_riders (Fleet Personnel & Live Duty Status)",
            "dark_stores": "dark_stores (Regional Fulfillment Micro-Hubs)",
            "products": "products (Master Catalog SKUs & Perishable Flags)",
            "vw_live_operations_summary": "vw_live_operations_summary (SQL View: Real-Time Hub Operations)",
        }

        sel_col1, sel_col2 = st.columns([3, 1])
        with sel_col1:
            chosen_tbl = st.selectbox(
                "Select Database Table or View to Inspect",
                options=table_options,
                format_func=lambda t: table_labels.get(t, t),
                key="dbms_inspector_table_select",
            )
        with sel_col2:
            limit_rows = st.selectbox(
                "Row Limit",
                options=[25, 50, 100, 200],
                index=1,
                key="dbms_inspector_limit_select",
            )

        try:
            raw_df = load_raw_table_data(chosen_tbl, limit=limit_rows)
            if raw_df.empty:
                st.info(f"ℹ️ Table **{chosen_tbl}** is currently empty (0 rows in MySQL).")
            else:
                st.caption(f"Showing latest **{len(raw_df)}** row(s) from **`{chosen_tbl}`** (live from MySQL):")
                st.dataframe(raw_df, use_container_width=True, hide_index=True)
        except Exception as err:
            st.error(f"Error reading table {chosen_tbl}: {err}")

        st.markdown("<hr style='margin: 16px 0; border-color: #334155;'/>", unsafe_allow_html=True)

        # 3. Complete Relational Schema & Trigger Mapping Overview
        with st.expander("📊 Complete 3NF Schema & Attached Triggers Mapping", expanded=False):
            if not summary_df.empty:
                st.dataframe(summary_df, use_container_width=True, hide_index=True)

        # 4. Trigger Execution Monitor Cards
        st.markdown("#### ⚡ Autonomous MySQL Triggers Proof Monitor")
        t_col1, t_col2, t_col3 = st.columns(3)

        with t_col1:
            with st.container(border=True):
                st.markdown("##### 1. Stock Depletion")
                st.caption("`trg_deduct_stock_after_order_item`")
                st.markdown("• **Event:** `AFTER INSERT ON order_items`\n• **Action:** Decrements batch stock quantity immediately.\n• **Status:** 🟢 Active & Enforced")

        with t_col2:
            with st.container(border=True):
                st.markdown("##### 2. Fleet State Machine")
                st.caption("`trg_sync_rider_status_on_order_update`")
                st.markdown("• **Event:** `AFTER UPDATE ON orders`\n• **Action:** Synchronizes rider `IDLE` ↔ `DELIVERING`.\n• **Status:** 🟢 Active & Enforced")

        with t_col3:
            with st.container(border=True):
                st.markdown("##### 3. SLA Breach Compensation")
                st.caption("`trg_check_sla_breach_and_refund`")
                st.markdown("• **Event:** `AFTER UPDATE ON orders`\n• **Action:** Logs late delivery and auto-credits ₹50 to wallet.\n• **Status:** 🟢 Active & Enforced")

        # 5. One-Click Viva Demo Reset in Inspector
        st.markdown("<hr style='margin: 16px 0; border-color: #334155;'/>", unsafe_allow_html=True)
        r_col1, r_col2 = st.columns([3, 1])
        with r_col1:
            st.markdown("**Viva Demonstration Reset Utility:**")
            st.caption("Clears all test orders and SLA violations, resets customer wallets to seed values, and restores full batch stock.")
        with r_col2:
            if st.button("🔄 Reset Demo State", key="inspector_reset_demo_btn", type="primary", use_container_width=True):
                res = reset_demo_data()
                if res.get("status") == "success":
                    st.session_state["action_notice"] = "⚡ Database restored to clean viva demo state!"
                    st.rerun()
                else:
                    st.error(f"Reset failed: {res.get('error')}")
