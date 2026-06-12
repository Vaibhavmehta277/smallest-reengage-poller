#!/usr/bin/env python3
"""
Smallest P1 Re-engagement orchestrator.
Drives the omnichannel workflow across Smartlead (email) + HeyReach (LinkedIn).

Commands:
  python orchestrator.py load     # one-time: load the 134 leads into the right campaigns
  python orchestrator.py poll     # run one polling cycle (cron this every 5 min)
  python orchestrator.py status   # print current state summary

Config flags below. Set CONNECTION_SPLIT=False to run the robust
"email-first for everyone, LinkedIn on engagement" mode (no Track A/B).
"""
import json, time, urllib.request, urllib.parse, os

import os
SL_KEY = os.environ["SMARTLEAD_KEY"]
HR_KEY = os.environ["HEYREACH_KEY"]
SL_CAMPAIGN = 3487952
HR_CHECK = 467439
HR_ENGAGE = 467437

# MODE = native Track A/B: 90 LinkedIn leads run in Engage (HeyReach auto-splits
# already-connected -> message first vs not-connected -> invite first).
# Email is fallback: a LinkedIn lead with no reply after N days gets the email sequence.
LI_NOREPLY_EMAIL_DAYS = 4      # LinkedIn lead with no reply after N days -> start email

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "orchestrator_state.json")
LOAD = os.path.join(HERE, "load_ready.json")

def sl(path, method="GET", body=None):
    url = f"https://server.smartlead.ai/api/v1{path}{'&' if '?' in path else '?'}api_key={SL_KEY}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")

def hr(path, body=None):
    url = f"https://api.heyreach.io/api/public{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "X-API-KEY": HR_KEY, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")

def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}

def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)

def now():
    return int(time.time())

# ---------- LOAD ----------
def add_sl_lead(rec):
    payload = {"lead_list": [{
        "email": rec["email"], "first_name": rec["first_name"], "last_name": rec.get("last_name", ""),
        "company_name": rec["company"], "custom_fields": rec["sl_custom"]}],
        "settings": {"ignore_global_block_list": False, "ignore_unsubscribe_list": False,
                     "ignore_duplicate_leads_in_other_campaign": True}}
    return sl(f"/campaigns/{SL_CAMPAIGN}/leads", "POST", payload)

def add_hr_lead(campaign_id, rec):
    pair = {"lead": {"firstName": rec["first_name"], "lastName": rec.get("last_name", ""),
                     "profileUrl": rec["profileUrl"], "companyName": rec["company"],
                     "customUserFields": [{"name": k, "value": v} for k, v in rec["hr_custom"].items()]},
            "linkedInAccountId": rec["li_sender_id"]}
    return hr("/campaign/AddLeadsToCampaignV2", {"campaignId": campaign_id, "accountLeadPairs": [pair]})

def cmd_load():
    recs = json.load(open(LOAD))
    st = load_state()
    for rec in recs:
        em = rec["email"].lower()
        s = st.get(em, {})
        if s.get("loaded"):
            continue
        s.update({"email": rec["email"], "channel": rec["channel"], "track": "unknown",
                  "sl_added": False, "check_added": False, "engage_added": False,
                  "stopped": False, "loaded": True, "li_sent_at": None})
        if rec["channel"] == "email_only" or not rec["profileUrl"]:
            add_sl_lead(rec); s["sl_added"] = True; s["track"] = "email_only"
        else:
            if CONNECTION_SPLIT:
                add_hr_lead(HR_CHECK, rec); s["check_added"] = True   # classify first
            else:
                add_sl_lead(rec); s["sl_added"] = True; s["track"] = "B"
        st[em] = s
        time.sleep(0.3)
    save_state(st)
    print(f"loaded {len(recs)} leads")

# ---------- POLL ----------
def cmd_poll():
    recs = {r["email"].lower(): r for r in json.load(open(LOAD))}
    st = load_state()

    # 1) Email reply -> stop both channels
    stats, off = [], 0
    while True:
        page = sl(f"/campaigns/{SL_CAMPAIGN}/statistics?offset={off}&limit=100").get("data", [])
        stats += page
        if len(page) < 100:
            break
        off += 100
    by_email = {}
    for row in stats:
        by_email.setdefault(row["lead_email"].lower(), row)
    for em, row in by_email.items():
        s = st.get(em)
        if not s or s.get("stopped"):
            continue
        if row.get("reply_time"):
            stop_lead(recs.get(em), s); s["stopped"] = True

    # 2) LinkedIn reply -> stop both channels
    leads, off = [], 0
    while True:
        page = hr("/campaign/GetLeadsFromCampaign", {"campaignId": HR_ENGAGE, "offset": off, "limit": 100}).get("items", [])
        leads += page
        if len(page) < 100:
            break
        off += 100
    for L in leads:
        em = find_email(L, recs)
        s = st.get(em)
        if not s or s.get("stopped"):
            continue
        if li_replied(L):
            stop_lead(recs.get(em), s); s["stopped"] = True

    # 3) Email fallback: LinkedIn lead with no reply after N days -> start email
    for em, s in st.items():
        if s.get("track") == "linkedin" and not s.get("sl_added") and not s.get("stopped") and s.get("li_started_at"):
            if now() - s["li_started_at"] >= LI_NOREPLY_EMAIL_DAYS * 86400:
                add_sl_lead(recs[em]); s["sl_added"] = True

    save_state(st)
    print("poll cycle complete")

def stop_lead(rec, s):
    # pause Smartlead lead + stop HeyReach lead (cross-channel kill)
    try:
        lead = sl(f"/leads/?email={urllib.parse.quote(s['email'])}")
        lid = (lead or {}).get("id")
        if lid:
            sl(f"/campaigns/{SL_CAMPAIGN}/leads/{lid}/pause", "POST", {})
    except Exception:
        pass
    if rec and rec.get("profileUrl"):
        for cid in (HR_CHECK, HR_ENGAGE):
            try:
                hr("/campaign/StopLeadInCampaign", {"campaignId": cid, "leadUrl": rec["profileUrl"]})
            except Exception:
                pass

def find_email(L, recs):
    pu = (L.get("profileUrl") or "").rstrip("/").lower()
    for em, r in recs.items():
        if r.get("profileUrl") and r["profileUrl"].rstrip("/").lower() == pu:
            return em
    return None

# --- These two read HeyReach lead objects; exact fields confirmed during stress test ---
def is_connected(L):
    # Read leadConnectionStatus (confirmed field). "None" = check not run yet.
    cs = str(L.get("leadConnectionStatus") or "None")
    if cs in ("None", ""):
        return None
    return cs.lower() in ("connected", "1st", "firstdegree", "true")

def li_replied(L):
    return str(L.get("leadMessageStatus") or "").lower() in ("replied", "reply")

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"load": cmd_load, "poll": cmd_poll}.get(cmd, lambda: print(json.dumps(load_state(), indent=1)))()
