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
_local_seeding_status = {
    "is_running": False,
    "current_year": None,
    "total_meetings": 0,
    "processed_meetings": 0,
    "errors": 0,
    "start_time": None,
    "end_time": None
}
_stop_seeding_requested = False
_start_time = datetime.now(timezone.utc)

# Redis Key Constants
METRICS_KEY = "f1_api_metrics"
METRICS_HISTORY_KEY = "f1_api_metrics_history"
SEEDING_STATUS_KEY = "f1_api_seeding_status"
SEEDING_STOP_SIGNAL_KEY = "f1_api_seeding_stop_signal"

def update_metric(field):
    if field in _local_metrics:
        _local_metrics[field] += 1
    
    # Also update in Redis if available
    if r:
        try:
            current_metrics = r.get(METRICS_KEY)
            metrics = json.loads(current_metrics) if current_metrics else _local_metrics.copy()
            if field in metrics:
                metrics[field] = metrics.get(field, 0) + 1
            r.set(METRICS_KEY, json.dumps(metrics))
        except: pass

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
        .tab-active { border-bottom: 2px solid #ef4444; color: #ef4444; }
        .hidden { display: none; }
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
        <!-- Tabs -->
        <div class="flex border-b border-slate-800 mb-8 overflow-x-auto">
            <button onclick="showTab('metrics')" id="tab-metrics" class="px-6 py-3 font-bold text-sm tab-active transition-all whitespace-nowrap">Metrics</button>
            <button onclick="showTab('calendar')" id="tab-calendar" class="px-6 py-3 font-bold text-sm text-slate-500 hover:text-slate-300 transition-all whitespace-nowrap">Race Calendar</button>
            <button onclick="showTab('cache')" id="tab-cache" class="px-6 py-3 font-bold text-sm text-slate-500 hover:text-slate-300 transition-all whitespace-nowrap">Cache Inspector</button>
            <button onclick="showTab('seeding')" id="tab-seeding" class="px-6 py-3 font-bold text-sm text-slate-500 hover:text-slate-300 transition-all whitespace-nowrap">Seeding</button>
            <button onclick="showTab('docs')" id="tab-docs" class="px-6 py-3 font-bold text-sm text-slate-500 hover:text-slate-300 transition-all whitespace-nowrap">API Docs</button>
        </div>

        <!-- Metrics Tab -->
        <div id="content-metrics" class="tab-content">
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

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <div class="lg:col-span-2 bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                    <h3 class="text-lg font-bold mb-4">Traffic Summary</h3>
                    <div id="traffic-info" class="text-slate-400">Loading metrics history...</div>
                </div>
                <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                    <h3 class="text-lg font-bold mb-4">Service Info</h3>
                    <div class="space-y-3 text-sm">
                        <div class="flex justify-between py-2 border-b border-slate-800">
                            <span class="text-slate-500">Uptime</span>
                            <span id="stat-uptime" class="font-mono">00:00:00</span>
                        </div>
                        <div class="flex justify-between py-2 border-b border-slate-800">
                            <span class="text-slate-500">Source API</span>
                            <span class="text-red-400 font-bold">OpenF1</span>
                        </div>
                        <div class="flex justify-between py-2">
                            <span class="text-slate-500">Environment</span>
                            <span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] font-bold uppercase">Production</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Race Calendar Tab -->
        <div id="content-calendar" class="tab-content hidden space-y-6">
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-2xl font-bold tracking-tight">F1 Race Calendar</h2>
                <div class="flex items-center gap-4">
                    <select id="calendar-year-select" onchange="refreshCalendar()" class="bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-sm font-bold text-slate-200 outline-none focus:border-red-500 transition-all">
                        <option value="2025">2025 Season</option>
                        <option value="2024" selected>2024 Season</option>
                        <option value="2023">2023 Season</option>
                    </select>
                    <button onclick="refreshCalendar()" class="p-2 hover:bg-slate-800 rounded-lg transition-all text-slate-400 hover:text-red-500" title="Refresh Calendar">
                        <i data-lucide="refresh-cw" class="w-5 h-5" id="refresh-icon-calendar"></i>
                    </button>
                </div>
            </div>
            <div id="calendar-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <!-- Meetings will be loaded here -->
                <div class="col-span-full py-20 text-center text-slate-500 animate-pulse">
                    <i data-lucide="calendar" class="w-12 h-12 mx-auto mb-4 opacity-20"></i>
                    <p>Loading the F1 calendar...</p>
                </div>
            </div>
        </div>

        <!-- Cache Inspector Tab -->
        <div id="content-cache" class="tab-content hidden">
            <div class="bg-slate-900/50 border border-slate-800 rounded-2xl overflow-hidden">
                <div class="p-6 border-b border-slate-800 flex justify-between items-center">
                    <h3 class="text-lg font-bold">Redis Cache Keys</h3>
                    <button onclick="refreshCacheKeys()" class="p-2 hover:bg-slate-800 rounded-lg transition-all">
                        <i data-lucide="refresh-cw" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="p-0 max-h-[600px] overflow-y-auto" id="cache-key-list">
                    <div class="p-8 text-center text-slate-500 italic">No keys found or Redis disconnected.</div>
                </div>
            </div>
        </div>

        <!-- Seeding Tab -->
        <div id="content-seeding" class="tab-content hidden">
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                    <h3 class="text-lg font-bold mb-4">Manual Seeding</h3>
                    <p class="text-sm text-slate-400 mb-6">Trigger historical data caching for specific years. This fetches all meetings and basic session data.</p>
                    
                    <div class="space-y-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-500 uppercase mb-2">Target Years (comma separated)</label>
                            <input type="text" id="seed-years" value="2023,2024" class="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-slate-200 focus:outline-none focus:border-red-500 transition-all">
                        </div>
                        <div class="flex gap-4">
                            <button onclick="triggerSeed()" id="btn-start-seed" class="flex-1 bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded-xl transition-all">Start Seeding</button>
                            <button onclick="stopSeed()" id="btn-stop-seed" class="flex-1 bg-slate-800 hover:bg-slate-700 text-white font-bold py-2 px-4 rounded-xl transition-all">Stop</button>
                        </div>
                    </div>
                </div>

                <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                    <h3 class="text-lg font-bold mb-4">Seeding Status</h3>
                    <div id="seeding-status-container" class="space-y-6">
                        <div class="text-center p-8 text-slate-500 italic">Idle</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- API Docs Tab -->
        <div id="content-docs" class="tab-content hidden">
            <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6">
                <h3 class="text-lg font-bold mb-6">Endpoint Documentation</h3>
                <div class="space-y-4">
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] font-bold uppercase">GET</span>
                            <code class="text-blue-400 font-bold">/meetings?year=2024</code>
                        </div>
                        <p class="text-xs text-slate-500">Fetch all F1 meetings for a specific year.</p>
                    </div>
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] font-bold uppercase">GET</span>
                            <code class="text-blue-400 font-bold">/sessions?meeting_key=1234</code>
                        </div>
                        <p class="text-xs text-slate-500">Fetch sessions for a given meeting.</p>
                    </div>
                    <div class="p-4 bg-slate-950 rounded-xl border border-slate-800">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px] font-bold uppercase">GET</span>
                            <code class="text-blue-400 font-bold">/{data_type}?session_key=5678</code>
                        </div>
                        <p class="text-xs text-slate-500">Proxy dynamic data. Valid types: weather, positions, drivers, laps, race_control, location, car_data, stints, intervals, pit.</p>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <script>
        lucide.createIcons();

        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.remove('tab-active', 'text-slate-200'));
            document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.add('text-slate-500'));
            
            document.getElementById('content-' + tabId).classList.remove('hidden');
            document.getElementById('tab-' + tabId).classList.add('tab-active', 'text-slate-200');
            document.getElementById('tab-' + tabId).classList.remove('text-slate-500');

            if (tabId === 'cache') refreshCacheKeys();
        }

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

                if (data.metrics_history && data.metrics_history.length > 0) {
                    const last = data.metrics_history[data.metrics_history.length - 1];
                    document.getElementById('traffic-info').innerHTML = `
                        <div class="flex flex-col gap-4">
                            <div class="text-sm">Last Snapshot: <span class="text-slate-200">${new Date(last.timestamp).toLocaleString()}</span></div>
                            <div class="grid grid-cols-2 gap-4">
                                <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Total Hits Recorded</div>
                                    <div class="text-xl font-bold text-blue-400">${last.total_requests}</div>
                                </div>
                                <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Efficiency</div>
                                    <div class="text-xl font-bold text-emerald-400">${((last.cache_hits / (last.total_requests || 1)) * 100).toFixed(1)}%</div>
                                </div>
                            </div>
                        </div>
                    `;
                }
            } catch (e) {
                console.error('Failed to fetch metrics', e);
            }
        }

        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.remove('tab-active', 'text-slate-200'));
            document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.add('text-slate-500'));
            
            document.getElementById('content-' + tabId).classList.remove('hidden');
            document.getElementById('tab-' + tabId).classList.add('tab-active', 'text-slate-200');
            document.getElementById('tab-' + tabId).classList.remove('text-slate-500');

            if (tabId === 'cache') refreshCacheKeys();
            if (tabId === 'calendar') refreshCalendar();
        }

        async function refreshCalendar() {
            const year = document.getElementById('calendar-year-select').value;
            const listEl = document.getElementById('calendar-list');
            const icon = document.getElementById('refresh-icon-calendar');
            
            if (icon) icon.classList.add('animate-spin');
            
            try {
                const resp = await fetch(`/meetings?year=${year}`);
                const meetings = await resp.json();
                
                if (!meetings || meetings.length === 0) {
                    listEl.innerHTML = `
                        <div class="col-span-full py-20 text-center text-slate-500 border border-dashed border-slate-800 rounded-3xl">
                            <i data-lucide="frown" class="w-12 h-12 mx-auto mb-4 opacity-20"></i>
                            <p>No meetings found for ${year} in cache.</p>
                            <p class="text-xs mt-2">Try seeding this year in the Seeding tab.</p>
                        </div>
                    `;
                } else {
                    // Sort by date (OpenF1 returns them mostly sorted, but let's be sure)
                    meetings.sort((a, b) => new Date(a.date_start) - new Date(b.date_start));
                    
                    let html = '';
                    meetings.forEach(m => {
                        const startDate = new Date(m.date_start);
                        const dateStr = startDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                        
                        html += `
                            <div class="bg-slate-900/50 border border-slate-800 rounded-2xl overflow-hidden hover:border-slate-700 transition-all group flex flex-col">
                                <div class="p-5 flex-1">
                                    <div class="flex justify-between items-start mb-4">
                                        <div class="bg-red-500/10 text-red-500 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider">
                                            Round ${m.meeting_key % 100}
                                        </div>
                                        <div class="text-slate-500 text-xs font-mono font-bold">${dateStr}</div>
                                    </div>
                                    <h3 class="text-lg font-bold text-white group-hover:text-red-400 transition-colors line-clamp-1">${m.meeting_name}</h3>
                                    <p class="text-slate-500 text-sm mb-4">${m.location}, ${m.country_name}</p>
                                    
                                    <div id="sessions-container-${m.meeting_key}" class="space-y-2 mt-4 pt-4 border-t border-slate-800/50">
                                        <button onclick="fetchSessionsForMeeting(${m.meeting_key})" class="w-full py-2 text-xs font-bold text-slate-400 hover:text-white bg-slate-800/30 hover:bg-slate-800 rounded-lg transition-all flex items-center justify-center gap-2">
                                            <i data-lucide="list" class="w-3.5 h-3.5"></i>
                                            Show Sessions
                                        </button>
                                    </div>
                                </div>
                            </div>
                        `;
                    });
                    listEl.innerHTML = html;
                }
            } catch (e) {
                listEl.innerHTML = `<div class="col-span-full py-20 text-center text-red-400">Failed to load calendar: ${e.message}</div>`;
            } finally {
                if (icon) icon.classList.remove('animate-spin');
                lucide.createIcons();
            }
        }

        async function fetchSessionsForMeeting(meetingKey) {
            const container = document.getElementById(`sessions-container-${meetingKey}`);
            container.innerHTML = '<div class="py-4 text-center text-slate-600 animate-pulse text-[10px] font-bold uppercase tracking-widest">Fetching Sessions...</div>';
            
            try {
                const resp = await fetch(`/sessions?meeting_key=${meetingKey}`);
                const sessions = await resp.json();
                
                if (!sessions || sessions.length === 0) {
                    container.innerHTML = '<div class="text-[10px] text-slate-500 italic py-2">No sessions found in cache.</div>';
                } else {
                    // Sort sessions by date
                    sessions.sort((a, b) => new Date(a.date_start) - new Date(b.date_start));
                    
                    let html = '<div class="space-y-2">';
                    sessions.forEach(s => {
                        html += `
                            <div class="p-3 bg-slate-950/50 border border-slate-800 rounded-xl hover:border-slate-700 transition-all cursor-pointer group/session" onclick="viewSessionData(${s.session_key})">
                                <div class="flex justify-between items-center">
                                    <div>
                                        <div class="text-xs font-bold text-slate-200 group-hover/session:text-red-400 transition-colors">${s.session_name}</div>
                                        <div class="text-[9px] text-slate-500 font-mono">${new Date(s.date_start).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</div>
                                    </div>
                                    <i data-lucide="chevron-right" class="w-3.5 h-3.5 text-slate-700 group-hover/session:text-red-500 transition-all"></i>
                                </div>
                            </div>
                        `;
                    });
                    html += '</div>';
                    container.innerHTML = html;
                    lucide.createIcons();
                }
            } catch (e) {
                container.innerHTML = `<div class="text-[10px] text-red-500 py-2">Error: ${e.message}</div>`;
            }
        }

        async function viewSessionData(sessionKey) {
            // Placeholder for deep session data view
            // For now, let's just show a quick overview in an alert or a future modal
            try {
                const resp = await fetch(`/drivers?session_key=${sessionKey}`);
                const drivers = await resp.json();
                
                let driverList = Array.isArray(drivers) ? drivers.map(d => `${d.broadcast_name} (${d.team_name})`).join('\n') : "No driver data";
                if (driverList.length > 300) driverList = driverList.substring(0, 300) + "...";
                
                alert(`Session Details (Key: ${sessionKey})\n\nDrivers Present:\n${driverList}`);
            } catch (e) {
                alert(`Session: ${sessionKey}\nError fetching driver list: ${e.message}`);
            }
        }

        async function refreshCacheKeys() {
            const listEl = document.getElementById('cache-key-list');
            listEl.innerHTML = '<div class="p-8 text-center text-slate-500 animate-pulse">Scanning Redis...</div>';
            try {
                const resp = await fetch('/cache_keys');
                const data = await resp.json();
                if (data.count === 0) {
                    listEl.innerHTML = '<div class="p-8 text-center text-slate-500 italic">No keys found in Redis.</div>';
                    return;
                }
                let html = '<div class="divide-y divide-slate-800">';
                data.keys.forEach(key => {
                    html += `
                        <div class="p-4 flex justify-between items-center hover:bg-slate-900/50 group transition-all">
                            <code class="text-sm text-blue-400 truncate mr-4">${key}</code>
                            <button onclick="viewCacheData('${key}')" class="flex-shrink-0 text-[10px] font-bold uppercase text-slate-500 group-hover:text-slate-200 transition-all">View Data</button>
                        </div>
                    `;
                });
                html += '</div>';
                listEl.innerHTML = html;
            } catch (e) {
                listEl.innerHTML = '<div class="p-8 text-center text-red-500 italic">Failed to fetch cache keys.</div>';
            }
        }

        async function viewCacheData(key) {
            try {
                const resp = await fetch('/cache_data/' + encodeURIComponent(key));
                const data = await resp.json();
                alert('Data for ' + key + ':\n' + JSON.stringify(data).substring(0, 500) + '...');
            } catch (e) {
                alert('Failed to fetch data');
            }
        }

        async function fetchSeedingStatus() {
            try {
                const resp = await fetch('/seed_status');
                const data = await resp.json();
                const container = document.getElementById('seeding-status-container');
                
                if (!data.is_running && !data.end_time) {
                    container.innerHTML = '<div class="text-center p-8 text-slate-500 italic">No seeding task has been run yet.</div>';
                    return;
                }

                const progress = data.total_meetings > 0 ? (data.processed_meetings / data.total_meetings) * 100 : 0;
                
                container.innerHTML = `
                    <div class="flex justify-between items-center">
                        <span class="text-sm font-bold ${data.is_running ? 'text-emerald-500 animate-pulse' : 'text-slate-400'}">
                            ${data.is_running ? 'Seeding in Progress...' : 'Completed'}
                        </span>
                        <span class="text-xs text-slate-500">${new Date(data.start_time).toLocaleTimeString()}</span>
                    </div>
                    
                    <div class="w-full bg-slate-950 rounded-full h-4 border border-slate-800 overflow-hidden">
                        <div class="bg-red-600 h-full transition-all duration-500" style="width: ${progress}%"></div>
                    </div>
                    
                    <div class="grid grid-cols-2 gap-4">
                        <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Current Year</div>
                            <div class="text-xl font-bold text-slate-200">${data.current_year || '---'}</div>
                        </div>
                        <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Progress</div>
                            <div class="text-xl font-bold text-slate-200">${data.processed_meetings} / ${data.total_meetings}</div>
                        </div>
                    </div>
                    
                    ${data.errors > 0 ? `<div class="p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">Errors encountered: ${data.errors}</div>` : ''}
                `;

                document.getElementById('btn-start-seed').disabled = data.is_running;
                document.getElementById('btn-start-seed').className = data.is_running ? 
                    'flex-1 bg-slate-800 text-slate-500 font-bold py-2 px-4 rounded-xl cursor-not-allowed' : 
                    'flex-1 bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded-xl transition-all';

            } catch (e) {
                console.error('Failed to fetch seeding status', e);
            }
        }

        async function triggerSeed() {
            const years = document.getElementById('seed-years').value;
            await fetch('/seed_history?years=' + encodeURIComponent(years), { method: 'POST' });
            fetchSeedingStatus();
        }

        async function stopSeed() {
            await fetch('/stop_seeding', { method: 'POST' });
            fetchSeedingStatus();
        }

        setInterval(fetchMetrics, 5000);
        setInterval(fetchSeedingStatus, 3000);
        fetchMetrics();
        fetchSeedingStatus();
    </script>
