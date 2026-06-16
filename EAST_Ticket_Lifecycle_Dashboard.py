import streamlit as st
import pandas as pd
import requests
import math
import numpy as np

# =============================
# CONFIG (PASTE PASSWORD HERE)
# =============================
USERNAME = "github_servicenow_api"
PASSWORD = "wL<c&sLHGso(mH3mIRs=byF5C%97o>P3z[K+QZSD"   # ✅ PASTE YOUR PASSWORD HERE
INSTANCE_URL = "https://progress1.service-now.com"

EAST_TEAM = [
    "Balaji Manikanta Sai Sadhu", "Digvijay Pawar", "Manognasri Chitrala",
    "Rachana Adiga", "Saranmai Mandarapu", "Sreeja Janumpally",
    "Raleigh Turner", "Christina Sunil", "Chris Braga", "Chris braga"
]

EAST_GROUPS = [
    "IT Supp: EAST - Delivery",
    "IT Supp: EAST - Leads",
    "IT Supp: System Access Requests"
]

NOT_UPDATED_DAYS = 2

# =============================
# Chris Weighted Model
# =============================
WEIGHTS = {
    "age": 0.25,
    "inactivity": 0.20,
    "priority": 0.20,
    "reassign": 0.15,
    "skill": 0.10,
    "requester": 0.10
}

AGE_LOOKUP = [
    (0, 1),
    (3, 2),
    (7, 4),
    (14, 6),
    (30, 8),
    (60, 9),
    (90, 10)
]

INACTIVITY_LOOKUP = [
    (0, 1),
    (2, 3),
    (5, 5),
    (10, 7),
    (20, 9),
    (30, 10)
]

REASSIGN_LOOKUP = [
    (0, 1),
    (1, 3),
    (2, 5),
    (4, 7),
    (6, 9),
    (8, 10)
]

PRIORITY_LOOKUP = {
    "Low": 3,
    "Medium": 6,
    "High": 9,
    "Critical": 10
}

SKILL_LOOKUP = {
    "Strong Match": 1,
    "Good": 5,
    "Mismatch": 9,
    "Severe Mismatch": 10
}

REQUESTER_LOOKUP = {
    "Individual": 3,
    "Manager": 5,
    "Director": 8,
    "VP+": 10
}

def risk_level(score_0_10: float) -> str:
    if score_0_10 <= 4.0:
        return "Low"
    elif score_0_10 <= 7.0:
        return "Medium"
    return "High"

def intervention_label_from_weighted(score_0_10: float) -> str:
    if score_0_10 >= 7.1:
        return "🔴 Immediate"
    if score_0_10 >= 6.0:
        return "🟠 High Risk"
    if score_0_10 >= 4.1:
        return "🟡 Watch"
    return "✅ Normal"

# ✅ FIXED: this now uses renamed column "Final Score"
def highlight_rows_weighted(row):
    s = float(row["Final Score"])
    if s >= 7.1:
        return ["background-color:#cc0000;color:white"] * len(row)
    if s >= 6.0:
        return ["background-color:#ff704d"] * len(row)
    if s >= 4.1:
        return ["background-color:#fff2cc"] * len(row)
    return [""] * len(row)

# =============================
# UI
# =============================
st.set_page_config(layout="wide")
st.title("🎯 EAST Ticket Lifecycle Dashboard ✅")
st.caption(f"Last Refresh: {pd.Timestamp.now().strftime('%d %b %Y, %I:%M %p')}")

top_col1, top_col2, top_col3 = st.columns([3, 1, 1])
with top_col1:
    max_to_load = st.selectbox("Max tickets to load", [500, 1000, 2000, 5000, 10000], index=3)
with top_col2:
    show_team_only = st.checkbox("Show only EAST team-owned tickets", value=False)
with top_col3:
    if st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()

# =============================
# HELPERS
# =============================
def clean_display(x):
    if isinstance(x, dict):
        return x.get("display_value", "") or x.get("value", "") or ""
    if x is None:
        return ""
    return str(x)

def map_priority(p):
    p = str(p).lower()
    if p.startswith("1") or "critical" in p:
        return "Critical"
    if p.startswith("2") or "high" in p:
        return "High"
    if p.startswith("3") or "moderate" in p or "medium" in p:
        return "Medium"
    if p.startswith("4") or "low" in p:
        return "Low"
    return "Other"

