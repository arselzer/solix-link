<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Station } from './types';

const props = defineProps<{ station: Station; fresh: boolean }>();
const showAll = ref(false);
const labels: Record<string, string> = {
  model_unsupported: 'Not established for this model', transport_unsupported: 'Unavailable on this transport',
  gateway_controls_disabled: 'HTTP controls disabled', token_scope_denied: 'Token does not permit this command',
  worker_controls_disabled: 'Worker controls disabled', not_advertised: 'Not advertised by this service',
  telemetry_unavailable: 'Fresh connected telemetry required', missing_or_invalid_metrics: 'Missing or invalid cached settings',
  firmware_unqualified: 'Firmware qualification required', output_must_be_off: 'Its output must already be off',
  countdown_must_be_inactive: 'Output countdowns must be inactive', standard_mode_required: 'Standard mode required',
  clock_must_be_inactive: 'Clock screen/transfer must be inactive', command_in_progress: 'Another command is running',
};
const rows = computed(() => {
  const report = props.station.control_availability;
  if (report?.schema_version !== 1 || !Array.isArray(report.commands)) return [];
  return report.commands.filter((row) => row && typeof row.command === 'string'
    && Array.isArray(row.reasons) && row.reasons.every((reason) => typeof reason === 'string' && reason in labels)
    && Array.isArray(row.missing_metrics) && row.missing_metrics.every((key) => typeof key === 'string'));
});
const visible = computed(() => rows.value.filter((row) => showAll.value || !row.reasons.includes('model_unsupported') && !row.reasons.includes('transport_unsupported')));
</script>

<template>
  <details class="panel availability-panel" data-testid="control-availability">
    <summary>Why controls are available or blocked</summary>
    <p>Cached checks explain permissions and prerequisites. Command-specific backend checks still apply.</p>
    <p v-if="!fresh" class="validation-error">Telemetry is stale or unavailable; readiness is unknown.</p>
    <p v-if="!rows.length">This gateway does not provide control explanations yet.</p>
    <template v-else><label class="availability-filter"><input type="checkbox" v-model="showAll" /> Include unsupported controls</label><div class="availability-table"><table><thead><tr><th>Command</th><th>Cached readiness</th></tr></thead><tbody><tr v-for="row in visible" :key="row.command"><td>{{ row.command }}</td><td>{{ row.reasons.length ? row.reasons.map((reason) => labels[reason]).join('; ') : fresh ? 'Cached prerequisites met · backend checks still required' : 'Unknown' }}<span v-if="row.missing_metrics.length"> · {{ row.missing_metrics.join(', ') }}</span></td></tr></tbody></table></div></template>
  </details>
</template>
