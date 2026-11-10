# Brand Search Interest (VBI) on the Dell

- Jobs (VGR OS node): `vbi-nightly` (02:30 daily, replaces Windows `VBI-FetchNightly`) and `vbi-monthly` (day 1, 06:00, replaces `VBI-Monthly`, emails the report).
- Code: GitHub `almarpause/vgr-brand-search-interest` cloned at `~/projects/vgr-brand-search-interest`; scripts pull it first. This `dell/` folder is copied from the laptop over SSH by the CodeAutoPush task (not in GitHub).
- Venv `~/venvs/vbi` (uv, from `uv.lock`); setup: `bash dell/setup_dell.sh`.
- The fetch cache (`cache/`, ~90 MB) is data, not code: it was copied once from the laptop (tar over SSH). The monthly report is assembled from it.
- Monthly email reuses the Zara SMTP ini (`~/projects/parser/russia/zara/config/email.ini`, already on the Dell). `bash dell/run_monthly.sh --dry-run` builds without sending.
- The Dell cannot push to GitHub (gh login invalid); the GitHub Action publishes the dashboard from whatever is on GitHub.
- Outputs copied to `~/data/outbox/vbi/` (Drive): nightly status, dashboard, data json, index csv, report docx.