def level_from_group(g):
    g = str(g)
    return "L2" if "Leads" in g else "L1"

def age_bucket(days):
    if pd.isna(days):
        return "Unknown"
    d = int(days)
    if d <= 5:
        return "NEW"
    if d <= 14:
        return "AGING"
    if d <= 30:
        return "STALE"
    return "OLD"

def score_from_lookup(value: float, lookup_list):
    value = float(value)
    best = lookup_list[0][1]
    for min_v, sc in lookup_list:
        if value >= float(min_v):
            best = sc
        else:
            break
    return int(best)

def normalize_skill(x: str) -> str:
    x = str(x).strip()
    if x in SKILL_LOOKUP:
        return x
    low = x.lower()
    if "severe" in low:
        return "Severe Mismatch"
    if "mismatch" in low:
        return "Mismatch"
    if "strong" in low:
        return "Strong Match"
    return "Good"

def normalize_requester_role(x: str) -> str:
    x = str(x).strip()
    if x in REQUESTER_LOOKUP:
        return x
    low = x.lower()
    if "vp" in low:
        return "VP+"
    if "director" in low:
        return "Director"
    if "manager" in low:
        return "Manager"
    return "Individual"

# ✅ Business days only (Mon–Fri). Holidays are NOT excluded in this version.
def business_days_between(start_ts, end_ts):
    if pd.isna(start_ts) or pd.isna(end_ts):
        return 0.0
    start_date = pd.Timestamp(start_ts).date()
    end_date = pd.Timestamp(end_ts).date()
    if end_date <= start_date:
        return 0.0
    return float(np.busday_count(start_date, end_date))

def plain_english_reason(row):
    reasons = []

    if row["ticket_age_days"] >= 90:
        reasons.append("very old ticket")
    elif row["ticket_age_days"] >= 30:
        reasons.append("older backlog item")
    elif row["ticket_age_days"] >= 14:
        reasons.append("aging ticket")

    if row["inactivity_days"] >= 30:
        reasons.append("has not been updated for a long time")
    elif row["inactivity_days"] >= 10:
        reasons.append("has been inactive for many days")
    elif row["inactivity_days"] >= 5:
        reasons.append("has not been updated recently")

    if row["priority"] == "Critical":
        reasons.append("critical priority")
    elif row["priority"] == "High":
        reasons.append("high priority")

    if row["reassignment_count"] >= 6:
        reasons.append("has bounced between queues many times")
    elif row["reassignment_count"] >= 2:
        reasons.append("has been reassigned multiple times")

    if row["skill_alignment"] == "Severe Mismatch":
        reasons.append("appears assigned to the wrong skill group")
    elif row["skill_alignment"] == "Mismatch":
        reasons.append("may not match the current owner’s skill area")

    if row["requester_impact"] in ["Director", "VP+"]:
        reasons.append("has higher business visibility")
    elif row["requester_impact"] == "Manager":
        reasons.append("has manager-level requester visibility")

    if not reasons:
        return "No major risk signals; standard queue handling."

    return ", ".join(reasons).capitalize() + "."

# =============================
# LOAD DATA
# =============================
@st.cache_data(show_spinner=True)
def load_table(table_name: str, query: str, fields: str, max_rows: int) -> pd.DataFrame:
    url = f"{INSTANCE_URL}/api/now/table/{table_name}"
    page_size = 2000
    pages = max(1, math.ceil(max_rows / page_size))
    all_rows = []

    for i in range(pages):
        offset = i * page_size
        params = {
            "sysparm_query": query,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": str(min(page_size, max_rows - offset)),
            "sysparm_offset": str(offset),
            "sysparm_fields": fields
        }

        r = requests.get(
            url,
            params=params,
            auth=(USERNAME, PASSWORD),
            headers={"Accept": "application/json"},
            timeout=60
        )

        if r.status_code >= 400:
            try:
                err = r.json()
            except Exception:
                err = r.text
            raise RuntimeError(f"ServiceNow API Error ({table_name}) {r.status_code}: {err}")

        chunk = r.json().get("result", [])
        if not chunk:
            break

        all_rows.extend(chunk)

        if len(chunk) < page_size:
            break

    return pd.DataFrame(all_rows)

