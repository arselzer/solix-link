<script setup lang="ts">
import { computed } from 'vue';
import type { Station } from './types';
import { modelLabel, numberMetric, powerMetric, telemetryFresh } from './types';

const props = defineProps<{ stations: Station[]; selected: string; now: number; online: boolean; busy: boolean }>();
const emit = defineEmits<{ select: [name: string] }>();
const live = (station: Station) => props.online && telemetryFresh(station, props.now);
const freshCount = computed(() => props.stations.filter(live).length);
function reading(station: Station, value: number | null, suffix: string): string {
  return live(station) && value !== null ? `${Math.round(value)}${suffix}` : 'Unknown';
}
function alerts(station: Station): string[] {
  if (!live(station)) return ['Telemetry unavailable · power state unknown'];
  const flags = [];
  if (numberMetric(station, 'ac_input_connected') === 0) flags.push('Mains input disconnected');
  if (station.ups_state?.battery_reserve_low === true) flags.push('Battery below reserve');
  if (station.command_context?.busy === true) flags.push('Command in progress');
  return flags;
}
function supply(station: Station): string {
  return live(station) && ['grid', 'battery', 'transitioning'].includes(station.power_flow ?? '')
    ? station.power_flow!.replace(/^./, (first) => first.toUpperCase()) : 'Unknown';
}
</script>

<template>
  <section class="panel fleet-panel" data-testid="fleet-overview">
    <div class="panel-heading"><h2>Your stations</h2><span>{{ freshCount }}/{{ stations.length }} with fresh telemetry</span></div>
    <p class="fleet-note">Power is shown per station. Chained supplies can count the same load.</p>
    <div class="fleet-grid"><article v-for="station in stations" :key="station.name" class="fleet-card" :class="{ 'fleet-selected': station.name === selected, 'fleet-stale': !live(station) }" :data-station="station.name">
      <h3>{{ station.name }}</h3><p>{{ modelLabel(station.model) }} · {{ station.controls.length ? 'Controls permitted' : 'Monitoring only' }}</p>
      <dl><div><dt>Battery</dt><dd>{{ reading(station, numberMetric(station, 'battery_percentage'), '%') }}</dd></div><div><dt>Input</dt><dd>{{ reading(station, powerMetric(station, 'input'), ' W') }}</dd></div><div><dt>Output</dt><dd>{{ reading(station, powerMetric(station, 'output'), ' W') }}</dd></div><div><dt>Supply</dt><dd>{{ supply(station) }}</dd></div></dl>
      <ul class="fleet-alerts" v-if="alerts(station).length"><li v-for="alert in alerts(station)" :key="alert">{{ alert }}</li></ul><p v-else class="fleet-note">No flagged conditions in current telemetry.</p>
      <button class="secondary" :disabled="busy" :aria-pressed="station.name === selected" @click="emit('select', station.name)">View station</button>
    </article></div>
  </section>
</template>
