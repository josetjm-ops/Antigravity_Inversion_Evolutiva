# Entry point for Streamlit Community Cloud.
# Cloud looks for streamlit_app.py at the repo root; this re-exports dashboard/app.py.
from dashboard.app import main

main()
