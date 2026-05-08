import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { 
  Monitor, Cpu, HardDrive, Activity, RefreshCw, Power, 
  AlertTriangle, Settings, Search, Thermometer, Zap, Plus, X, Check,
  Wifi, ShieldCheck, Info, Radar, Music, Radio, Terminal, Send, Cpu as Cable,
  Play, Command, Loader2, ShieldAlert, Camera, Heart, LayoutGrid, Settings2,
  Clock, Gauge, ChevronRight, ToggleLeft, ToggleRight, Download, Trash2, Save
} from 'lucide-react';

const AGENT_PORT = 5001;
const STORAGE_KEY = 'museum_monitor_stations';
const SUBNET_KEY = 'museum_monitor_subnet';
const REFRESH_RATE_KEY = 'museum_monitor_refresh_rate';
const CONFIG_OPTIONS_KEY = 'museum_monitor_config_options';
const MASTER_IP_KEY = 'museum_monitor_master_ip';
const RESTART_TIMEOUT_MS = 5 * 60 * 1000; 

export default function App() {
  const [adoptedIps, setAdoptedIps] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : ['127.0.0.1'];
  });

  const [scanSubnet, setScanSubnet] = useState(() => {
    return localStorage.getItem(SUBNET_KEY) || '192.168.1';
  });

  const [refreshRate, setRefreshRate] = useState(() => {
    const saved = localStorage.getItem(REFRESH_RATE_KEY);
    return saved ? parseInt(saved) : 5000;
  });

  const [masterServiceIp, setMasterServiceIp] = useState(() => {
    return localStorage.getItem(MASTER_IP_KEY) || '';
  });

  const [configOptions, setConfigOptions] = useState(() => {
    const saved = localStorage.getItem(CONFIG_OPTIONS_KEY);
    return saved ? JSON.parse(saved) : {
      highFreq: true,
      smoothing: true,
      autoScreenshot: false
    };
  });

  const [stations, setStations] = useState([]);
  const [discoveredStations, setDiscoveredStations] = useState([]);
  const [selectedStationId, setSelectedStationId] = useState(null);
  const [vitals, setVitals] = useState({});
  const [apps, setApps] = useState({});
  const [presets, setPresets] = useState({}); 
  const [searchTerm, setSearchTerm] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [isScanning, setIsScanning] = useState(false);
  const [showManualControl, setShowManualControl] = useState(false);
  const [isPulsing, setIsPulsing] = useState(false); 
  const [notification, setNotification] = useState(null);
  const [showSettingsDropdown, setShowSettingsDropdown] = useState(false);
  const [activeSettingsPanel, setActiveSettingsPanel] = useState(null); 
  const [screenshot, setScreenshot] = useState(null);
  const [loadingScreenshot, setLoadingScreenshot] = useState(false);
  const [restartingApps, setRestartingApps] = useState({});
  
  const [controlTab, setControlTab] = useState('midi');
  const [controlPayload, setControlPayload] = useState({
    midi: { note: 60, velocity: 100, channel: 1 },
    osc: { path: '/exhibit/trigger', port: 8000, value: 1.0 },
    serial: { port: 'COM1', baud: 9600, message: 'ON' }
  });

  const stationsRef = useRef([]);

  useEffect(() => {
    const script = document.createElement("script");
    script.src = "https://cdn.tailwindcss.com";
    document.head.appendChild(script);
  }, []);

  const syncToMaster = useCallback(async (ipsToSync) => {
    if (!masterServiceIp) return;
    try {
      const response = await fetch(`http://${masterServiceIp}:5002/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ips: ipsToSync })
      });
      if (response.ok) {
        console.log("Master Service Synced Successfully");
      }
    } catch (err) {
      console.warn("Could not sync to Master Service IP provided.");
    }
  }, [masterServiceIp]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(adoptedIps));
    localStorage.setItem(SUBNET_KEY, scanSubnet);
    localStorage.setItem(REFRESH_RATE_KEY, refreshRate.toString());
    localStorage.setItem(CONFIG_OPTIONS_KEY, JSON.stringify(configOptions));
    localStorage.setItem(MASTER_IP_KEY, masterServiceIp);
    
    syncToMaster(adoptedIps);
  }, [adoptedIps, scanSubnet, refreshRate, configOptions, masterServiceIp, syncToMaster]);

  useEffect(() => {
    setStations(prev => {
      const existingIps = prev.map(s => s.ip);
      const newStations = adoptedIps
        .filter(ip => !existingIps.includes(ip))
        .map(ip => ({ 
          id: ip, 
          ip, 
          name: 'Connecting...', 
          location: 'Detecting...', 
          status: 'offline' 
        }));
      
      const filtered = prev.filter(s => adoptedIps.includes(s.ip));
      const result = [...filtered, ...newStations];
      stationsRef.current = result;
      return result;
    });
  }, [adoptedIps]);

  const showToast = (message, type = 'info') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 3000);
  };

  const fetchScreenshot = useCallback(async (ip, silent = false) => {
    if (!silent) setLoadingScreenshot(true);
    try {
      const response = await fetch(`http://${ip}:${AGENT_PORT}/action/screenshot`);
      const data = await response.json();
      if (data.image) {
        setScreenshot(`data:image/jpeg;base64,${data.image}`);
      }
    } catch (err) {
      if (!silent) console.error("Failed to fetch screenshot", err);
    } finally {
      if (!silent) setLoadingScreenshot(false);
    }
  }, []);

  const pollAgents = useCallback(async () => {
    const currentStations = stationsRef.current;
    if (currentStations.length === 0) return;

    const results = await Promise.all(currentStations.map(async (station) => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1500);
        const response = await fetch(`http://${station.ip}:${AGENT_PORT}/health`, { 
          signal: controller.signal 
        });
        clearTimeout(timeoutId);

        if (response.ok) {
          const data = await response.json();
          return {
            ip: station.ip,
            status: 'online',
            name: data.name || station.id,
            location: data.location || 'Unknown',
            vitals: data.vitals,
            apps: data.apps,
            presets: data.presets || []
          };
        }
      } catch (err) {}
      return { ip: station.ip, status: 'offline' };
    }));

    setStations(prev => {
      const next = prev.map(s => {
        const update = results.find(r => r.ip === s.ip);
        return update ? { ...s, ...update } : s;
      });
      stationsRef.current = next;
      return next;
    });

    const newVitals = {};
    const newApps = {};
    const newPresets = {};
    
    results.forEach(r => {
      if (r.status === 'online') {
        newVitals[r.ip] = r.vitals;
        newApps[r.ip] = r.apps;
        newPresets[r.ip] = r.presets;

        if (restartingApps[r.ip]) {
          const stationRestarts = restartingApps[r.ip];
          let updated = false;
          const nextRestarts = { ...stationRestarts };
          r.apps.forEach(app => {
            if (app.status === 'running' && nextRestarts[app.name]?.status === 'restarting') {
              delete nextRestarts[app.name];
              updated = true;
            }
          });
          if (updated) {
            setRestartingApps(prev => {
              const cleaned = { ...prev, [r.ip]: nextRestarts };
              if (Object.keys(nextRestarts).length === 0) delete cleaned[r.ip];
              return cleaned;
            });
          }
        }
      }
    });

    setVitals(prev => ({ ...prev, ...newVitals }));
    setApps(prev => ({ ...prev, ...newApps }));
    setPresets(prev => ({ ...prev, ...newPresets }));
    
    setLastUpdate(new Date().toLocaleTimeString());
    setIsPulsing(true);
    setTimeout(() => setIsPulsing(false), 800); 

    if (configOptions.autoScreenshot && selectedStationId) {
      const target = results.find(r => r.ip === selectedStationId && r.status === 'online');
      if (target) {
        fetchScreenshot(target.ip, true);
      }
    }
  }, [restartingApps, configOptions.autoScreenshot, selectedStationId, fetchScreenshot]);

  useEffect(() => {
    const hasActiveRestarts = Object.values(restartingApps).some(station => 
      Object.values(station).some(app => app.status === 'restarting')
    );
    
    const intervalTime = (configOptions.highFreq && hasActiveRestarts) 
      ? Math.min(2000, refreshRate) 
      : refreshRate;

    pollAgents();
    const interval = setInterval(pollAgents, intervalTime);
    return () => clearInterval(interval);
  }, [pollAgents, restartingApps, refreshRate, configOptions.highFreq]);

  const exportFleetLog = () => {
    const logData = {
      timestamp: new Date().toISOString(),
      fleetCount: adoptedIps.length,
      adoptedIps,
      stations: stations.map(s => ({
        name: s.name,
        ip: s.ip,
        status: s.status,
        location: s.location,
        vitals: vitals[s.ip] || null
      })),
      configuration: {
        refreshRate,
        configOptions,
        scanSubnet,
        masterServiceIp
      }
    };

    const blob = new Blob([JSON.stringify(logData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `fleet_export_${new Date().getTime()}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    showToast("Fleet Log Exported Successfully", "info");
  };

  const purgeCache = () => {
    localStorage.clear();
    window.location.reload(); 
  };

  const saveMasterConfig = () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(adoptedIps));
    localStorage.setItem(SUBNET_KEY, scanSubnet);
    localStorage.setItem(REFRESH_RATE_KEY, refreshRate.toString());
    localStorage.setItem(CONFIG_OPTIONS_KEY, JSON.stringify(configOptions));
    localStorage.setItem(MASTER_IP_KEY, masterServiceIp);
    
    syncToMaster(adoptedIps);
    showToast("Master Configuration Saved & Synced", "success");
  };

  const toggleOption = (opt) => {
    setConfigOptions(prev => ({ ...prev, [opt]: !prev[opt] }));
  };

  const scanForNewExhibits = async (isDeepScan = false) => {
    if (isScanning) return;
    setIsScanning(true);
    const discovered = [];
    const limit = isDeepScan ? 100 : 30;
    const range = Array.from({ length: limit - 1 }, (_, i) => i + 2); 
    const batchSize = 10;
    for (let i = 0; i < range.length; i += batchSize) {
      const batch = range.slice(i, i + batchSize);
      await Promise.all(batch.map(async (num) => {
        const testIp = `${scanSubnet}.${num}`;
        if (adoptedIps.includes(testIp)) return;
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 1000);
          const response = await fetch(`http://${testIp}:${AGENT_PORT}/health`, { signal: controller.signal });
          clearTimeout(timeoutId);
          if (response.ok) {
            const data = await response.json();
            discovered.push({ ip: testIp, name: data.name, location: data.location });
          }
        } catch (e) {}
      }));
    }
    setDiscoveredStations(prev => {
      const combined = [...prev, ...discovered];
      return combined.filter((v, i, a) => a.findIndex(t => t.ip === v.ip) === i);
    });
    setIsScanning(false);
  };

  const adoptStation = (ip) => {
    setAdoptedIps(prev => prev.includes(ip) ? prev : [...prev, ip]);
    setDiscoveredStations(prev => prev.filter(s => s.ip !== ip));
    showToast(`Station ${ip} Adopted`, "success");
  };

  const removeStation = (ip) => {
    setAdoptedIps(prev => prev.filter(item => item !== ip));
    if (selectedStationId === ip) setSelectedStationId(null);
    showToast(`Station ${ip} Removed`, "info");
  };

  const handleRemoteAction = async (stationId, action, payload = {}) => {
    const targetStation = stations.find(s => s.id === stationId);
    if (!targetStation) return;
    setIsRefreshing(true);
    
    if (action === 'restart-app') {
      setRestartingApps(prev => ({
        ...prev,
        [targetStation.ip]: {
          ...(prev[targetStation.ip] || {}),
          [payload.name]: { startTime: Date.now(), status: 'restarting' }
        }
      }));
    }

    let endpoint;
    if (['midi', 'osc', 'serial', 'preset'].includes(action)) {
      endpoint = `control/${action}`;
    } else {
      endpoint = action === 'reboot' ? 'reboot' : 'restart-app';
    }
    
    try {
      await fetch(`http://${targetStation.ip}:${AGENT_PORT}/action/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      showToast(`Command sent: ${action}`, "success");
    } catch (err) {
      showToast(`Action failed: ${err.message}`, "danger");
    } finally {
      setIsRefreshing(false);
    }
  };

  const selectedStation = useMemo(() => 
    stations.find(s => s.id === selectedStationId), 
    [stations, selectedStationId]
  );

  const filteredStations = stations.filter(s => 
    s.name?.toLowerCase().includes(searchTerm.toLowerCase()) || 
    s.id?.toLowerCase().includes(searchTerm.toLowerCase()) ||
    s.location?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8 font-sans selection:bg-blue-500/30">
      <header className="flex flex-col md:flex-row justify-between items-center gap-6 mb-10 pb-8 border-b border-slate-800">
        <div className="flex items-center gap-4">
          <div className="bg-blue-600 p-3 rounded-2xl shadow-lg shadow-blue-500/20">
            <ShieldCheck className="w-8 h-8 text-white" />
          </div>
          <div>
            <h1 className="text-3xl font-black tracking-tighter text-white">MUSEUM<span className="text-blue-500">MONITOR</span></h1>
            <p className="text-xs font-bold text-slate-500 uppercase tracking-widest text-nowrap">Exhibit Fleet Command</p>
          </div>
        </div>

        <div className="hidden lg:flex items-center gap-3 bg-slate-900/50 px-4 py-2 rounded-xl border border-slate-800 shadow-lg">
          <div className="relative">
            <Heart 
              className={`w-5 h-5 transition-all duration-300 ${isPulsing ? 'text-red-500 fill-red-500 scale-125' : 'text-slate-600'}`} 
            />
            {isPulsing && (
              <span className="absolute inset-0 animate-ping rounded-full bg-red-500/20" />
            )}
          </div>
          <div className="flex flex-col">
            <span className="text-[9px] font-black text-slate-500 uppercase tracking-widest leading-none mb-1 text-nowrap">Fleet Heartbeat</span>
            <span className="text-xs font-mono text-slate-300 leading-none">{lastUpdate || 'Initializing...'}</span>
          </div>
        </div>

        <div className="flex items-center gap-3 w-full md:w-auto">
          <div className="relative flex-1 md:flex-none">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input 
              type="text" 
              placeholder="Search fleet..." 
              className="bg-slate-900 border border-slate-800 rounded-lg py-2 pl-10 pr-4 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all w-full md:w-64"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          
          <div className="relative">
            <button 
              onClick={() => setShowSettingsDropdown(!showSettingsDropdown)}
              className={`p-2 rounded-lg border transition-all ${showSettingsDropdown || activeSettingsPanel ? 'bg-blue-600 border-blue-500 shadow-lg shadow-blue-500/20' : 'bg-slate-900 border-slate-800 hover:bg-slate-800'}`}
            >
              <Settings className={`w-5 h-5 ${showSettingsDropdown ? 'rotate-90' : ''} transition-transform duration-300`} />
            </button>

            {showSettingsDropdown && (
              <div className="absolute right-0 mt-2 w-64 bg-slate-900 border border-slate-800 rounded-xl shadow-2xl z-[100] overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                <div className="p-2 space-y-1">
                  <button 
                    onClick={() => { setActiveSettingsPanel('fleet'); setShowSettingsDropdown(false); }}
                    className="w-full flex items-center justify-between p-3 rounded-lg hover:bg-slate-800 transition-colors text-left group"
                  >
                    <div className="flex items-center gap-3">
                      <div className="bg-blue-500/10 p-1.5 rounded text-blue-400 group-hover:bg-blue-500 group-hover:text-white transition-all">
                        <LayoutGrid className="w-4 h-4" />
                      </div>
                      <span className="text-sm font-bold">Fleet Management</span>
                    </div>
                    <ChevronRight className="w-4 h-4 text-slate-600 group-hover:translate-x-0.5 transition-transform" />
                  </button>
                  <button 
                    onClick={() => { setActiveSettingsPanel('config'); setShowSettingsDropdown(false); }}
                    className="w-full flex items-center justify-between p-3 rounded-lg hover:bg-slate-800 transition-colors text-left group"
                  >
                    <div className="flex items-center gap-3">
                      <div className="bg-purple-500/10 p-1.5 rounded text-purple-400 group-hover:bg-purple-500 group-hover:text-white transition-all">
                        <Settings2 className="w-4 h-4" />
                      </div>
                      <span className="text-sm font-bold">System Config</span>
                    </div>
                    <ChevronRight className="w-4 h-4 text-slate-600 group-hover:translate-x-0.5 transition-transform" />
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      {activeSettingsPanel === 'fleet' && (
        <div className="mb-6 bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl animate-in fade-in zoom-in duration-200">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-bold flex items-center gap-2 text-blue-400">
              <LayoutGrid className="w-5 h-5" />
              Fleet Management
            </h2>
            <button onClick={() => setActiveSettingsPanel(null)} className="bg-slate-800 p-1.5 rounded-full text-slate-500 hover:text-white transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-4">
              <label className="block text-xs font-bold text-slate-500 uppercase tracking-widest">Discovery Engine</label>
              <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-4">
                <div className="space-y-2">
                  <span className="text-xs text-slate-400 font-medium">Subnet Mask</span>
                  <input 
                    type="text" 
                    value={scanSubnet}
                    onChange={(e) => setScanSubnet(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:ring-1 focus:ring-blue-500 outline-none"
                    placeholder="e.g. 192.168.1"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2 pt-2">
                  <button onClick={() => scanForNewExhibits(false)} disabled={isScanning} className="bg-slate-800 hover:bg-slate-700 disabled:opacity-50 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-2 border border-slate-700">
                    <RefreshCw className={`w-3 h-3 ${isScanning ? 'animate-spin' : ''}`} /> Quick Scan
                  </button>
                  <button onClick={() => scanForNewExhibits(true)} disabled={isScanning} className="bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 border border-blue-500/30 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-2">
                    <Radar className={`w-3 h-3 ${isScanning ? 'animate-spin' : ''}`} /> Force Deep Scan
                  </button>
                </div>
              </div>
            </div>
            <div className="space-y-4">
              <label className="block text-xs font-bold text-slate-500 uppercase tracking-widest">Active Fleet ({adoptedIps.length})</label>
              <div className="max-h-48 overflow-y-auto bg-slate-950 rounded-xl border border-slate-800 p-1 space-y-1">
                {adoptedIps.map(ip => (
                  <div key={ip} className="flex justify-between items-center text-xs px-3 py-2 bg-slate-900/50 rounded-lg border border-slate-800 group hover:border-slate-700">
                    <div className="flex items-center gap-2">
                      <div className={`w-1.5 h-1.5 rounded-full ${stations.find(s=>s.ip === ip)?.status === 'online' ? 'bg-emerald-500' : 'bg-slate-600'}`} />
                      <span className="font-mono text-slate-300">{ip}</span>
                    </div>
                    <button onClick={() => removeStation(ip)} className="text-slate-500 hover:text-red-400 p-1 transition-colors">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {activeSettingsPanel === 'config' && (
        <div className="mb-6 bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl animate-in fade-in zoom-in duration-200">
          <div className="flex justify-between items-center mb-6 text-purple-400">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <Settings2 className="w-5 h-5" />
              System Configuration
            </h2>
            <button onClick={() => setActiveSettingsPanel(null)} className="bg-slate-800 p-1.5 rounded-full text-slate-500 hover:text-white transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div className="space-y-4 p-4 bg-slate-950 rounded-xl border border-slate-800">
              <div className="flex items-center gap-2 text-blue-400 mb-2">
                <Clock className="w-4 h-4" />
                <span className="text-xs font-black uppercase tracking-widest">Fleet Refresh</span>
              </div>
              <div className="space-y-4">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-400 font-medium">Heartbeat Rate</span>
                  <span className="text-white font-mono">{refreshRate}ms</span>
                </div>
                <input 
                  type="range" min="1000" max="30000" step="500"
                  value={refreshRate}
                  onChange={(e) => setRefreshRate(parseInt(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
                />
              </div>
            </div>

            <div className="space-y-4 p-4 bg-slate-950 rounded-xl border border-slate-800">
              <div className="flex items-center gap-2 text-red-400 mb-2">
                <ShieldAlert className="w-4 h-4" />
                <span className="text-xs font-black uppercase tracking-widest">Master Service</span>
              </div>
              <div className="space-y-2">
                <label className="text-[10px] text-slate-500 font-bold uppercase">Master Service IP</label>
                <input 
                  type="text" 
                  value={masterServiceIp}
                  onChange={(e) => setMasterServiceIp(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs focus:ring-1 focus:ring-red-500 outline-none"
                  placeholder="e.g. 192.168.1.50"
                />
                <p className="text-[9px] text-slate-500 italic">Target IP for 24/7 background syncing.</p>
              </div>
            </div>

            <div className="space-y-4 p-4 bg-slate-950 rounded-xl border border-slate-800">
              <div className="flex items-center gap-2 text-emerald-400 mb-2">
                <Gauge className="w-4 h-4" />
                <span className="text-xs font-black uppercase tracking-widest">Performance</span>
              </div>
              <div className="space-y-3">
                 <button onClick={() => toggleOption('highFreq')} className="w-full flex items-center justify-between text-left group">
                    <span className="text-xs text-slate-400">High-Freq Polling</span>
                    {configOptions.highFreq ? <ToggleRight className="text-blue-500" /> : <ToggleLeft className="text-slate-600" />}
                 </button>
                 <button onClick={() => toggleOption('smoothing')} className="w-full flex items-center justify-between text-left group">
                    <span className="text-xs text-slate-400">Vitals smoothing</span>
                    {configOptions.smoothing ? <ToggleRight className="text-emerald-500" /> : <ToggleLeft className="text-slate-600" />}
                 </button>
                 <button onClick={() => toggleOption('autoScreenshot')} className="w-full flex items-center justify-between text-left group">
                    <span className="text-xs text-slate-400">Auto-Screenshot</span>
                    {configOptions.autoScreenshot ? <ToggleRight className="text-amber-500" /> : <ToggleLeft className="text-slate-600" />}
                 </button>
              </div>
            </div>

            <div className="space-y-4 p-4 bg-slate-950 rounded-xl border border-slate-800">
              <div className="flex items-center gap-2 text-amber-400 mb-2">
                <ShieldCheck className="w-4 h-4" />
                <span className="text-xs font-black uppercase tracking-widest">Actions</span>
              </div>
              <div className="space-y-2">
                 <button onClick={exportFleetLog} className="w-full py-2 bg-slate-900 border border-slate-800 rounded text-[10px] font-bold uppercase hover:bg-slate-800 flex items-center justify-center gap-2">
                   <Download className="w-3 h-3" /> Export Log
                 </button>
                 <button onClick={purgeCache} className="w-full py-2 bg-slate-900 border border-slate-800 rounded text-[10px] font-bold uppercase hover:bg-red-500/10 hover:text-red-400 flex items-center justify-center gap-2">
                   <Trash2 className="w-3 h-3" /> Purge Cache
                 </button>
                 <button onClick={saveMasterConfig} className="w-full py-2 bg-blue-600 text-white rounded text-[10px] font-bold uppercase flex items-center justify-center gap-2">
                   <Save className="w-3 h-3" /> Save All
                 </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-4 space-y-4 h-[calc(100vh-250px)] overflow-y-auto pr-2 custom-scrollbar">
          {filteredStations.map(station => (
            <StationCard 
              key={station.id}
              station={station}
              vitals={vitals[station.id]}
              isSelected={selectedStationId === station.id}
              onClick={() => setSelectedStationId(station.id)}
              smoothing={configOptions.smoothing}
            />
          ))}
          {filteredStations.length === 0 && (
            <div className="py-20 text-center text-slate-600 border border-dashed border-slate-800 rounded-2xl bg-slate-900/20">
              <Monitor className="w-12 h-12 mx-auto mb-4 opacity-5" />
              <p className="text-sm italic">Fleet is empty or no matches found.</p>
            </div>
          )}
        </div>

        <div className="lg:col-span-8 bg-slate-900/50 border border-slate-800 rounded-2xl p-6 relative h-[calc(100vh-250px)] overflow-y-auto custom-scrollbar">
          {!selectedStation ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-600 space-y-4 italic">
              <ShieldCheck className="w-16 h-16 opacity-5" />
              <p>Select a station to begin remote management</p>
            </div>
          ) : (
            <div className="space-y-8 animate-in fade-in slide-in-from-right-4 duration-300">
              <div className="flex flex-col md:flex-row justify-between items-start gap-4 border-b border-slate-800 pb-6">
                <div className="space-y-1">
                  <div className="flex items-center gap-3">
                    <h2 className="text-2xl font-bold tracking-tight">{selectedStation.name}</h2>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase border ${selectedStation.status === 'online' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-slate-500/10 text-slate-400 border-slate-500/20'}`}>
                      {selectedStation.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-slate-500 text-xs font-mono">
                    <span className="flex items-center gap-1"><Wifi className="w-3 h-3"/> {selectedStation.ip}</span>
                    <span>•</span>
                    <span className="bg-slate-800 px-1.5 py-0.5 rounded text-slate-400 tracking-wider uppercase font-bold text-[9px]">{selectedStation.location}</span>
                  </div>
                </div>
                
                <div className="flex flex-wrap gap-2">
                  <button 
                    onClick={() => fetchScreenshot(selectedStation.ip)}
                    disabled={loadingScreenshot || selectedStation.status === 'offline'}
                    className={`flex items-center gap-2 px-3 py-2 border rounded-lg text-[11px] font-bold uppercase transition-all disabled:opacity-20 shadow-lg ${configOptions.autoScreenshot ? 'bg-amber-600/10 text-amber-400 border-amber-500/20' : 'bg-emerald-600/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-600 hover:text-white'}`}
                  >
                    {loadingScreenshot ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Camera className="w-3.5 h-3.5" />}
                    {configOptions.autoScreenshot ? 'Auto-Capturing' : 'Capture View'}
                  </button>
                  <button 
                    onClick={() => setShowManualControl(true)}
                    disabled={selectedStation.status === 'offline'}
                    className="flex items-center gap-2 px-3 py-2 bg-blue-600/10 text-blue-400 border border-blue-500/20 hover:bg-blue-600 hover:text-white rounded-lg text-[11px] font-bold uppercase transition-all disabled:opacity-20"
                  >
                    <Command className="w-3.5 h-3.5" />
                    Manual Control
                  </button>
                  <button 
                    onClick={() => handleRemoteAction(selectedStation.id, 'reboot')}
                    disabled={isRefreshing || selectedStation.status === 'offline'}
                    className="flex items-center gap-2 px-3 py-2 bg-red-600/10 text-red-500 border border-red-500/20 hover:bg-red-600 hover:text-white rounded-lg text-[11px] font-bold uppercase transition-all disabled:opacity-20"
                  >
                    <Power className="w-3.5 h-3.5" />
                    Remote Reboot
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <VitalMeter icon={<Cpu />} label="CPU" value={vitals[selectedStation.id]?.cpu} color="blue" smoothing={configOptions.smoothing} />
                <VitalMeter icon={<Activity />} label="RAM" value={vitals[selectedStation.id]?.ram} color="emerald" smoothing={configOptions.smoothing} />
                <VitalMeter icon={<Zap />} label="GPU" value={vitals[selectedStation.id]?.gpu} color="purple" smoothing={configOptions.smoothing} />
                <VitalMeter icon={<Thermometer />} label="TEMP" value={vitals[selectedStation.id]?.temp} unit="°C" color="orange" limit={75} smoothing={configOptions.smoothing} />
              </div>

              <div className="space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                   <h3 className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-blue-400/80">
                    <HardDrive className="w-4 h-4" />
                    App Watcher
                  </h3>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {apps[selectedStation.id]?.map((app, idx) => {
                    const restartStatus = restartingApps[selectedStation.id]?.[app.name]?.status;
                    const isRestarting = restartStatus === 'restarting';
                    return (
                      <div key={idx} className="flex items-center justify-between p-3 bg-slate-950/50 border border-slate-800/50 rounded-xl group hover:border-blue-500/30 transition-all">
                        <div className="flex items-center gap-3">
                          {isRestarting ? <Loader2 className="w-3 h-3 text-blue-400 animate-spin" /> : <div className={`w-2 h-2 rounded-full ${app.status === 'running' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.3)]' : 'bg-red-500'}`} />}
                          <div>
                            <p className="font-bold text-sm text-slate-200">{app.name}</p>
                            <p className="text-[10px] font-mono uppercase text-slate-500">{isRestarting ? 'Restarting...' : `${app.status} • ${app.uptime}`}</p>
                          </div>
                        </div>
                        <button onClick={() => handleRemoteAction(selectedStation.id, 'restart-app', { name: app.name })} className="opacity-0 group-hover:opacity-100 p-2 bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-all">
                          <RefreshCw className="w-4 h-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {showManualControl && selectedStation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl shadow-2xl overflow-hidden">
            <div className="flex items-center justify-between p-6 border-b border-slate-800">
              <h2 className="text-lg font-bold flex items-center gap-2 text-blue-400"><Command className="w-5 h-5" /> Manual Control</h2>
              <button onClick={() => setShowManualControl(false)} className="p-1.5 rounded-full bg-slate-800 text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-8 space-y-6">
              <div className="flex gap-4 border-b border-slate-800 pb-4">
                {['midi', 'osc', 'serial'].map(tab => (
                  <button key={tab} onClick={() => setControlTab(tab)} className={`text-[10px] font-black uppercase tracking-widest px-4 py-2 rounded-lg transition-all ${controlTab === tab ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-500'}`}>{tab}</button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2 py-4 text-center text-slate-500 text-sm italic">Configure {controlTab} payload above and send.</div>
                <button onClick={() => handleRemoteAction(selectedStation.id, controlTab, controlPayload[controlTab])} className="col-span-2 bg-blue-600 py-3 rounded-xl font-black uppercase tracking-widest hover:bg-blue-500 transition-all">Send Command</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {screenshot && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/90 backdrop-blur-md">
          <div className="relative max-w-5xl w-full bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-2xl">
            <div className="flex justify-between items-center p-4 border-b border-slate-800">
               <span className="text-xs font-bold uppercase tracking-widest text-emerald-400 flex items-center gap-2"><Camera className="w-4 h-4"/> Live Frame: {selectedStation?.name}</span>
               <button onClick={() => setScreenshot(null)} className="p-2 bg-slate-800 rounded-lg"><X className="w-4 h-4"/></button>
            </div>
            <img src={screenshot} alt="Preview" className="w-full h-auto max-h-[80vh] object-contain" />
          </div>
        </div>
      )}

      {notification && (
        <div className={`fixed bottom-8 left-1/2 -translate-x-1/2 z-[300] px-6 py-3 rounded-2xl border shadow-2xl flex items-center gap-3 animate-in slide-in-from-bottom duration-300 ${
          notification.type === 'success' ? 'bg-emerald-600 border-emerald-400 text-white' :
          notification.type === 'danger' ? 'bg-red-600 border-red-400 text-white' : 'bg-blue-600 border-blue-400 text-white'
        }`}>
          {notification.type === 'success' ? <Check className="w-5 h-5" /> : <Info className="w-5 h-5" />}
          <span className="font-bold text-sm">{notification.message}</span>
        </div>
      )}
    </div>
  );
}

function StationCard({ station, vitals, isSelected, onClick, smoothing }) {
  return (
    <div onClick={onClick} className={`p-4 rounded-xl border cursor-pointer transition-all active:scale-95 ${isSelected ? 'ring-2 ring-blue-500/50 border-blue-500/50 bg-slate-900 shadow-2xl' : 'border-slate-800 bg-slate-900/40 hover:border-slate-700'}`}>
      <div className="flex justify-between items-start mb-2">
        <div className="space-y-1">
          <h3 className="font-bold text-md text-slate-200">{station.name}</h3>
          <p className="text-[9px] text-blue-400 font-black uppercase tracking-[0.2em]">{station.location}</p>
        </div>
        <div className={`w-2 h-2 rounded-full ${station.status === 'online' ? 'bg-emerald-500' : 'bg-slate-700'}`} />
      </div>
      {station.status === 'online' && (
        <div className="flex gap-4 mt-4">
          <div className="flex-1">
            <div className="flex justify-between text-[8px] font-black uppercase text-slate-500 mb-1"><span>CPU</span><span>{Math.round(vitals?.cpu || 0)}%</span></div>
            <div className="h-1 bg-slate-800 rounded-full overflow-hidden"><div className={`h-full bg-blue-500 ${smoothing ? 'transition-all duration-700' : ''}`} style={{ width: `${vitals?.cpu || 0}%` }} /></div>
          </div>
          <div className="flex-1">
            <div className="flex justify-between text-[8px] font-black uppercase text-slate-500 mb-1"><span>TEMP</span><span>{Math.round(vitals?.temp || 0)}°C</span></div>
            <div className="h-1 bg-slate-800 rounded-full overflow-hidden"><div className={`h-full ${vitals?.temp > 70 ? 'bg-orange-500' : 'bg-emerald-500'} ${smoothing ? 'transition-all duration-700' : ''}`} style={{ width: `${Math.min(vitals?.temp || 0, 100)}%` }} /></div>
          </div>
        </div>
      )}
    </div>
  );
}

function VitalMeter({ icon, label, value = 0, unit = '%', color, limit = 90, smoothing }) {
  const isDanger = value > limit;
  const colors = {
    blue: 'text-blue-400 bg-blue-400/5 border-blue-400/20',
    emerald: 'text-emerald-400 bg-emerald-400/5 border-emerald-400/20',
    purple: 'text-purple-400 bg-purple-400/5 border-purple-400/20',
    orange: 'text-orange-400 bg-orange-400/5 border-orange-400/20',
  }[color];
  
  return (
    <div className={`p-4 rounded-2xl border ${colors} ${isDanger ? 'animate-pulse border-red-500/50 bg-red-500/5' : ''}`}>
      <div className="flex items-center gap-2 mb-2">{React.cloneElement(icon, { className: 'w-3 h-3' })}<span className="text-[10px] font-black uppercase tracking-widest opacity-80">{label}</span></div>
      <div className="flex items-baseline gap-1"><span className="text-3xl font-black tabular-nums">{Math.round(value)}</span><span className="text-xs font-bold opacity-40">{unit}</span></div>
      <div className="mt-2 h-1 bg-white/5 rounded-full overflow-hidden">
         <div className={`h-full bg-current opacity-30 ${smoothing ? 'transition-all duration-700' : ''}`} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
    </div>
  );
}