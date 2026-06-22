import streamlit as st
import pandas as pd
import requests
import numpy as np
from datetime import datetime
from requests.auth import HTTPBasicAuth

# ============================================================
# EAST Ticket Lifecycle Dashboard
# Full version with:
# ✅ RITM + Incident support
# ✅ EAST Leads incidents included
# ✅ Correct L1 / L2 mapping
# ✅ Correct individual backlog counts
# ✅ Direct password placeholder
# ============================================================

st.set_page_config(
    page_title="EAST Ticket Lifecycle Dashboard",
    page_icon="🎯",
    layout="wide"
)

# ============================================================
# SERVICENOW LOGIN CONFIG
# ============================================================
# IMPORTANT:
# Paste your ServiceNow API password/token below.
# Since this GitHub repo is public, do not keep the real password here long term.

USERNAME = "github_servicenow_api"

# ✅ PASTE YOUR PASSWORD / TOKEN BETWEEN THE QUOTES BELOW
PASSWORD = "wL<c&sLHGso(mH3mIRs=byF5C%97o>P3z[K+QZSD"

INSTANCE_URL = "https://progress1.service-now.com"

# ============================================================
# EAST CONFIG
# ============================================================

EAST_TEAM = [
    "Balaji Manikanta Sai Sadhu",
    "Digvijay Pawar",
    "Manognasri Chitrala",
    "Rachana Adiga",
    "Saranmai Mandarapu",
    "Sreeja Janumpally",
    "Raleigh Turner",
    "Christina Sunil",
]

EAST_L1_GROUPS = [
    "IT Supp: EAST - Delivery",
    "IT Supp: System Access Requests",
]

EAST_L2_GROUPS = [
    "IT Supp: EAST - Leads",
]

EAST_GROUPS = EAST_L1_GROUPS + EAST_L2_GROUPS

NOT_UPDATED_DAYS = 2

PRIORITY_ORDER = {
    "1 - Critical": 1,
    "1-Critical": 1,
    "Critical": 1,
    "2 - High": 2,
    "2-High": 2,
    "High": 2,
    "3 - Moderate": 3,
    "3-Moderate": 3,
    "Moderate": 3,
    "Medium": 3,
    "4 - Low": 4,
    "4-Low": 4,
    "Low": 4,
    "5 - Planning": 5,
    "5-Planning": 5,
    "Planning": 5,
    "Other": 6,
    "": 6,
    None: 6,
}

# ============================================================
# BASIC HELPERS
# ============================================================

def safe_display_value(value):
    """
    ServiceNow may return fields as:
    - plain text
    - dict with display_value/value
    - None
    This helper safely returns readable display text.
    """
    if isinstance(value, dict):
        display_value = value.get("display_value")
        raw_value = value.get("value")

        if display_value not in [None, ""]:
            return str(display_value)

        if raw_value not in [None, ""]:
            return str(raw_value)

        return ""

    if value is None:
        return ""

    return str(value)


def safe_raw_value(value):
    """
    Returns raw ServiceNow value where available.
    """
    if isinstance(value, dict):
        raw_value = value.get("value")
        display_value = value.get("display_value")

        if raw_value not in [None, ""]:
            return str(raw_value)

        if display_value not in [None, ""]:
            return str(display_value)

        return ""

    if value is None:
        return ""

    return str(value)


def parse_datetime(value):
    """
    Converts ServiceNow date/time values safely.
    """
    if value is None:
        return pd.NaT

    value = str(value).strip()

    if value == "":
        return pd.NaT

    try:
        return pd.to_datetime(value, errors="coerce")
    except Exception:
        return pd.NaT


def business_days_between(start_dt, end_dt=None):
    """
    Business-day age calculation.
    Includes weekdays only.
    """
    if pd.isna(start_dt):
        return 0

    if end_dt is None or pd.isna(end_dt):
        end_dt = pd.Timestamp.now()

    try:
        start_date = pd.to_datetime(start_dt).date()
        end_date = pd.to_datetime(end_dt).date()
    except Exception:
        return 0

    if end_date < start_date:
        return 0

    try:
        return int(np.busday_count(start_date, end_date + pd.Timedelta(days=1).date()))
    except Exception:
        return 0


