<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue';
import StationHistory from './StationHistory.vue';
import StationControls from './StationControls.vue';
import GatewayChecks from './GatewayChecks.vue';
import ChargingPreview from './ChargingPreview.vue';
import { draftFor, modelLabel, numberMetric, powerMetric } from './types';
import type { Draft, Proposal } from './types';
import { useGateway } from './useGateway';

const gateway = useGateway();
const { stations, histories, session, online, connecting, polling, busy, checking, checks, notice, noticeKind, now } = gateway;
const checksOpen = ref(false);
const tokenInput = ref('');
const selectedName = ref('');
const drafts = reactive<Record<string, Draft>>({});
const proposal = ref<Proposal | null>(null);
const dialog = ref<HTMLDialogElement | null>(null);
const cancelButton = ref<HTMLButtonElement | null>(null);
const selected = computed(() => stations.value.find((station) => station.name === selectedName.value) ?? null);
const fresh = computed(() => selected.value ? gateway.fresh(selected.value) : false);
const writable = computed(() => fresh.value && !busy.value && !polling.value && !!selected.value?.controls.length);
const history = computed(() => histories.value[selectedName.value] ?? []);
const age = computed(() => {
  const last = selected.value?.last_seen_timestamp;
  return typeof last === 'number' && Number.isFinite(last) && last <= now.value / 1000 + 5 ? `${Math.max(0, Math.floor(now.value / 1000 - last))}s ago` : 'No telemetry received';
});
const input = computed(() => selected.value ? powerMetric(selected.value, 'input') : null);
const output = computed(() => selected.value ? powerMetric(selected.value, 'output') : null);
const battery = computed(() => selected.value ? numberMetric(selected.value, 'battery_percentage') : null);
const flow = computed(() => {
  if (!fresh.value) return 'Unknown';
  switch (selected.value?.power_flow) {
    case 'grid': return 'Grid';
    case 'battery': return 'Battery';
    case 'transitioning': return 'Transitioning';
    default: return 'Unknown';
  }
});
const confirmationStation = computed(() => proposal.value ? stations.value.find((station) => station.name === proposal.value!.station) : null);
const canConfirm = computed(() => !!confirmationStation.value && gateway.fresh(confirmationStation.value)
  && !busy.value && !polling.value && !!proposal.value && confirmationStation.value.controls.includes(proposal.value.body.command));
const format = (value: number | null, suffix = '') => value === null ? '—' : `${Math.round(value)}${suffix}`;

watch(stations, (values) => {
  if (!values.some((station) => station.name === selectedName.value)) selectedName.value = values[0]?.name ?? '';
  values.forEach((station) => { if (!drafts[station.name]) drafts[station.name] = draftFor(station); });
}, { immediate: true });

watch(proposal, async (value) => {
  await nextTick();
  if (value && dialog.value && !dialog.value.open) {
    dialog.value.showModal();
    cancelButton.value?.focus();
  }
});

watch(session, (value) => { if (!value) checksOpen.value = false; });

function openChecks() {
  checksOpen.value = true;
  void gateway.checkGateway();
}

function connect() {
  const token = tokenInput.value.trim();
  tokenInput.value = '';
  void gateway.connect(token);
}

function disconnect() {
  cancel();
  tokenInput.value = '';
  gateway.disconnect();
}

function cancel() {
  dialog.value?.close();
  proposal.value = null;
}

function confirm() {
  if (!canConfirm.value || !proposal.value || !confirmationStation.value) return;
  const body = proposal.value.body;
  const station = confirmationStation.value;
  cancel();
  void gateway.send(station, body);
}
</script>

