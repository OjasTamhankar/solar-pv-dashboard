# Solar PV Monitoring & Generation Forecasting

A local React + TypeScript dashboard backed by FastAPI. Plant configuration persists to JSON, and generation values are calculated by the Python engine from a local weather CSV.

## Run locally

Requirements: Python 3.10+ and Node.js 18+.

1. Install backend dependencies: `cd backend` then `python -m pip install -r requirements.txt`.
2. Start the API from the project root: `python -m uvicorn app.main:app --app-dir backend --reload`.
3. In another terminal: `cd frontend`, run `npm install`, then `npm run dev`.
4. Open the Vite URL (normally `http://localhost:5173`).

The API defaults to the included `SOLAR_WEATHER_CSV.csv`. Upload a CSV with **Add weather CSV** on the dashboard; the app validates required columns before activating it and stores the upload unchanged in `data/uploads/`. You can also set `SOLAR_WEATHER_CSV` (absolute or project-relative path). The source CSV is read only. Missing files, invalid schema, and unavailable matching dates are reported in the dashboard and `/api/weather/status`; no weather records are synthesized. Plant settings are stored in `config/plant.json`. Override with `SOLAR_CONFIG_PATH` if desired.

## Public deployment

The app uses a React frontend and a FastAPI backend, so deploy both services:

1. Push this repository to GitHub.
2. In Render, create a Blueprint from the repository. `render.yaml` creates the API service. Set `SOLAR_FRONTEND_ORIGINS` to the public Netlify site origin (for example, `https://your-site.netlify.app`) when prompted.
3. In Netlify, import the same repository. `netlify.toml` sets the frontend build and publish directory.
4. In Netlify site environment variables, set `VITE_API_BASE_URL` to the Render service origin (for example, `https://solar-pv-api.onrender.com`), then trigger a new deploy.

The Render API includes the sample weather CSV from this repository. Uploaded weather files and configuration changes are written to the service filesystem; on hosts with ephemeral storage those changes may reset when the service is redeployed. The sample configuration is restored from `config/plant.json` after a restart.

## Data and calculations

The CSV requires `period_end`, `gti` (W/m²), and `air_temp` (°C). ISO timestamps with offsets are normalized to the configured plant timezone (`Asia/Kolkata` initially); invalid records are dropped, duplicates keep their last source row, and remaining records are sorted. The actual next chronological record drives the deterministic forecast.

The displayed current Karad temperature comes from Open-Meteo current conditions at Karad coordinates. This display-only reading is returned by `/api/weather/karad-current` and is never passed to generation or CSV forecast calculations. The forecast table shows both the live Karad reading and the timestamp-specific CSV ambient input used by the model. If the live service is unavailable, the live value shows unavailable rather than substituting CSV data.

Current dashboard selection uses the matching date/time one year earlier, then nearest record on that same date, then the configured reference date. The dashboard identifies unavailable data explicitly. Daily energy is the integral over records available on the selected date and is labeled as partial day. Forecast interval energy holds next-record power constant for the actual interval.

The default generation model is `AC rating × GTI/1000 × PR`, capped at configured AC capacity. The optional temperature model uses module DC nameplate, NOCT, temperature coefficient, and PR, replacing the supplied 98.7% reference inverter component with the configured inverter efficiency. This avoids stacking an extra inverter loss on the aggregate PR. This implementation preserves editable equipment electrical fields and validates string voltage/current limits when sufficient values are present. Equipment figures are editable example configuration values, not verified product claims.

## Configuration

Use the Plant Configuration section to update plant details, select identical/shared or independent subsystem values, add/remove systems, choose the calculation model, and save. The backend validates entered parameters. Switching back to identical mode asks which subsystem to use as the shared source.

## Verify

Run backend unit tests from the project root: `python -m pytest backend/tests`. Build the UI with `cd frontend && npm run build`.
