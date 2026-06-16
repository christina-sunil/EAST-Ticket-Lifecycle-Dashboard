import requests
import json
from datetime import datetime

INSTANCE = "https://progress1.service-now.com"
API_KEY = "now_ojtqFALqS5jSWkyZbAr8GOM53uv1KkXJljoBUvjVKC8qaLUGCZt2zYNBhGxKRUwfXStmjt3tIWAtJ3n7V-ERBg"
RITM = "RITM0264671"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-sn-apikey": API_KEY
}

def get(table, query=None, limit=200, fields=None):
    url = f"{INSTANCE}/api/now/table/{table}"
    params = {"sysparm_limit": limit}
    if query:
        params["sysparm_query"] = query
    if fields:
        params["sysparm_fields"] = fields
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    return r

def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None

def parse_dt(s):
    # ServiceNow format: "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

print("======================================")
print("LIFECYCLE BUILD (via task + sys_audit)")
print("======================================")

# 1) Fetch RITM record via task (workaround for sc_req_item 401)
q = f"sys_class_name=sc_req_item^number={RITM}"
fields = "number,sys_id,state,opened_at,sys_updated_on,short_description,assigned_to,assignment_group,sys_class_name"
r = get("task", query=q, limit=1, fields=fields)

print("\n1) Fetch RITM via /task")
print("Status:", r.status_code)
data = safe_json(r)
if not data or not data.get("result"):
    print("❌ Could not fetch RITM via task. Response:")
    print(r.text[:800])
    raise SystemExit(1)

ritm_row = data["result"][0]
doc_sys_id = ritm_row.get("sys_id")

print("✅ Found:", ritm_row.get("number"), "sys_id:", doc_sys_id)
print("Short desc:", ritm_row.get("short_description"))

# 2) Pull sys_audit rows
print("\n2) Pull sys_audit rows")
audit_fields = "fieldname,oldvalue,newvalue,sys_created_on,user"
audit_q = f"documentkey={doc_sys_id}^ORDERBYsys_created_on"
a = get("sys_audit", query=audit_q, limit=500, fields=audit_fields)
print("Audit Status:", a.status_code)

audit = safe_json(a)
if not audit:
    print("❌ Could not parse audit JSON:")
    print(a.text[:800])
    raise SystemExit(1)

rows = audit.get("result", [])
print("Audit rows:", len(rows))

# 3) Build timeline
timeline = []
for row in rows:
    created = row.get("sys_created_on")
    dt = parse_dt(created) if created else None
    timeline.append({
        "dt": dt,
        "when": created,
        "field": row.get("fieldname"),
        "old": row.get("oldvalue"),
        "new": row.get("newvalue"),
        "user": row.get("user")
    })

timeline.sort(key=lambda x: (x["dt"] is None, x["dt"]))

# 4) Print clean lifecycle summary
print("\n======================================")
print(f"LIFECYCLE TIMELINE for {RITM}")
print("======================================")

important_fields = {
    "state",
    "assignment_group",
    "assigned_to",
    "approval_history",
    "comments",
    "work_notes",
    "watch_list",
    "short_description",
    "description",
}

shown = 0
for e in timeline:
    field = (e["field"] or "").strip()
    if important_fields and field not in important_fields:
        # Still show approval_history + watch_list etc; filter the noise
        continue

    new_val = (e["new"] or "")
    old_val = (e["old"] or "")

    # Clean up very long journal fields for console readability
    if old_val == "JOURNAL FIELD ADDITION":
        old_val = "(journal addition)"
    if len(new_val) > 180:
        new_val = new_val[:180] + "..."

    print(f"- {e['when']} | field={field} | user={e['user']}")
    print(f"  old: {old_val}")
    print(f"  new: {new_val}\n")
    shown += 1

if shown == 0:
    print("⚠️ No important audited fields found (might not have auditing enabled for state/assignment).")
    print("We can still print ALL audit rows if you want (remove the filter).")

print("✅ Done.")