<template>
  <div class="dashboard-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="SOLIX Link dashboard">
        <span class="brand-mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3 5 14h6l-1 7 9-12h-6l1-6Z" /></svg></span>
        <span>SOLIX<span class="brand-light"> Link</span><small>LOCAL POWER CONSOLE</small></span>
      </a>
      <div class="topbar-actions"><span class="connection-badge" :class="{ connected: online, disconnected: !online }"><span class="status-dot"></span>{{ online ? 'Gateway connected' : session ? 'Gateway offline' : 'Disconnected' }}</span>
        <button v-if="session" class="quiet" data-testid="refresh" :disabled="polling || busy || connecting" @click="gateway.refresh">{{ polling ? 'Refreshing…' : '↻ Refresh' }}</button>
        <button v-if="session" class="quiet" data-testid="open-checks" :disabled="busy || connecting" @click="openChecks">Checks</button>
        <button v-if="session" class="quiet" data-testid="disconnect" :disabled="busy" @click="disconnect">Disconnect</button></div>
    </header>

    <main>
      <div v-if="notice" class="notice" :class="noticeKind" role="status" aria-live="polite" data-testid="gateway-notice"><span>{{ notice }}</span><button class="notice-close" aria-label="Dismiss notice" @click="notice = ''">×</button></div>

      <section v-if="!session" class="welcome-grid">
        <div class="welcome-copy"><p class="eyebrow">Your power. Your network.</p><h1>Stay connected<br>to your energy.</h1><p class="welcome-description">Live station readings, charging settings and hourly plans — through your local SOLIX Link gateway.</p><div class="welcome-features"><span><span class="feature-icon">◉</span> Live power & battery</span><span><span class="feature-icon">⌁</span> Local connections</span><span><span class="feature-icon">✓</span> Confirmed controls</span></div></div>
        <form class="panel login-panel" @submit.prevent="connect"><div class="login-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></svg></div><h2>Connect to your gateway</h2><p>Use the token configured on this server. Leave it empty for a gateway without authentication.</p><label for="gateway-token">Gateway token</label><input id="gateway-token" v-model="tokenInput" data-testid="token-input" type="password" autocomplete="off" spellcheck="false" placeholder="Enter gateway token" /><button class="primary login-submit" type="submit" data-testid="connect" :disabled="connecting || busy">{{ connecting ? 'Connecting…' : 'Connect to gateway' }} <span aria-hidden="true">→</span></button><p class="login-footnote">The token stays in memory and clears when you disconnect or close this page.</p></form>
      </section>

      <template v-else>
        <div class="workspace-heading"><div><p class="eyebrow">Station dashboard</p><h1>{{ selected?.name || 'Your stations' }}</h1><p>{{ selected ? modelLabel(selected.model) : connecting ? 'Connecting to the gateway…' : 'No configured stations reported by this gateway.' }}</p></div>
          <div class="station-picker"><label for="station-select">Select station</label><select id="station-select" v-model="selectedName" data-testid="station-select" :disabled="busy || !stations.length"><option v-for="station in stations" :key="station.name" :value="station.name">{{ station.name }} · {{ modelLabel(station.model) }}</option></select></div></div>

        <template v-if="selected">
          <div class="station-status"><span class="live-badge" :class="{ stale: !fresh }"><span class="status-dot"></span>{{ fresh ? 'Live telemetry' : 'Stale / unavailable' }}</span><span>Updated {{ age }}</span><span class="status-spacer"></span><span>{{ selected.controls.length ? 'Control enabled' : 'Read-only gateway' }}</span><span>{{ selected.timezone_name || 'Timezone not reported' }}</span></div>
          <div class="metric-grid" :class="{ 'metrics-stale': !fresh }">
            <section class="metric-card battery-card" data-testid="battery-reading"><div class="metric-top"><span>Battery</span><svg class="metric-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="17" height="12" rx="3" /><path d="M22 10v4M7 10h9v4H7z" /></svg></div><div class="metric-value">{{ format(battery) }}<span>%</span></div><p>{{ selected.metrics.battery_status ?? 'Activity unknown' }}</p></section>
            <section class="metric-card" data-testid="input-reading"><div class="metric-top"><span>Power in</span><span class="metric-arrow input-arrow" aria-hidden="true">↙</span></div><div class="metric-value">{{ format(input) }}<span>W</span></div><p>{{ numberMetric(selected, 'ac_input_connected') === 1 ? 'AC input connected' : numberMetric(selected, 'ac_input_connected') === 0 ? 'AC input not connected' : 'AC input not reported' }}</p></section>
            <section class="metric-card"><div class="metric-top"><span>Power out</span><span class="metric-arrow output-arrow" aria-hidden="true">↗</span></div><div class="metric-value">{{ format(output) }}<span>W</span></div><p>{{ numberMetric(selected, 'ac_output_enabled') === 1 ? 'AC output enabled' : numberMetric(selected, 'ac_output_enabled') === 0 ? 'AC output off' : 'AC output not reported' }}</p></section>
            <section class="metric-card" data-testid="supply-reading"><div class="metric-top"><span>Supply source</span><span class="metric-arrow supply-arrow" aria-hidden="true">⌁</span></div><div class="metric-value source-value">{{ flow }}</div><p>{{ String(selected.metrics.usage_mode ?? 'Mode unknown').replaceAll('_', ' ') }}<span v-if="selected.metrics.active_tariff && selected.metrics.active_tariff !== 'none'"> · {{ String(selected.metrics.active_tariff).replaceAll('_', ' ') }}</span></p></section>
          </div>
          <StationHistory :key="selected.name" :station="selected" :samples="history" :now="now" :request="gateway.readOnly" />
          <div class="station-details"><span>Upper charge limit <strong>{{ format(numberMetric(selected, 'max_charge_percentage'), '%') }}</strong></span><span>Discharge floor <strong>{{ format(numberMetric(selected, 'min_charge_percentage'), '%') }}</strong></span><span>Reserve <strong>{{ format(numberMetric(selected, 'backup_reserve_percentage'), '%') }}</strong></span><span>Temperature <strong>{{ format(numberMetric(selected, 'temperature_c'), '°C') }}</strong></span><span>Firmware <strong>{{ selected.metrics.software_version ?? '—' }}</strong></span><span v-if="selected.model === 'c1000_gen2' && [0, 1].includes(numberMetric(selected, 'pv_weak_light_locked') ?? -1)" data-testid="pv-weak-light-lock" title="Firmware-derived C1000 Gen 2 flag; physical PV behavior untested.">PV weak-light lock <strong>{{ numberMetric(selected, 'pv_weak_light_locked') === 1 ? 'Active' : 'Inactive' }}</strong></span></div>
          <button class="secondary" data-testid="settings-export" :disabled="busy || polling" @click="gateway.exportSettings(selected.name)">Download partial settings</button>
          <StationControls v-if="drafts[selected.name]" :station="selected" :draft="drafts[selected.name]!" :writable="writable" :now="now" @propose="proposal = $event" />
          <ChargingPreview :key="selected.name" :station="selected" :busy="busy" :request="gateway.readOnly" />
        </template>
        <section v-else-if="!connecting" class="panel empty-stations"><h2>No stations configured</h2><p>Add a station to your SOLIX Link gateway, then refresh this page.</p><button class="secondary" :disabled="polling" @click="gateway.refresh">Refresh stations</button></section>
      </template>
      <footer class="page-footer"><span>SOLIX Link <span class="footer-separator">/</span> Local gateway</span><span>5-second telemetry refresh · Optional saved history</span></footer>
    </main>

    <GatewayChecks v-if="checksOpen && session" :checking="checking" :reports="checks" @close="checksOpen = false" @refresh="gateway.checkGateway" />
    <dialog v-if="proposal" ref="dialog" class="confirm-dialog" data-testid="command-review" aria-labelledby="confirmation-title" @cancel.prevent="cancel">
      <div class="confirmation-label"><span class="status-dot"></span>Confirm station command</div><h2 id="confirmation-title">{{ proposal.title }}</h2><p class="confirmation-station">{{ proposal.station }}</p><p>{{ proposal.detail }}</p><ul class="confirmation-summary"><li v-for="item in proposal.summary" :key="item">{{ item }}</li></ul><p v-if="!canConfirm" class="validation-error">Wait for fresh telemetry and an available gateway before applying.</p><div class="confirmation-actions"><button ref="cancelButton" class="secondary" data-testid="cancel-command" @click="cancel">Cancel</button><button class="primary" data-testid="confirm-command" :disabled="!canConfirm" @click="confirm">Apply command</button></div>
    </dialog>
  </div>
</template>
