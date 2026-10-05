<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import type { Station } from './types';

const props = defineProps<{ station: Station; busy: boolean;
  request: (path: string, body?: Record<string, unknown>) => Promise<unknown> }>();
const records = ref<Record<string, unknown>[]>([]);
const pending = ref(false);
const loaded = ref(false);
const persisted = ref(false);
const error = ref('');
const filter = ref('all');
const baseline = ref<Record<string, unknown> | null>(null);
const comparison = ref<Record<string, unknown> | null>(null);
let generation = 0;
const labels: Record<string, string> = { mains_lost: 'Mains lost', mains_restored: 'Mains restored',
  telemetry_lost: 'Telemetry lost · power state unknown', telemetry_restored: 'Telemetry restored',
  battery_low: 'Battery below reserve', battery_recovered: 'Battery recovered',
  settings_changed: 'Reported settings changed', command_started: 'Command started', command_finished: 'Command result' };
function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
const visible = computed(() => records.value.filter((row) => filter.value === 'all'
  || (filter.value === 'commands' ? String(row.kind).startsWith('command_')
    : filter.value === 'settings' ? row.kind === 'settings_changed' : !String(row.kind).startsWith('command_') && row.kind !== 'settings_changed')));
const changes = computed(() => Array.isArray(comparison.value?.changes) ? comparison.value.changes.map(object).filter((row) => row !== null) : []);
const path = computed(() => `/devices/${encodeURIComponent(props.station.name)}`);
function time(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : 'Unknown time';
}
function details(row: Record<string, unknown>): string {
  const data = object(row.details);
  if (!data) return '';
  if (row.kind === 'command_started') return String(data.command ?? '');
  if (row.kind === 'command_finished') {
    const readback = object(data.readback)?.reported_values_match;
    return `${data.command ?? ''}: ${data.outcome ?? 'unknown'}${data.error ? ` (${data.error})` : ''} · cached values ${readback === true ? 'match' : readback === false ? 'differ' : 'unknown'} · electrical behavior unverified`;
  }
  if (row.kind === 'settings_changed') {
    const changes = Array.isArray(data.changes) ? data.changes.map(object).filter((item) => item !== null) : [];
    return changes.map((item) => `${item.field}: ${JSON.stringify(item.before)} → ${JSON.stringify(item.after)}`).join('; ');
  }
  if (row.kind === 'battery_low' || row.kind === 'battery_recovered') return `${data.battery_percentage}% battery · ${data.reserve_percentage}% reserve threshold`;
  return data.interval_unknown === true ? 'Observed after a telemetry gap; event start time is unknown.' : '';
}
async function load() {
  const revision = ++generation;
  pending.value = true; error.value = '';
  const result = object(await props.request(`${path.value}/activity?limit=50`));
  if (revision !== generation) return;
  pending.value = false; loaded.value = true;
  if (result?.schema_version === 1 && Array.isArray(result.records)) {
    records.value = result.records.map(object).filter((row): row is Record<string, unknown> => row !== null && row.name === props.station.name && typeof row.kind === 'string' && row.kind in labels).reverse();
    persisted.value = result.persisted === true;
  } else error.value = 'Activity is unavailable on this gateway.';
}
async function capture() {
  const revision = ++generation;
  pending.value = true; error.value = ''; comparison.value = null;
  const result = object(await props.request(`${path.value}/settings-export`));
  if (revision !== generation) return;
  pending.value = false;
  if (result?.schema_version === 1 && result.complete === false && result.model === props.station.model && object(result.settings)) baseline.value = result;
  else error.value = 'A partial settings baseline could not be captured.';
}
async function compare() {
  if (!baseline.value) return;
  const revision = ++generation;
  pending.value = true; error.value = '';
  const result = object(await props.request(`${path.value}/settings-compare`, { baseline: baseline.value }));
  if (revision !== generation) return;
  pending.value = false;
  if (result?.schema_version === 1 && result.compatible === true && Array.isArray(result.changes)) comparison.value = result;
  else error.value = 'Settings comparison is unavailable or the model changed.';
}
function toggle(event: Event) {
  if ((event.target as HTMLDetailsElement).open && !loaded.value) void load();
}
onUnmounted(() => { generation++; });
</script>

<template>
  <details class="panel activity-panel" data-testid="station-activity" @toggle="toggle">
    <summary>UPS events, settings and command history</summary>
    <p>Cached observations. Telemetry loss does not establish a power failure. Command completion does not verify electrical behavior.</p>
    <div class="activity-actions"><label>Show <select v-model="filter"><option value="all">All activity</option><option value="ups">UPS observations</option><option value="settings">Settings changes</option><option value="commands">Commands</option></select></label><button class="secondary" :disabled="pending || busy" @click="load">Refresh activity</button></div>
    <p>{{ persisted ? 'Private persisted history' : 'Current gateway session only' }} · newest 50 records · bounded retention</p>
    <p v-if="error" class="validation-error" role="status">{{ error }}</p>
    <ol class="activity-records" data-testid="activity-records"><li v-for="row in visible" :key="String(row.id)"><strong>{{ labels[String(row.kind)] }}</strong><time>{{ time(row.timestamp) }}</time><p>{{ details(row) }}</p></li></ol>
    <p v-if="loaded && !visible.length && !error">No matching activity recorded.</p>
    <h3>Compare partial settings</h3>
    <p>Keep a sanitized baseline in this tab, then compare later. No settings are restored. Cached field freshness is unverified.</p>
    <div class="activity-actions"><button class="secondary" data-testid="settings-baseline" :disabled="pending || busy" @click="capture">{{ baseline ? 'Replace baseline' : 'Capture baseline' }}</button><button class="secondary" data-testid="settings-compare" :disabled="pending || busy || !baseline" @click="compare">Compare with baseline</button></div>
    <div v-if="comparison" data-testid="settings-differences"><p>{{ changes.length }} changed preferences · {{ comparison.after_fresh ? 'recent telemetry' : 'stale telemetry' }}</p><ul><li v-for="change in changes" :key="String(change.field)">{{ change.field }}: {{ JSON.stringify(change.before) }} → {{ JSON.stringify(change.after) }}</li></ul><p>Newly reported: {{ JSON.stringify(comparison.newly_reported_fields) }}. No longer reported: {{ JSON.stringify(comparison.no_longer_reported_fields) }}.</p></div>
  </details>
</template>