</body>
</html>
"""

@app.get("/metrics")
async def get_metrics():
    uptime = datetime.now(timezone.utc) - _start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    current_metrics = _local_metrics
    if r:
        try:
            m_data = r.get(METRICS_KEY)
            if m_data:
                current_metrics = json.loads(m_data)
        except: pass

    return {
        "total_requests": current_metrics["total_requests"],
        "cache_hits": current_metrics["cache_hits"],
        "cache_misses": current_metrics["cache_misses"],
        "openf1_errors": current_metrics["openf1_errors"],
        "redis_connected": r is not None,
        "uptime": f"{hours:02}:{minutes:02}:{seconds:02}",
        "metrics_history": _local_metrics_history
    }

@app.get("/cache_keys")
async def get_cache_keys():
    """List all keys in Redis if available."""
    if not r:
        return {"keys": [], "count": 0}
    try:
        keys = r.keys("f1_*")
        return {"keys": sorted(keys), "count": len(keys)}
    except:
        return {"keys": [], "count": 0}

@app.get("/cache_data/{key}")
async def get_cache_data_endpoint(key: str):
    """View data for a specific cache key."""
    data = get_cached_data(key)
    if data:
        return data
    raise HTTPException(status_code=404, detail="Key not found")

@app.get("/seed_status")
async def get_seed_status():
    """Endpoint to check the status of historical seeding."""
    if r:
        try:
            data = r.get(SEEDING_STATUS_KEY)
            if data:
                return json.loads(data)
        except: pass
    return _local_seeding_status

@app.post("/seed_history")
async def trigger_seed_history(years: str = "2023,2024"):
    """Endpoint to manually trigger historical data seeding."""
    status = await get_seed_status()
    if status.get("is_running"):
        return {"status": "Seeding already in progress"}
    
    global _stop_seeding_requested
    _stop_seeding_requested = False
    if r:
        try: r.delete(SEEDING_STOP_SIGNAL_KEY)
        except: pass
    
    year_list = [int(y.strip()) for y in years.split(",") if y.strip().isdigit()]
    
    seeding_thread = threading.Thread(target=seed_historical_f1_data, args=(year_list,), daemon=True)
    seeding_thread.start()
    return {"status": "Historical seeding started", "years": year_list}

@app.post("/stop_seeding")
async def stop_seeding():
    """Endpoint to stop the historical seeding."""
    global _stop_seeding_requested
    _stop_seeding_requested = True
    if r:
        try: r.set(SEEDING_STOP_SIGNAL_KEY, "true")
        except: pass
    return {"status": "Stop signal sent"}

def seed_historical_f1_data(years=[2023, 2024]):
    """Background task to seed historical F1 data."""
    global _local_seeding_status
    
    status = {
        "is_running": True,
        "current_year": None,
        "total_meetings": 0,
        "processed_meetings": 0,
        "errors": 0,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None
    }
    
    def update_status(new_status):
        nonlocal status
        status.update(new_status)
        if r:
            try: r.set(SEEDING_STATUS_KEY, json.dumps(status))
            except: pass
    
    update_status({})
    
    try:
        for year in years:
            if _stop_seeding_requested or (r and r.get(SEEDING_STOP_SIGNAL_KEY)):
                break
                
            update_status({"current_year": year})
            
            # Fetch meetings for year
            url = f"{OPENF1_BASE_URL}/meetings?year={year}"
            resp = requests.get(url)
            if resp.status_code != 200:
                update_status({"errors": status["errors"] + 1})
                continue
            
            meetings = resp.json()
            set_cached_data(f"f1_meetings_{year}", meetings, ttl=86400) # 24h for seed
            
            update_status({"total_meetings": status["total_meetings"] + len(meetings)})
            
            for meeting in meetings:
                if _stop_seeding_requested or (r and r.get(SEEDING_STOP_SIGNAL_KEY)):
                    break
                    
                m_key = meeting['meeting_key']
                # Fetch sessions for meeting
                s_url = f"{OPENF1_BASE_URL}/sessions?meeting_key={m_key}"
                s_resp = requests.get(s_url)
                if s_resp.status_code == 200:
                    sessions = s_resp.json()
                    set_cached_data(f"f1_sessions_m{m_key}_sNone", sessions, ttl=86400)
                    
                    # Fetch basic data for each session (Drivers, Laps)
                    for session in sessions:
                        s_key = session['session_key']
                        for dtype in ['drivers', 'laps', 'stints']:
                            d_url = f"{OPENF1_BASE_URL}/{dtype}?session_key={s_key}"
                            d_resp = requests.get(d_url)
                            if d_resp.status_code == 200:
                                set_cached_data(f"f1_{dtype}_sk{s_key}", d_resp.json(), ttl=86400)
                            time.sleep(0.5) # Prevent rate limit during seeding
                
                update_status({"processed_meetings": status["processed_meetings"] + 1})
                time.sleep(1)
                
    except Exception as e:
        print(f"Seeding error: {e}")
        update_status({"errors": status["errors"] + 1})
    finally:
        update_status({
            "is_running": False, 
            "end_time": datetime.now(timezone.utc).isoformat()
        })

def start_background_worker():
    def run():
        # Wait a bit for the app to start
        time.sleep(5)
        print("Starting F1 API background worker...")
        
        last_run = {
            "metrics": 0,
            "meetings": 0
        }
        
        while True:
            try:
                now = time.time()
                
                # Snapshot metrics (every 60s)
                if now - last_run["metrics"] >= 60:
                    current_metrics = _local_metrics.copy()
                    if r:
                        m_data = r.get(METRICS_KEY)
                        if m_data: current_metrics = json.loads(m_data)
                    
                    snapshot = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "total_requests": current_metrics["total_requests"],
                        "cache_hits": current_metrics["cache_hits"]
                    }
                    _local_metrics_history.append(snapshot)
                    if len(_local_metrics_history) > 100:
                        _local_metrics_history.pop(0)
                    
                    if r:
                        try: r.set(METRICS_HISTORY_KEY, json.dumps(_local_metrics_history))
                        except: pass
                    last_run["metrics"] = now
                
                # Refresh current year meetings (every 30m)
                if now - last_run["meetings"] >= 1800:
                    year = datetime.now().year
                    url = f"{OPENF1_BASE_URL}/meetings?year={year}"
                    resp = requests.get(url)
                    if resp.status_code == 200:
                        set_cached_data(f"f1_meetings_{year}", resp.json(), ttl=3600)
                    last_run["meetings"] = now
                    
            except Exception as e:
                print(f"Background worker error: {e}")
            
            time.sleep(10)
            
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

# Start the background worker
start_background_worker()

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
