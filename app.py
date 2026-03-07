import os
import json
import zlib
import base64
import requests
import redis
import time
import threading
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="F1 Buddy Proxy API", description="Proxies and caches data from OpenF1 API")

# Metrics tracking
_local_metrics = {
    "total_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "openf1_errors": 0
}
_local_metrics_history = []
_start_time = datetime.now(timezone.utc)

def update_metric(field):
    if field in _local_metrics:
        _local_metrics[field] += 1

# Redis Configuration (matching launch-summary-api pattern)
def get_redis_client():
    redis_url = os.getenv("REDIS_URL") or os.getenv("REDISCLOUD_URL") or os.getenv("REDISTOGO_URL")
    if redis_url:
        try:
            if redis_url.startswith("rediss://"):
                client = redis.from_url(redis_url, decode_responses=True, ssl_cert_reqs=None)
            else:
                client = redis.from_url(redis_url, decode_responses=True)
            client.ping()
            print(f"Connected to Redis")
            return client
        except Exception as e:
            print(f"Failed to connect to Redis: {e}")
    return None

r = get_redis_client()

def set_cached_data(key, data, ttl=300):
    """Set data in Redis with compression."""
    if not r:
        return False
    try:
        json_str = json.dumps(data)
        compressed = zlib.compress(json_str.encode('utf-8'))
        encoded = base64.b64encode(compressed).decode('utf-8')
        r.setex(key, ttl, encoded)
        return True
    except Exception as e:
        print(f"Redis write error for {key}: {e}")
        return False

def get_cached_data(key):
    """Get data from Redis with decompression."""
    if not r:
        return None
    try:
        data = r.get(key)
        if not data:
            return None
        decoded = base64.b64decode(data)
        decompressed = zlib.decompress(decoded)
        return json.loads(decompressed)
    except Exception as e:
        print(f"Redis read error for {key}: {e}")
        return None

OPENF1_BASE_URL = "https://api.openf1.org/v1"

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>F1 Buddy Proxy API Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
        body { font-family: 'Inter', sans-serif; }
    </style>
</head>
<body class="bg-slate-950 text-slate-200 min-h-screen">
    <nav class="border-b border-slate-800 bg-slate-900/50 backdrop-blur-md sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex items-center justify-between h-16">
                <div class="flex items-center gap-2">
                    <i data-lucide="flag" class="text-red-500 w-8 h-8"></i>
                    <span class="text-xl font-bold tracking-tight">F1 Buddy API</span>
                </div>
                <div class="flex items-center gap-4">
                    <div class="flex items-center gap-2 px-3 py-1 bg-slate-950 border border-slate-800 rounded-lg">
                        <i data-lucide="clock" class="text-slate-500 w-4 h-4"></i>
                        <span id="utc-clock" class="text-xs font-mono font-bold text-slate-400">00:00:00 UTC</span>
                    </div>
                </div>
            </div>
        </div>
    </nav>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div class="mb-8">
            <h2 class="text-2xl font-bold tracking-tight">System Status</h2>
            <p class="text-slate-400 text-sm">Real-time performance and API proxy metrics.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
             <div class="bg-slate-900/50 border border-slate-800 p-4 rounded-2xl flex items-center gap-4">
                <div class="p-3 bg-blue-500/10 rounded-xl">
                    <i data-lucide="activity" class="text-blue-500 w-6 h-6"></i>
                </div>
                <div>
                    <p class="text-slate-500 text-[10px] font-bold uppercase tracking-wider">Total Requests</p>
                    <p class="text-xl font-bold" id="stat-requests">0</p>
                </div>
             </div>
             <div class="bg-slate-900/50 border border-slate-800 p-4 rounded-2xl flex items-center gap-4">
                <div class="p-3 bg-emerald-500/10 rounded-xl">
                    <i data-lucide="zap" class="text-emerald-500 w-6 h-6"></i>
                </div>
                <div>
                    <p class="text-slate-500 text-[10px] font-bold uppercase tracking-wider">Cache Hits</p>
                    <p class="text-xl font-bold" id="stat-hits">0</p>
                </div>
             </div>
             <div class="bg-slate-900/50 border border-slate-800 p-4 rounded-2xl flex items-center gap-4">
                <div class="p-3 bg-amber-500/10 rounded-xl">
                    <i data-lucide="database" class="text-amber-500 w-6 h-6"></i>
                </div>
                <div>
                    <p class="text-slate-500 text-[10px] font-bold uppercase tracking-wider">Redis Status</p>
                    <p class="text-xl font-bold" id="stat-redis">Checking...</p>
                </div>
             </div>
             <div class="bg-slate-900/50 border border-slate-800 p-4 rounded-2xl flex items-center gap-4">
                <div class="p-3 bg-red-500/10 rounded-xl">
                    <i data-lucide="alert-circle" class="text-red-500 w-6 h-6"></i>
                </div>
                <div>
                    <p class="text-slate-500 text-[10px] font-bold uppercase tracking-wider">API Errors</p>
                    <p class="text-xl font-bold" id="stat-errors">0</p>
                </div>
             </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
                    <i data-lucide="book-open" class="text-blue-400 w-5 h-5"></i>
                    API Documentation
                </h3>
                <div class="space-y-4">
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <code class="text-blue-400">GET /meetings?year=2024</code>
                        <p class="text-xs text-slate-500 mt-1">Fetch all F1 meetings for a specific year.</p>
                    </div>
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <code class="text-blue-400">GET /sessions?meeting_key=1234</code>
                        <p class="text-xs text-slate-500 mt-1">Fetch sessions for a given meeting.</p>
                    </div>
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <code class="text-blue-400">GET /{data_type}?session_key=5678</code>
                        <p class="text-xs text-slate-500 mt-1">Proxy dynamic data (weather, positions, car_data, etc).</p>
                    </div>
                </div>
            </div>

            <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
                    <i data-lucide="info" class="text-amber-400 w-5 h-5"></i>
                    Service Information
                </h3>
                <div class="space-y-3 text-sm">
                    <div class="flex justify-between py-2 border-b border-slate-800">
                        <span class="text-slate-500">Uptime</span>
                        <span id="stat-uptime" class="font-mono">00:00:00</span>
                    </div>
                    <div class="flex justify-between py-2 border-b border-slate-800">
                        <span class="text-slate-500">Source API</span>
                        <span class="text-red-400 font-bold">OpenF1</span>
                    </div>
                    <div class="flex justify-between py-2 border-b border-slate-800">
                        <span class="text-slate-500">Environment</span>
                        <span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] font-bold uppercase">Production</span>
                    </div>
                    <div class="flex justify-between py-2">
                        <span class="text-slate-500">API Version</span>
                        <span class="font-mono">v1.0.0</span>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <script>
        lucide.createIcons();

        function updateClock() {
            const now = new Date();
            document.getElementById('utc-clock').textContent = now.toISOString().split('T')[1].split('.')[0] + ' UTC';
        }
        setInterval(updateClock, 1000);
        updateClock();

        async function fetchMetrics() {
            try {
                const response = await fetch('/metrics');
                const data = await response.json();
                
                document.getElementById('stat-requests').textContent = data.total_requests;
                document.getElementById('stat-hits').textContent = data.cache_hits;
                document.getElementById('stat-errors').textContent = data.openf1_errors;
                document.getElementById('stat-redis').textContent = data.redis_connected ? 'Connected' : 'Disconnected';
                document.getElementById('stat-redis').className = data.redis_connected ? 'text-xl font-bold text-emerald-500' : 'text-xl font-bold text-red-500';
                document.getElementById('stat-uptime').textContent = data.uptime;
            } catch (e) {
                console.error('Failed to fetch metrics', e);
            }
        }
        setInterval(fetchMetrics, 5000);
        fetchMetrics();
    </script>
