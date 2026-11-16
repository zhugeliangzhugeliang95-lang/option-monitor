# Independent Strike Lab

This directory is the standalone replacement requested by the owner. It runs on port 3200 with FastAPI and vanilla JavaScript, independently of the legacy application at the repository root.

- Keep valuation and relative-price calculations in Python. Preserve the 3 + 1 + 3 strike rule and LAST / MID median, minimum and maximum summaries.
- Do not substitute missing quotes, remove near strikes to fill from farther strikes, or label old snapshots as fresh observations.
- Keep contract metadata, original timestamps and validation restrictions through import and editing. Changed prices must not inherit original event timestamps.
- Do not introduce trading or account-management endpoints.
- This application has no SQLAlchemy, SSE worker, Next.js or TypeScript build. Do not add dependencies on the legacy application's database or services.
- Validate relevant changes with `python -m pytest -q`, `node --test tests/frontend.test.cjs`, and `node --check app/static/app.js`. Inspect browser interactions when changed.
- Never commit runtime/, .venv/, credentials, personal drafts, market snapshots or generated archives. The original attachment examples are explicitly labeled historical or illustrative.
