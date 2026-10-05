import { computed, onUnmounted, ref } from 'vue';
import type { Command, Sample, Station } from './types';
import { numberMetric, powerMetric, telemetryFresh } from './types';

const WINDOW = 30 * 60 * 1000;

export function useGateway() {
  const stations = ref<Station[]>([]);
  const histories = ref<Record<string, Sample[]>>({});
  const session = ref(false);
  const online = ref(false);
  const connecting = ref(false);
  const polling = ref(false);
  const busy = ref(false);
  const checking = ref(false);
  const checks = ref<{ diagnostics: unknown; setup: unknown } | null>(null);
  const notice = ref('');
  const noticeKind = ref<'success' | 'error' | 'info'>('info');
  const now = ref(Date.now());
  let token = '';
  let generation = 0;
  let timer: ReturnType<typeof setInterval> | undefined;
  const requests = new Set<AbortController>();
  const observed = new Map<string, number>();

  function message(text: string, kind: 'success' | 'error' | 'info' = 'info') {
    notice.value = text;
    noticeKind.value = kind;
  }

  function append(station: Station, gap = false) {
    const currentTime = Date.now();
    const data = histories.value[station.name] ?? [];
    const fresh = !gap && telemetryFresh(station, currentTime);
    const time = fresh ? station.last_seen_timestamp! * 1000 : currentTime;
    if (fresh) {
      if (station.last_seen_timestamp! <= (observed.get(station.name) ?? -Infinity)) return;
      observed.set(station.name, station.last_seen_timestamp!);
      if (time <= (data.at(-1)?.time ?? -Infinity)) return;
    }
    data.push({
      time,
      input: fresh ? powerMetric(station, 'input') : null,
      output: fresh ? powerMetric(station, 'output') : null,
      battery: fresh ? numberMetric(station, 'battery_percentage') : null,
    });
    histories.value[station.name] = data.filter((point) => point.time >= currentTime - WINDOW).slice(-360);
  }

  function disconnect(quiet = false) {
    generation += 1;
    token = '';
    session.value = online.value = connecting.value = polling.value = busy.value = false;
    checking.value = false;
    checks.value = null;
    if (timer) clearInterval(timer);
    timer = undefined;
    for (const request of requests) request.abort();
    requests.clear();
    stations.value.forEach((station) => append(station, true));
    if (!quiet) message('Disconnected. The gateway and station keep running.');
  }

  async function request(path: string, body?: Record<string, unknown>, quiet = false, readOnly = false) {
    const currentGeneration = generation;
    const controller = new AbortController();
    requests.add(controller);
    const timeout = readOnly ? 10000 : body?.command === 'return-grid' ? 190000 : body ? 150000 : 10000;
    const deadline = setTimeout(() => controller.abort(), timeout);
    try {
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (body) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, {
        method: body ? 'POST' : 'GET', headers, signal: controller.signal,
        cache: 'no-store', credentials: 'omit', ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (currentGeneration !== generation) return null;
      if (response.status === 401) {
        disconnect(true);
        message('Access denied. Connect again with the gateway token.', 'error');
        return null;
      }
      if (!response.ok) {
        if (body && !readOnly) {
          let changed = true;
          try {
            const failure = await response.json();
            if (failure && typeof failure === 'object' && failure.settings_may_have_changed === false) changed = false;
          } catch { /* Do not display arbitrary server response text. */ }
          if (currentGeneration !== generation) return null;
          message(!changed ? 'The command was rejected without changing settings. Check the gateway permissions and fresh status.' : response.status === 504
            ? 'Confirmation timed out. A setting may have changed; fresh status is being checked. Do not retry automatically.'
            : 'The gateway could not confirm this command. Fresh status is being checked; a setting may have changed.', 'error');
        } else if (!quiet) {
          message('The gateway is unavailable. Readings and controls are paused.', 'error');
        }
        return null;
      }
      const result: unknown = await response.json();
      return currentGeneration === generation ? result : null;
    } catch {
      if (currentGeneration === generation && !quiet) {
        message(body && !readOnly
          ? 'Connection lost before confirmation. A setting may have changed; check fresh status before trying again.'
          : 'Cannot reach the gateway. Readings and controls are paused.', 'error');
      }
      return null;
    } finally {
      clearTimeout(deadline);
      requests.delete(controller);
    }
  }

  function isStation(value: unknown): value is Station {
    if (!value || typeof value !== 'object') return false;
    const object = value as Partial<Station>;
    return typeof object.name === 'string' && typeof object.model === 'string'
      && typeof object.connected === 'boolean' && typeof object.available === 'boolean'
      && (object.last_seen_timestamp === null || (typeof object.last_seen_timestamp === 'number' && Number.isFinite(object.last_seen_timestamp)))
      && Array.isArray(object.controls) && object.controls.every((command) => typeof command === 'string')
      && !!object.metrics && typeof object.metrics === 'object' && !Array.isArray(object.metrics);
  }

  async function refresh() {
    if (!session.value || polling.value || busy.value) return false;
    const currentGeneration = generation;
    polling.value = true;
    const result = await request('/devices');
    if (currentGeneration !== generation) return false;
    polling.value = false;
    const devices = result && typeof result === 'object' ? (result as { devices?: unknown }).devices : null;
    if (!Array.isArray(devices) || !devices.every(isStation)) {
      online.value = false;
      stations.value.forEach((station) => append(station, true));
      return false;
    }
    stations.value = devices;
    online.value = true;
    devices.forEach((station) => append(station));
    now.value = Date.now();
    return true;
  }

  async function connect(value: string) {
    disconnect(true);
    token = value;
    const currentGeneration = generation;
    connecting.value = true;
    session.value = true;
    notice.value = '';
    const success = await refresh();
    if (currentGeneration !== generation) return;
    connecting.value = false;
    if (success) message('Connected to the local gateway.', 'success');
    timer = setInterval(() => {
      now.value = Date.now();
      void refresh();
    }, 5000);
  }

  function fresh(station: Station) {
    return session.value && online.value && telemetryFresh(station, now.value);
  }

  async function send(station: Station, body: Command) {
    if (busy.value || polling.value || !fresh(station) || !station.controls.includes(body.command)) return;
    const currentGeneration = generation;
    busy.value = true;
    message('Applying command and waiting for station confirmation…');
    const result = await request(`/devices/${encodeURIComponent(station.name)}/commands`, body);
    if (currentGeneration !== generation) return;
    if (isStation(result)) {
      stations.value = stations.value.map((current) => current.name === result.name ? result : current);
      append(result);
      message('Command confirmed by fresh station telemetry.', 'success');
    } else if (result !== null) {
      message('The response did not confirm the command. A setting may have changed; check fresh status.', 'error');
    }
    const commandNotice = notice.value;
    busy.value = false;
    const refreshed = await refresh();
    if (!isStation(result) && !refreshed && currentGeneration === generation && session.value) {
      message(`${commandNotice} The gateway is currently unavailable, so fresh status could not be read.`, 'error');
    }
  }

  async function checkGateway() {
    if (!session.value || checking.value || busy.value) return;
    const currentGeneration = generation;
    checking.value = true;
    checks.value = null;
    const [diagnostics, setup] = await Promise.all([
      request('/diagnostics', undefined, true), request('/setup-check', undefined, true),
    ]);
    if (currentGeneration !== generation) return;
    checks.value = { diagnostics, setup };
    checking.value = false;
  }

  async function readOnly(path: string, body?: Record<string, unknown>) {
    if (!session.value || busy.value) return null;
    return request(path, body, true, true);
  }

  async function exportSettings(name: string) {
    const value = await readOnly(`/devices/${encodeURIComponent(name)}/settings-export`);
    if (!value || typeof value !== 'object' || !('complete' in value) || value.complete !== false
      || !('restore_supported' in value) || value.restore_supported !== false) {
      if (session.value) message('Partial settings export is unavailable on this gateway.', 'error');
      return;
    }
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'solix-partial-settings.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message('Downloaded cached preferences. This incomplete export cannot restore the station.');
  }

  onUnmounted(() => disconnect(true));
  return { stations, histories, session, online, connecting, polling, busy, checking, checks, notice, noticeKind, now,
    active: computed(() => session.value && online.value), connect, disconnect, refresh, send, fresh, checkGateway, readOnly, exportSettings };
}