def age_bucket(days):
    try:
        days = int(days)
    except Exception:
        return "UNKNOWN"

    if days <= 5:
        return "NEW"
    elif days <= 14:
        return "AGING"
    elif days <= 30:
        return "STALE"
    else:
        return "OLD"


def normalize_priority(priority):
    priority = safe_display_value(priority).strip()

    if priority in PRIORITY_ORDER:
        return priority

    lower = priority.lower()

    if lower.startswith("1") or "critical" in lower:
        return "1 - Critical"
    if lower.startswith("2") or "high" in lower:
        return "2 - High"
    if lower.startswith("3") or "moderate" in lower or "medium" in lower:
        return "3 - Moderate"
    if lower.startswith("4") or "low" in lower:
        return "4 - Low"
    if lower.startswith("5") or "planning" in lower:
        return "5 - Planning"

    if priority:
        return priority

    return "Other"


def priority_rank(priority):
    return PRIORITY_ORDER.get(normalize_priority(priority), 6)


def map_level_from_group(assignment_group):
    group = str(assignment_group).strip()

    if group in EAST_L1_GROUPS:
        return "L1"

    if group in EAST_L2_GROUPS:
        return "L2"

    return "Other"


def is_closed_or_cancelled(state):
    """
    We exclude fully closed/cancelled items.
    We keep Resolved because your report includes resolved incident INC0101731.
    """
    state = str(state).strip().lower()

    if state == "":
        return False

    if state.startswith("closed"):
        return True

    if "cancel" in state:
        return True

    return False


def calc_sla_remaining_days(ticket_age_days):
    """
    EAST backlog target = 5 business days.
    """
    try:
        return 5 - int(ticket_age_days)
    except Exception:
        return 0


def calc_priority_score(row):
    """
    Higher score = needs more attention.
    """
    p_rank = priority_rank(row.get("priority", ""))

    try:
        age = float(row.get("ticket_age_days", 0))
    except Exception:
        age = 0

    try:
        inactivity = float(row.get("inactivity_days", 0))
    except Exception:
        inactivity = 0

    priority_weight = {
        1: 3.0,
        2: 2.5,
        3: 2.0,
        4: 1.2,
        5: 1.0,
        6: 0.5,
    }.get(p_rank, 0.5)

    age_weight = min(age / 10, 4)
    inactivity_weight = min(inactivity / 5, 3)

    return round(priority_weight + age_weight + inactivity_weight, 2)


# ============================================================
# SERVICENOW API
# ============================================================

def get_auth():
    if not USERNAME or not PASSWORD or PASSWORD == "PASTE_YOUR_PASSWORD_HERE":
        return None

    return HTTPBasicAuth(USERNAME, PASSWORD)


def snow_get_records(table_name, encoded_query, fields, max_records=5000):
    """
    Generic ServiceNow Table API pull with pagination.
    """
    auth = get_auth()

    if auth is None:
        st.error(
            "ServiceNow password/token is missing. "
            "Edit the code and paste it into PASSWORD = \"PASTE_YOUR_PASSWORD_HERE\"."
        )
        return []

    url = f"{INSTANCE_URL}/api/now/table/{table_name}"

    all_records = []
    limit = 500
    offset = 0

    while len(all_records) < max_records:
        params = {
            "sysparm_query": encoded_query,
            "sysparm_fields": ",".join(fields),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": limit,
            "sysparm_offset": offset,
        }

        try:
            response = requests.get(
                url,
                params=params,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=60,
            )

            if response.status_code != 200:
                st.error(
                    f"ServiceNow API error for table {table_name}: "
                    f"{response.status_code} - {response.text[:800]}"
                )
                break

            payload = response.json()
            records = payload.get("result", [])

            if not records:
                break

            all_records.extend(records)

            if len(records) < limit:
                break

            offset += limit

        except Exception as e:
            st.error(f"ServiceNow API request failed for {table_name}: {e}")
            break

    return all_records[:max_records]