# =============================
# PULL RITMS + INCIDENTS
# =============================
ritm_query = (
    "active=true"
    "^assignment_group.nameIN"
    "IT Supp: EAST - Delivery,IT Supp: EAST - Leads,IT Supp: System Access Requests"
)

incident_query = (
    "active=true"
    "^assignment_group.name=IT Supp: EAST - Delivery"
)

common_fields = "number,assignment_group,assigned_to,priority,sys_created_on,sys_updated_on"

df_ritm = load_table("sc_req_item", ritm_query, common_fields, max_to_load)
df_inc = load_table("incident", incident_query, common_fields, max_to_load)

if not df_ritm.empty:
    df_ritm["ticket_type"] = "RITM"
if not df_inc.empty:
    df_inc["ticket_type"] = "INC"

df = pd.concat([df_ritm, df_inc], ignore_index=True)

if df.empty:
    st.warning("No tickets returned. Check credentials, access, or query scope.")
    st.stop()

for col in ["number", "assignment_group", "assigned_to", "priority", "sys_created_on", "sys_updated_on", "ticket_type"]:
    if col not in df.columns:
        df[col] = ""

df["assignment_group"] = df["assignment_group"].apply(clean_display)
df["assigned_to"] = df["assigned_to"].apply(clean_display)
df["priority"] = df["priority"].apply(map_priority)

df["open_date"] = pd.to_datetime(df["sys_created_on"], errors="coerce")
df["updated"] = pd.to_datetime(df["sys_updated_on"], errors="coerce")

# ✅ Business days only
today_ts = pd.Timestamp.now()
df["ticket_age_days"] = df["open_date"].apply(lambda x: business_days_between(x, today_ts)).round(2)
df["inactivity_days"] = df["updated"].apply(lambda x: business_days_between(x, today_ts)).round(2)

df["level"] = df["assignment_group"].apply(level_from_group)
df["age_bucket"] = df["ticket_age_days"].apply(age_bucket)

df_backlog = df.copy()
df_team = df_backlog[df_backlog["assigned_to"].isin(EAST_TEAM)].copy()
df_view_base = df_team if show_team_only else df_backlog

# =============================
# WEIGHTED MODEL SCORING
# =============================
if "reassignment_count" not in df_backlog.columns:
    df_backlog["reassignment_count"] = 0
if "skill_alignment" not in df_backlog.columns:
    df_backlog["skill_alignment"] = "Good"
if "requester_impact" not in df_backlog.columns:
    df_backlog["requester_impact"] = "Individual"

df_backlog["skill_alignment"] = df_backlog["skill_alignment"].apply(normalize_skill)
df_backlog["requester_impact"] = df_backlog["requester_impact"].apply(normalize_requester_role)

df_backlog["age_score_0_10"] = df_backlog["ticket_age_days"].apply(lambda x: score_from_lookup(x, AGE_LOOKUP))
df_backlog["inactivity_score_0_10"] = df_backlog["inactivity_days"].apply(lambda x: score_from_lookup(x, INACTIVITY_LOOKUP))
df_backlog["priority_score_0_10"] = df_backlog["priority"].apply(lambda p: int(PRIORITY_LOOKUP.get(p, 0)))
df_backlog["reassign_score_0_10"] = df_backlog["reassignment_count"].apply(lambda x: score_from_lookup(x, REASSIGN_LOOKUP))
df_backlog["skill_score_0_10"] = df_backlog["skill_alignment"].apply(lambda s: int(SKILL_LOOKUP.get(s, 5)))
df_backlog["requester_score_0_10"] = df_backlog["requester_impact"].apply(lambda r: int(REQUESTER_LOOKUP.get(r, 3)))

df_backlog["age_weighted"] = df_backlog["age_score_0_10"] * WEIGHTS["age"]
df_backlog["inactivity_weighted"] = df_backlog["inactivity_score_0_10"] * WEIGHTS["inactivity"]
df_backlog["priority_weighted"] = df_backlog["priority_score_0_10"] * WEIGHTS["priority"]
df_backlog["reassign_weighted"] = df_backlog["reassign_score_0_10"] * WEIGHTS["reassign"]
df_backlog["skill_weighted"] = df_backlog["skill_score_0_10"] * WEIGHTS["skill"]
df_backlog["requester_weighted"] = df_backlog["requester_score_0_10"] * WEIGHTS["requester"]

