# Always-on poller via GitHub Actions (runs on GitHub, not your Mac)

## One-time setup (~5 min)
1. Create a new PRIVATE GitHub repo, e.g. `smallest-reengage-poller`.
2. Put these 4 files in it (exactly this layout):
       orchestrator.py
       load_ready.json
       orchestrator_state.json
       .github/workflows/poll.yml
   (Drag the contents of this `cloud_poller` folder into the repo.)
3. Repo Settings -> Secrets and variables -> Actions -> New repository secret, add two:
       SMARTLEAD_KEY = <your Smartlead API key>
       HEYREACH_KEY  = <your HeyReach API key>
4. Commit & push. Open the Actions tab, enable workflows if prompted.

Done. It runs every 5 minutes, does one poll cycle (reply-stop on both
channels + email fallback for silent LinkedIn leads), and commits the updated
state back so the next run continues where it left off.

## Terminal route (if you prefer)
    cd smallest-reengage-poller
    cp -r "<this cloud_poller folder>/." .
    git init && git add -A && git commit -m "poller"
    gh repo create smallest-reengage-poller --private --source=. --push   # needs gh
    gh secret set SMARTLEAD_KEY -b"<key>"
    gh secret set HEYREACH_KEY  -b"<key>"

## Notes
- Min cron interval on Actions is 5 min; runs may lag a few min under load. Fine here.
- Keys live only in GitHub Secrets, never in code.
- Pause anytime: Actions tab -> disable the workflow.
- This replaces the local crontab; don't run both or they'll race (the state
  commit handles concurrency, but one source is cleaner).
