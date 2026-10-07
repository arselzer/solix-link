<script setup lang="ts">
import { computed } from 'vue';
import type { Station } from './types';
import { groups, nativeEnergy } from './nativeEnergy';

const props = defineProps<{ station: Station; now: number }>();
const report = computed(() => nativeEnergy(props.station, props.now));
const supported = computed(() => ['c1000_gen2', 'c2000_gen2'].includes(props.station.model) && props.station.protocol === 'native_mqtt');
const rows = computed(() => groups.flatMap((group) => {
  const values = report.value?.groups[group];
  return values ? Object.entries(values.raw).map(([key, raw]) => ({ group, key, raw,
    kwh: key.endsWith('_energy_raw') ? (raw / 1000).toFixed(6) : '—' })) : [];
}));
const label = (value: string) => value.replaceAll('_', ' ');
</script>

<template>
  <section class="panel native-energy" data-testid="native-energy">
    <div class="panel-heading"><h2>Device energy counters</h2><span>{{ report ? report.available ? 'Recent report' : 'Stale report' : 'No native report' }}</span></div>
    <p v-if="!supported">Native counters are not available through this model/transport. Saved power-based history remains separate.</p>
    <p v-else-if="!report">Waiting for an energy upload. Requires opt-in <code>--energy-reports</code> on the Gen 2 AP worker; this page sends no station request.</p>
    <template v-else>
      <p data-testid="native-energy-status">Received {{ new Date(report.reported_at * 1000).toLocaleString() }} · Epoch {{ report.counter_epoch }} · {{ label(report.continuity) }} · {{ report.received_reports }} reports</p>
      <p>kWh conversion: {{ report.conversion_basis === 'nominal_wh' ? 'nominal Wh inferred from C1000 Gen 2 1.1.4.9 firmware' : 'assumed Wh; this firmware’s scaling is unresolved' }}. Physical units are unverified. Counters can reset; mode groups are not summed.</p>
      <div class="energy-scroll"><table><thead><tr><th scope="col">Mode group</th><th scope="col">Counter</th><th scope="col">Raw value</th><th scope="col">kWh (unverified)</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="`${row.group}:${row.key}`"><td>{{ label(row.group) }}</td><td>{{ label(row.key.replace(/_raw$/, '')) }}</td><td>{{ row.raw }}</td><td>{{ row.kwh }}</td></tr></tbody>
      </table></div>
      <p>AC input includes charging and bypass; AC output is delivered load energy. These are not battery-only energy or calibrated lifetime totals. Raw duration units are not converted.</p>
    </template>
  </section>
</template>

<style scoped>
.native-energy { margin-block: 1rem; }
.native-energy p { color: var(--muted, #526276); font-size: .875rem; line-height: 1.5; }
.energy-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .6rem; border-bottom: 1px solid #e0e7ef; white-space: nowrap; }
th { font-size: .8rem; }
</style>
