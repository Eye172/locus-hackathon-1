# MVP on Modal

Live app: https://nnurkhan91--campuslense-web.modal.run

Deployment: https://modal.com/apps/nnurkhan91/main/deployed/campuslense

React and FastAPI run together, with same-origin `/api` and `/s` routes. CLIP and
Depth Anything weights are included in the image. Runtime profiles, SQLite,
thumbnails and generated images live on the existing `campuslens-data` Volume.
Redeploying does not replace that data with the local database.

## Redeploy from PowerShell

Run from the `campuslens` directory after installing the frontend dependencies
with `npm.cmd ci` in `frontend`. The Modal CLI must already be authenticated.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:VITE_API_BASE = ''
$env:MIN_CONTAINERS = '0'
Push-Location frontend
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally {
    Pop-Location
}
modal deploy --strategy recreate deploy/modal_app.py
if ($LASTEXITCODE -ne 0) { throw 'Modal deployment failed' }
python deploy/smoke_check.py
```

Backend API keys are loaded from the ignored `backend/.env` and sent as a Modal
Secret. Vertex credentials come from the ignored `backend/secrets/vertex-sa.json`.
They are not baked into the image. `FRONTEND_ORIGIN` is forced empty in the cloud.
Frontend `VITE_*` variables are public browser configuration, compiled at build
time; server credentials must never be put there.

The explicit `recreate` strategy stops the old container before starting the new
one. Expect a short cold-start interruption and cancellation of active streams.
This avoids a long-lived stream keeping the old version in service during a
rolling update of this single-container MVP. The smoke check verifies the served
index matches the local build, rather than relying only on CLI deployment success.

## Runtime and cost

- One container maximum: SQLite and live profile builds currently belong to one process.
- Zero minimum containers: the app can stop when idle and wake on the next request.
- The idle window is 20 minutes to let background passes finish (their guard is 15 minutes).
- CPU: 2 Modal cores; memory reservation: 6144 MiB. No GPU.
- For a presentation, deploy with `MIN_CONTAINERS=1` to keep it warm, then redeploy
  with `MIN_CONTAINERS=0` afterwards.
- These settings limit capacity, not spending. Check Modal usage; external API
  quotas and charges are separate. No paid plan was enabled by this deployment.

## Verify

```powershell
Invoke-RestMethod 'https://nnurkhan91--campuslense-web.modal.run/api/health'
```

Expect `ok: true`, `index: 14467` and, after model warmup, `clip_ready: true`.
Then search for MIT, open its 3D campus and profile, and check that the profile
receives a fresh generation time and photos through SSE. Also check a photo,
the Climate tab and `/s/Q49108` for the sharing page.

For a consistent SQLite backup, use SQLite's backup API inside the running
container or download the database from the Volume while the app is stopped.
Do not overwrite the live database with a local copy while the app is running.

## Verified deployment — 2026-09-19

- Production frontend build and Python compilation passed.
- Health: CLIP ready, 14,467 universities loaded.
- Browser: university search, MIT 3D map, profile, climate charts and saved university list.
- Live SSE: first profile in 23.2 seconds; final result 199 photos, `partial=false`,
  zero inspector errors. The final profile was readable from the API and recent list,
  and the Volume database modification time advanced.
- HTTP checks: thumbnails, depth PNG, Open Graph PNG, sharing page, city context,
  3D map data, comparison and search settings all returned 200.
- Unknown API paths and encoded attempts to access files outside the frontend
  directory returned 404.

This is an MVP smoke test, not a load test or a guarantee of third-party API availability.
