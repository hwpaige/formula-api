# F1 Buddy Proxy API

A high-performance FastAPI-based proxy for the [OpenF1 API](https://openf1.org) with built-in Redis caching to improve performance and avoid rate limits. It acts as a middle-tier to serve multiple F1 Dash clients efficiently.

## Features
- **Compressed Caching**: Uses `zlib` compression to minimize Redis memory usage.
- **Intelligent TTL**: Dynamic cache expiry based on data volatility (e.g., telemetry expires faster than race results).
- **Heroku Ready**: Includes `Procfile` and `runtime.txt` for immediate deployment.
- **Dashboard Implementation**: Added a professional Tailwind CSS dashboard at `/` with tabs for Metrics, Cache Inspection, and Seeding Control.
- **Seeding Functionality**: Introduced a background seeding engine to pre-cache historical F1 data for specified years.
- **Background Worker**: Added a dedicated worker thread to handle metrics snapshots and automatic data refreshes.
- **Improved Metrics**: Real-time tracking of total requests, cache efficiency, and API errors with persistent storage in Redis.
- **Cache Inspector**: Integrated tool to browse and inspect active Redis keys directly from the dashboard.

---

## API Endpoints

### 1. Root / Dashboard
Interactive dashboard and system metrics.
- **Endpoint:** `GET /`
- **Tabs:** Metrics, Cache Inspector, Seeding, API Docs.

### 2. Seeding Control
Manage historical data pre-caching.
- **Endpoint:** `POST /seed_history`
- **Parameters:** `years` (comma-separated, default: "2023,2024")
- **Endpoint:** `POST /stop_seeding`
- **Endpoint:** `GET /seed_status`

### 3. Cache Management
Inspect current cache state.
- **Endpoint:** `GET /cache_keys`
- **Endpoint:** `GET /cache_data/{key}`

### 4. Get Meetings
Returns a list of F1 meetings (Grand Prix events).
- **Endpoint:** `GET /meetings`
- **Parameters:** `year` (optional, int)
- **Caching:** 1 hour.

### 5. Get Sessions
Returns a list of sessions for a specific meeting.
- **Endpoint:** `GET /sessions`
- **Parameters:**
    - `meeting_key`: (optional, int) Unique identifier for the meeting.
    - `session_key`: (optional, int) Unique identifier for the session.
- **Caching:** 30 minutes.

### 6. Dynamic Data (Telemetry, Weather, etc.)
Proxies various dynamic data types for a specific session.
- **Endpoint:** `GET /{data_type}`
- **Valid `data_type` values:**
    - `weather`: Ambient and track conditions.
    - `positions`: Live car positions on track.
    - `drivers`: Driver information for the session.
    - `laps`: Lap times and sector data.
    - `race_control`: Official race control messages.
    - `location`: GPS location data (high frequency).
    - `car_data`: Telemetry (RPM, speed, gear, etc.).
    - `stints`: Tire stint information.
    - `intervals`: Gaps between drivers.
    - `pit`: Pit stop information.
- **Parameters:**
    - `session_key`: (required, int) Unique identifier for the session.
    - `date_gt`: (optional, string) ISO8601 timestamp to fetch data since a specific time (incremental updates).
- **Caching (TTL):**
    - `positions`, `car_data`, `location`: 5 seconds.
    - `weather`, `intervals`: 30 seconds.
    - All others: 5 minutes.

---

## Data Volatility & Caching Strategy
The API utilizes a tiered caching strategy based on how often the underlying data changes:
1. **Volatile (5s)**: Live telemetry and GPS positions.
2. **Semi-Volatile (30s)**: Weather updates and driver intervals.
3. **Stable (5m)**: Laps, pit stops, and stint data.
4. **Static (30m - 1h)**: Session and meeting metadata.

---

## Local Setup
1. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Configuration:**
   Copy `.env.example` to `.env` and set your `REDIS_URL`.
   ```bash
   cp .env.example .env
   ```
4. **Run the server:**
   ```bash
   uvicorn app:app --reload --port 5000
   ```

---

## Heroku Deployment
1. **Create a new Heroku app:** `heroku create f1-buddy-api`
2. **Add Redis:** `heroku addons:create heroku-redis:mini`
3. **Deploy:** `git push heroku main`

---

## Usage Examples

### cURL
```bash
curl "http://localhost:5000/meetings?year=2024"
```

### Python (Requests)
```python
import requests

BASE_URL = "http://localhost:5000"
session_key = 9468

# Fetch live car data
response = requests.get(f"{BASE_URL}/car_data", params={"session_key": session_key})
telemetry = response.json()
print(f"Fetched {len(telemetry)} telemetry points.")
```

### JavaScript (Fetch)
```javascript
async function getF1Weather(sessionKey) {
  const response = await fetch(`http://localhost:5000/weather?session_key=${sessionKey}`);
  const data = await response.json();
  console.log('Current Track Temp:', data[0].track_temperature);
}
```

---

## License
MIT