</body>
</html>
"""

@app.get("/metrics")
async def get_metrics():
    uptime = datetime.now(timezone.utc) - _start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return {
        "total_requests": _local_metrics["total_requests"],
        "cache_hits": _local_metrics["cache_hits"],
        "cache_misses": _local_metrics["cache_misses"],
        "openf1_errors": _local_metrics["openf1_errors"],
        "redis_connected": r is not None,
        "uptime": f"{hours:02}:{minutes:02}:{seconds:02}"
    }

@app.get("/meetings")
async def get_meetings(year: int = None):
    update_metric("total_requests")
    cache_key = f"f1_meetings_{year}" if year else "f1_meetings_all"
    cached = get_cached_data(cache_key)
    if cached:
        update_metric("cache_hits")
        return cached

    update_metric("cache_misses")
    url = f"{OPENF1_BASE_URL}/meetings"
    if year:
        url += f"?year={year}"

    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            set_cached_data(cache_key, data, ttl=3600)  # Cache meetings for 1 hour
            return data
        update_metric("openf1_errors")
        raise HTTPException(status_code=response.status_code, detail="Error fetching meetings from OpenF1")
    except Exception as e:
        update_metric("openf1_errors")
        raise e

@app.get("/sessions")
async def get_sessions(meeting_key: int = None, session_key: int = None):
    update_metric("total_requests")
    cache_key = f"f1_sessions_m{meeting_key}_s{session_key}"
    cached = get_cached_data(cache_key)
    if cached:
        update_metric("cache_hits")
        return cached

    update_metric("cache_misses")
    params = []
    if meeting_key: params.append(f"meeting_key={meeting_key}")
    if session_key: params.append(f"session_key={session_key}")
    
    url = f"{OPENF1_BASE_URL}/sessions"
    if params:
        url += "?" + "&".join(params)

    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            set_cached_data(cache_key, data, ttl=1800)  # Cache sessions for 30 mins
            return data
        update_metric("openf1_errors")
        raise HTTPException(status_code=response.status_code, detail="Error fetching sessions from OpenF1")
    except Exception as e:
        update_metric("openf1_errors")
        raise e

@app.get("/{data_type}")
async def proxy_data(data_type: str, session_key: int, date_gt: str = None):
    """
    Proxy for dynamic F1 data (weather, positions, laps, etc.)
    """
    update_metric("total_requests")
    allowed_types = ['weather', 'positions', 'drivers', 'laps', 'race_control', 'location', 'car_data', 'stints', 'intervals', 'pit']
    if data_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid data type")

    # Construct cache key
    cache_key = f"f1_{data_type}_sk{session_key}"
    if date_gt:
        # For incremental updates, we might want shorter TTL or different keying strategy
        cache_key += f"_gt{date_gt}"

    cached = get_cached_data(cache_key)
    if cached:
        update_metric("cache_hits")
        return cached

    update_metric("cache_misses")
    url = f"{OPENF1_BASE_URL}/{data_type}?session_key={session_key}"
    if date_gt:
        url += f"&date>={date_gt}"

    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            
            # Determine TTL based on data volatility
            if data_type in ['positions', 'car_data', 'location']:
                ttl = 5  # Very volatile
            elif data_type in ['weather', 'intervals']:
                ttl = 30 # Semi-volatile
            else:
                ttl = 300 # More stable (drivers, laps, stints)
                
            set_cached_data(cache_key, data, ttl=ttl)
            return data
        
        update_metric("openf1_errors")
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="OpenF1 Rate Limit Exceeded")
            
        return []
    except Exception as e:
        update_metric("openf1_errors")
        raise e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