df_backlog["final_score"] = (
    df_backlog["age_weighted"]
    + df_backlog["inactivity_weighted"]
    + df_backlog["priority_weighted"]
    + df_backlog["reassign_weighted"]
    + df_backlog["skill_weighted"]
    + df_backlog["requester_weighted"]
).round(2)

df_backlog["risk_level"] = df_backlog["final_score"].apply(risk_level)
df_backlog["intervention"] = df_backlog["final_score"].apply(intervention_label_from_weighted)
df_backlog["why_flagged"] = df_backlog.apply(plain_english_reason, axis=1)

# sort by urgency
df_backlog = df_backlog.sort_values(
    by=["final_score", "inactivity_days", "ticket_age_days"],
    ascending=[False, False, False]
).reset_index(drop=True)

counts = df_backlog["intervention"].value_counts()
immediate_cnt = int(counts.get("🔴 Immediate", 0))
highrisk_cnt = int(counts.get("🟠 High Risk", 0))
watch_cnt = int(counts.get("🟡 Watch", 0))
normal_cnt = int(counts.get("✅ Normal", 0))

# =============================
# OVERVIEW
# =============================
st.subheader("📊 Overview")

total = len(df_view_base)
l1 = len(df_view_base[df_view_base["level"] == "L1"])
l2 = len(df_view_base[df_view_base["level"] == "L2"])
not_updated = len(df_view_base[df_view_base["inactivity_days"] > NOT_UPDATED_DAYS])
avg_time = round(df_view_base["ticket_age_days"].mean(), 2) if total else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Tickets", total)
c2.metric("L1 Tickets", l1)
c3.metric("L2 Tickets", l2)
c4.metric("Not Updated >2 Days", not_updated)
c5.metric("Avg Time Taken (Days)", avg_time)

with st.expander("🔍 Backlog count explanation (why numbers differ)"):
    st.write("Backlog (ALL open RITMs + EAST Delivery incidents):", len(df_backlog))
    st.write("Team-owned (Assigned to EAST team only):", len(df_team))
    st.write("Unassigned or assigned outside EAST team:", len(df_backlog) - len(df_team))
    st.caption("Ticket age and inactivity are now calculated using business days (Mon–Fri only). Company holidays are not excluded in this version.")

# =============================
# TABS
# =============================
tab = st.radio(
    "",
    [
        "All Tickets",
        "L1 Tickets",
        "L2 Tickets",
        "Individual Report",
        "Not Updated",
        "Needs Action",
        "Management Intervention"
    ],
    horizontal=True
)

standard_cols = [
    "ticket_type", "number", "level", "assigned_to", "assignment_group", "priority",
    "open_date", "ticket_age_days", "inactivity_days", "age_bucket"
]

# =============================
# TAB VIEWS
# =============================
if tab == "All Tickets":
    st.dataframe(df_view_base[standard_cols], use_container_width=True, hide_index=True)

elif tab == "L1 Tickets":
    d = df_view_base[df_view_base["level"] == "L1"]
    st.dataframe(d[standard_cols], use_container_width=True, hide_index=True)

elif tab == "L2 Tickets":
    d = df_view_base[df_view_base["level"] == "L2"]
    st.dataframe(d[standard_cols], use_container_width=True, hide_index=True)

elif tab == "Not Updated":
    d = df_view_base[df_view_base["inactivity_days"] > NOT_UPDATED_DAYS]
    st.dataframe(d[standard_cols], use_container_width=True, hide_index=True)

elif tab == "Needs Action":
    d = df_view_base[
        (df_view_base["inactivity_days"] > NOT_UPDATED_DAYS) |
        (df_view_base["ticket_age_days"] > 30)
    ]
    st.dataframe(d[standard_cols], use_container_width=True, hide_index=True)

