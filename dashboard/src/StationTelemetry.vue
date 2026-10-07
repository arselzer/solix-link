<script setup lang="ts">
import { computed } from 'vue';
import type { Station } from './types';
import { telemetryRows } from './telemetry';

const props = defineProps<{ station: Station; now: number }>();
const rows = computed(() => telemetryRows(props.station, props.now));
</script>

<template>
  <details v-if="rows.length" class="panel reported-telemetry" data-testid="reported-telemetry">
    <summary>Ports, runtime &amp; component firmware</summary>
    <p>Reported port power and device estimates. Countdown values describe current timers; zero means no active countdown. Wi-Fi signal uses its own query time.</p>
    <dl><div v-for="row in rows" :key="row.key" :data-testid="`telemetry-${row.key}`"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl>
  </details>
</template>

<style scoped>
.reported-telemetry { margin-block: 1rem; padding: 1.25rem; }
summary { cursor: pointer; font-weight: 600; }
p { color: var(--muted, #526276); font-size: .875rem; line-height: 1.5; }
dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); gap: .5rem 1.5rem; }
dl > div { display: flex; justify-content: space-between; gap: 1rem; border-bottom: 1px solid var(--line, #283240); padding: .5rem 0; }
dt { color: var(--muted, #526276); }
dd { margin: 0; font-variant-numeric: tabular-nums; }
</style>
