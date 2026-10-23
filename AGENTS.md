# Contributor instructions

- This is a local, read-only market research tool. Do not introduce order submission or trade permissions.
- Keep all financial/statistical calculations in Python. Frontend displays backend fields only.
- Preserve raw quotes, point-in-time valuation, config version/hash, model version and exclusion reasons.
- 0 strike counts mean zero, never all. Select nearest strikes before validating pairs. Never substitute a farther strike or another quote basis silently.
- REALTIME requires an actual feed label and quality evidence. MOCK, MANUAL, DELAYED and UNKNOWN remain distinct.
- One SQLAlchemy schema, one independent worker and one API SSE broadcaster. Do not add browser collectors or a second ORM.
- Core changes: run backend pytest. UI changes: run TypeScript and production build; inspect actual UI when interactions change.
- Never copy .env, data/, .venv, node_modules, credential headers or gateway error bodies into deliverable archives.
- See docs/CALCULATION.md and docs/API.md. Follow frontend/AGENTS.md for the installed Next version.