elif tab == "Individual Report":
    st.subheader("👤 Individual Report")

    people = sorted(df_team["assigned_to"].dropna().unique().tolist())
    person = st.selectbox("Select team member", people)

    d = df_team[df_team["assigned_to"] == person].copy()

    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Tickets", len(d))
    i2.metric("Avg time taken (days)", round(d["ticket_age_days"].mean(), 2) if len(d) else 0)
    i3.metric("Not touched >2 days", len(d[d["inactivity_days"] > NOT_UPDATED_DAYS]))
    i4.metric("Old (31+)", len(d[d["ticket_age_days"] > 30]))

    st.subheader("🚨 Needs Attention (>2 Days Not Updated)")
    d_flag = d[d["inactivity_days"] > NOT_UPDATED_DAYS]
    if d_flag.empty:
        st.success("✅ No tickets pending update >2 days")
    else:
        st.dataframe(d_flag[standard_cols], use_container_width=True, hide_index=True)

    st.subheader("📋 All Tickets for Selected Member")
    st.dataframe(d[standard_cols], use_container_width=True, hide_index=True)

else:
    # =============================
    # MANAGEMENT INTERVENTION
    # =============================
    st.subheader("🚨 Management Intervention")
    st.caption("Weighted model using Chris lookups + weights (0–10 score). Includes RITMs + active incidents assigned to EAST Delivery.")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🔴 Immediate", immediate_cnt)
    k2.metric("🟠 High Risk", highrisk_cnt)
    k3.metric("🟡 Watch", watch_cnt)
    k4.metric("✅ Normal", normal_cnt)

    with st.expander("📊 How the Weighted Score is Calculated (Chris Model)", expanded=False):
        st.markdown("**Final Score (0–10) = SUM(Scoreᵢ × Weightᵢ)**")
        st.code(f"""
Weights:
- Ticket Age: {WEIGHTS['age']*100:.0f}%
- Inactivity: {WEIGHTS['inactivity']*100:.0f}%
- Priority: {WEIGHTS['priority']*100:.0f}%
- Reassignment Count: {WEIGHTS['reassign']*100:.0f}%
- Skill Alignment: {WEIGHTS['skill']*100:.0f}%
- Requester Impact: {WEIGHTS['requester']*100:.0f}%

Risk bands:
- 0–4.0 = Low
- 4.1–7.0 = Medium
- 7.1–10.0 = High

Business-day logic:
- Ticket Age and Inactivity exclude weekends (Mon–Fri only)
- Company holidays are not excluded in this version
""")

    # Top 10
    st.subheader("🔥 Top 10 tickets (ranked by weighted score)")
    top10_display = df_backlog[[
        "final_score",
        "number",
        "ticket_type",
        "assigned_to",
        "assignment_group",
        "priority",
        "ticket_age_days",
        "inactivity_days",
        "intervention",
        "why_flagged"
    ]].head(10).copy()

    top10_display.rename(columns={
        "final_score": "Final Score",
        "number": "Ticket",
        "ticket_type": "Type",
        "assigned_to": "Assigned To",
        "assignment_group": "Assignment Group",
        "priority": "Priority",
        "ticket_age_days": "Ticket Age (days)",
        "inactivity_days": "Inactivity (days)",
        "intervention": "Intervention",
        "why_flagged": "Why Flagged"
    }, inplace=True)

    st.dataframe(top10_display, use_container_width=True, hide_index=True)

    # Full table
    st.subheader("📋 All Backlog by weighted intervention score")

    full_display = df_backlog[[
        "final_score",
        "number",
        "ticket_type",
        "assigned_to",
        "assignment_group",
        "priority",
        "ticket_age_days",
        "inactivity_days",
        "intervention",
        "risk_level",
        "why_flagged"
    ]].copy()

    full_display.rename(columns={
        "final_score": "Final Score",
        "number": "Ticket",
        "ticket_type": "Type",
        "assigned_to": "Assigned To",
        "assignment_group": "Assignment Group",
        "priority": "Priority",
        "ticket_age_days": "Ticket Age (days)",
        "inactivity_days": "Inactivity (days)",
        "intervention": "Intervention",
        "risk_level": "Risk Level",
        "why_flagged": "Why Flagged"
    }, inplace=True)

    st.dataframe(
        full_display.style.apply(highlight_rows_weighted, axis=1),
        use_container_width=True,
        hide_index=True
    )

