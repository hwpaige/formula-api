import os
import json
import zlib
import base64
import requests
import redis
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="F1 Buddy Proxy API", description="Proxies and caches data from OpenF1 API")

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

@app.get("/")
async def root():
    return {"status": "ok", "service": "F1 Buddy Proxy API", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/meetings")
async def get_meetings(year: int = None):
    cache_key = f"f1_meetings_{year}" if year else "f1_meetings_all"
    cached = get_cached_data(cache_key)
    if cached:
        return cached

    url = f"{OPENF1_BASE_URL}/meetings"
    if year:
        url += f"?year={year}"

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        set_cached_data(cache_key, data, ttl=3600)  # Cache meetings for 1 hour
        return data
    raise HTTPException(status_code=response.status_code, detail="Error fetching meetings from OpenF1")

@app.get("/sessions")
async def get_sessions(meeting_key: int = None, session_key: int = None):
    cache_key = f"f1_sessions_m{meeting_key}_s{session_key}"
    cached = get_cached_data(cache_key)
    if cached:
        return cached

    params = []
    if meeting_key: params.append(f"meeting_key={meeting_key}")
    if session_key: params.append(f"session_key={session_key}")
    
    url = f"{OPENF1_BASE_URL}/sessions"
    if params:
        url += "?" + "&".join(params)

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        set_cached_data(cache_key, data, ttl=1800)  # Cache sessions for 30 mins
        return data
    raise HTTPException(status_code=response.status_code, detail="Error fetching sessions from OpenF1")

@app.get("/{data_type}")
async def proxy_data(data_type: str, session_key: int, date_gt: str = None):
    """
    Proxy for dynamic F1 data (weather, positions, laps, etc.)
    """
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
        return cached

    url = f"{OPENF1_BASE_URL}/{data_type}?session_key={session_key}"
    if date_gt:
        url += f"&date>={date_gt}"

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
    
    if response.status_code == 429:
        # If rate limited, try to return whatever we have in cache if possible
        # (even if it's slightly stale, but here we don't have it in cache or it's expired)
        raise HTTPException(status_code=429, detail="OpenF1 Rate Limit Exceeded")
        
    return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