@st.cache_data(show_spinner=False, ttl=300)
def fetch_east_records(max_records=5000):
    """
    Main data pull.

    Critical fix:
    Pulls from TASK because TASK contains both:
    - sc_req_item / RITM
    - incident / INC

    This prevents incidents under IT Supp: EAST - Leads from being missed.
    """

    fields = [
        "number",
        "sys_class_name",
        "assigned_to",
        "assignment_group",
        "state",
        "priority",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "closed_at",
        "active",
        "short_description",
        "cmdb_ci",
    ]

    records_all = []

    # Pull the exact EAST queues by each class.
    # We intentionally include both sc_req_item and incident.
    for group in EAST_GROUPS:
        for ticket_class in ["sc_req_item", "incident"]:
            query = (
                f"sys_class_name={ticket_class}"
                f"^assignment_group.name={group}"
                f"^ORDERBYDESCsys_updated_on"
            )

            records = snow_get_records(
                table_name="task",
                encoded_query=query,
                fields=fields,
                max_records=max_records,
            )

            records_all.extend(records)

    # Fallback if dot-walk on assignment_group.name does not return anything.
    # This pulls active/latest task records for both classes and filters in pandas.
    if len(records_all) == 0:
        fallback_query = (
            "sys_class_nameINsc_req_item,incident"
            "^ORDERBYDESCsys_updated_on"
        )

        records_all = snow_get_records(
            table_name="task",
            encoded_query=fallback_query,
            fields=fields,
            max_records=max_records,
        )

    rows = []

    for rec in records_all:
        number = safe_display_value(rec.get("number"))
        sys_class_name = safe_raw_value(rec.get("sys_class_name")) or safe_display_value(rec.get("sys_class_name"))

        assigned_to = safe_display_value(rec.get("assigned_to"))
        assignment_group = safe_display_value(rec.get("assignment_group"))
        state = safe_display_value(rec.get("state"))
        priority = normalize_priority(rec.get("priority"))

        opened_raw = safe_display_value(rec.get("opened_at")) or safe_display_value(rec.get("sys_created_on"))
        updated_raw = safe_display_value(rec.get("sys_updated_on"))

        opened_dt = parse_datetime(opened_raw)
        updated_dt = parse_datetime(updated_raw)

        # Keep only EAST assignment groups.
        if assignment_group not in EAST_GROUPS:
            continue

        # Exclude fully closed/cancelled.
        # Keep Resolved because user report includes resolved incidents.
        if is_closed_or_cancelled(state):
            continue

        if number.startswith("INC") or "incident" in str(sys_class_name).lower():
            ticket_type = "INC"
            source = "incident"
        else:
            ticket_type = "RITM"
            source = "sc_req_item"

        level = map_level_from_group(assignment_group)

        ticket_age_days = business_days_between(opened_dt)
        inactivity_days = business_days_between(updated_dt)

        row = {
            "ticket_type": ticket_type,
            "number": number,
            "level": level,
            "assigned_to": assigned_to,
            "assignment_group": assignment_group,
            "priority": priority,
            "state": state,
            "open_date": opened_dt,
            "last_updated": updated_dt,
            "ticket_age_days": ticket_age_days,
            "inactivity_days": inactivity_days,
            "sla_remaining_days": calc_sla_remaining_days(ticket_age_days),
            "age_bucket": age_bucket(ticket_age_days),
            "source": source,
            "short_description": safe_display_value(rec.get("short_description")),
            "configuration_item": safe_display_value(rec.get("cmdb_ci")),
        }

        row["priority_score"] = calc_priority_score(row)

        rows.append(row)

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # Remove duplicate tickets caused by multi-query pull.
    df = df.drop_duplicates(subset=["number"], keep="first")

    df = df.sort_values(
        by=["priority_score", "ticket_age_days", "inactivity_days"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    return df


# ============================================================
# HEADER
# ============================================================

st.title("🎯 EAST Ticket Lifecycle Dashboard ✅")

st.caption(f"Last Refresh: {datetime.now().strftime('%d %b %Y, %I:%M %p')}")

control_col1, control_col2, control_col3 = st.columns([3, 1.3, 1])

with control_col1:
    max_tickets = st.selectbox(
        "Max tickets to load",
        [500, 1000, 2000, 5000],
        index=3,
    )

with control_col2:
    east_team_only = st.checkbox("Show only EAST team-owned tickets", value=False)

with control_col3:
    st.write("")
    st.write("")
    if st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()


# ============================================================
# LOAD DATA
# ============================================================

with st.spinner("Loading EAST RITMs and Incidents from ServiceNow..."):
    df = fetch_east_records(max_records=max_tickets)

if df.empty:
    st.warning(
        "No EAST records returned. Please verify password/token, ServiceNow API access, "
        "and access to task / sc_req_item / incident tables."
    )
    st.stop()

if east_team_only:
    df = df[df["assigned_to"].isin(EAST_TEAM)].copy()


# ============================================================
# OVERVIEW METRICS
# ============================================================

total_tickets = len(df)
l1_tickets = int((df["level"] == "L1").sum())
l2_tickets = int((df["level"] == "L2").sum())
ritm_count = int((df["ticket_type"] == "RITM").sum())
inc_count = int((df["ticket_type"] == "INC").sum())
not_updated_count = int((df["inactivity_days"] > NOT_UPDATED_DAYS).sum())
avg_time_taken = round(float(df["ticket_age_days"].mean()), 2) if len(df) else 0

st.markdown("## 📊 Overview")

metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)

metric_col1.metric("Total Tickets", total_tickets)
metric_col2.metric("L1 Tickets", l1_tickets)
metric_col3.metric("L2 Tickets", l2_tickets)
metric_col4.metric(f"Not Updated >{NOT_UPDATED_DAYS} Days", not_updated_count)
metric_col5.metric("Avg Time Taken (Days)", avg_time_taken)

metric_col6, metric_col7 = st.columns(2)

metric_col6.metric("RITM Count", ritm_count)
metric_col7.metric("INC Count", inc_count)

with st.expander("🔍 Backlog count explanation (why numbers differ)"):
    st.markdown(
        """
        This dashboard now includes both **RITM** and **Incident** tickets.

        **Included ticket classes**
        - RITM / `sc_req_item`
        - INC / `incident`

        **L1 assignment groups**
        - IT Supp: EAST - Delivery
        - IT Supp: System Access Requests

        **L2 assignment group**
        - IT Supp: EAST - Leads

        This fixes the earlier issue where incidents in **IT Supp: EAST - Leads**
        were missing from total backlog and individual backlog views.
        """
    )


# ============================================================
# VIEW SELECTOR
# ============================================================

view = st.radio(
    "",
    [
        "All Tickets",
        "L1 Tickets",
        "L2 Tickets",
        "Incidents",
        "Individual Report",
        "Not Updated",
        "Needs Action",
        "Management Intervention",
    ],
    horizontal=True,
)


# ============================================================
# FILTER VIEW
# ============================================================

display_df = df.copy()

if view == "L1 Tickets":
    display_df = display_df[display_df["level"] == "L1"].copy()

elif view == "L2 Tickets":
    display_df = display_df[display_df["level"] == "L2"].copy()

elif view == "Incidents":
    display_df = display_df[display_df["ticket_type"] == "INC"].copy()

elif view == "Not Updated":
    display_df = display_df[display_df["inactivity_days"] > NOT_UPDATED_DAYS].copy()

elif view == "Needs Action":
    display_df = display_df[
        (display_df["priority"].isin(["1 - Critical", "2 - High", "3 - Moderate", "Critical", "High", "Medium"]))
        | (display_df["inactivity_days"] > NOT_UPDATED_DAYS)
        | (display_df["age_bucket"].isin(["STALE", "OLD"]))
    ].copy()

elif view == "Management Intervention":
    display_df = display_df[
        (display_df["age_bucket"].isin(["OLD"]))
        | (display_df["inactivity_days"] >= 5)
        | (display_df["priority"].isin(["1 - Critical", "2 - High", "Critical", "High"]))
    ].copy()


# ============================================================
# INDIVIDUAL REPORT
# ============================================================

if view == "Individual Report":
    st.markdown("## 👤 Individual Backlog Report")

    individual_df = (
        df.groupby("assigned_to", dropna=False)
        .agg(
            total_tickets=("number", "count"),
            ritm_count=("ticket_type", lambda x: int((x == "RITM").sum())),
            incident_count=("ticket_type", lambda x: int((x == "INC").sum())),
            l1_count=("level", lambda x: int((x == "L1").sum())),
            l2_count=("level", lambda x: int((x == "L2").sum())),
            not_updated=("inactivity_days", lambda x: int((x > NOT_UPDATED_DAYS).sum())),
            avg_age_days=("ticket_age_days", "mean"),
            max_age_days=("ticket_age_days", "max"),
            new_count=("age_bucket", lambda x: int((x == "NEW").sum())),
            aging_count=("age_bucket", lambda x: int((x == "AGING").sum())),
            stale_count=("age_bucket", lambda x: int((x == "STALE").sum())),
            old_count=("age_bucket", lambda x: int((x == "OLD").sum())),
        )
        .reset_index()
        .rename(columns={"assigned_to": "Assignee"})
    )

    individual_df["avg_age_days"] = individual_df["avg_age_days"].round(2)

    individual_df = individual_df.sort_values(
        by=["total_tickets", "old_count", "not_updated"],
        ascending=[False, False, False],
    )

    st.dataframe(
        individual_df,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇️ Download Individual Report CSV",
        individual_df.to_csv(index=False).encode("utf-8"),
        file_name="east_individual_backlog_report.csv",
        mime="text/csv",
    )

else:
    st.markdown(f"## {view}")

    columns_to_show = [
        "ticket_type",
        "number",
        "level",
        "assigned_to",
        "assignment_group",
        "priority",
        "state",
        "open_date",
        "last_updated",
        "ticket_age_days",
        "inactivity_days",
        "sla_remaining_days",
        "age_bucket",
        "source",
    ]

    show_df = display_df[columns_to_show].copy()

    st.dataframe(
        show_df,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        f"⬇️ Download {view} CSV",
        show_df.to_csv(index=False).encode("utf-8"),
        file_name=f"east_{view.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )


# ============================================================
# VALIDATION SECTION
# ============================================================

with st.expander("✅ Data validation summary"):
    validation_df = pd.DataFrame(
        [
            {"Metric": "Total Tickets", "Count": len(df)},
            {"Metric": "RITM / sc_req_item", "Count": int((df["ticket_type"] == "RITM").sum())},
            {"Metric": "Incident", "Count": int((df["ticket_type"] == "INC").sum())},
            {"Metric": "L1", "Count": int((df["level"] == "L1").sum())},
            {"Metric": "L2", "Count": int((df["level"] == "L2").sum())},
            {"Metric": f"Not Updated > {NOT_UPDATED_DAYS} Days", "Count": int((df["inactivity_days"] > NOT_UPDATED_DAYS).sum())},
        ]
    )

    st.dataframe(validation_df, use_container_width=True, hide_index=True)

    st.markdown("### Ticket type split")
    type_split = (
        df.groupby(["ticket_type", "source"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )

    st.dataframe(type_split, use_container_width=True, hide_index=True)

    st.markdown("### Assignment group split")
    group_split = (
        df.groupby(["assignment_group", "level", "ticket_type"])
        .size()
        .reset_index(name="count")
        .sort_values(["assignment_group", "level", "ticket_type"])
    )

    st.dataframe(group_split, use_container_width=True, hide_index=True)

    st.markdown("### Assignee split")
    assignee_split = (
        df.groupby(["assigned_to", "ticket_type"])
        .size()
        .reset_index(name="count")
        .sort_values(["assigned_to", "ticket_type"])
    )

    st.dataframe(assignee_split, use_container_width=True, hide_index=True)


# ============================================================
# FOOTER
# ============================================================

st.caption(
    "EAST Ticket Lifecycle Dashboard | Includes active/open RITMs and Incidents from EAST L1/L2 assignment groups."
)
