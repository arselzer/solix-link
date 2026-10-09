<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue';
import type { Station } from './types';

const props = defineProps<{ station: Station; busy: boolean; request: (path: string, body?: Record<string, unknown>) => Promise<unknown> }>();
const form = reactive({ mode: 'export', exportValue: '', priceValue: '', exportStart: 600, exportStop: 300,
  priceStart: 0.1, priceStop: 0.2, high: 1000, low: 300, reserve: 20, simulateArmed: true, exportConfirmed: false,
  policy: 'owned_surplus', ownership: '', controllerAction: 'evaluate', targetExport: 100, deadband: 50, maximumStep: 200, dischargeStart: 0.4,
  dischargeStop: 0.3, margin: 5, override: 'none', previous: 'grid', previousAge: 300,
  cooldown: 180, latch: false });
const controller = computed(() => form.policy.startsWith('owned_'));
const surplus = computed(() => ['surplus', 'owned_surplus'].includes(form.policy));
const priceTou = computed(() => ['price_tou', 'owned_price'].includes(form.policy));
const source = computed(() => surplus.value ? 'export' : priceTou.value ? 'price' : form.mode);
const pending = ref(false);
const error = ref('');
const result = ref<Record<string, unknown> | null>(null);
const signalTime = reactive({ export: 0, price: 0 });
let revision = 0;
const reasons: Record<string, string> = {
  policy_disarmed: 'The simulated policy is disarmed.', command_latched: 'The command latch needs review.',
  cooldown_active: 'Cooldown has not elapsed.', station_disconnected: 'The station is disconnected.',
  station_unavailable: 'The monitor reports unavailable.', station_error: 'The monitor reports a station error.',
  telemetry_missing: 'No usable telemetry timestamp.', telemetry_future: 'Telemetry is ahead of the gateway clock.',
  telemetry_stale: 'Telemetry is stale.', mains_not_confirmed: 'AC input is not confirmed connected.',
  ac_output_not_confirmed: 'AC output is not confirmed on.', fast_not_confirmed_off: 'Fast charging must already be off.',
  standard_mode_required: 'Standard mode is required.', no_active_tariff_required: 'An active tariff blocks this policy.',
  battery_invalid: 'Battery percentage is unavailable.', current_settings_invalid: 'Current limits are incomplete or invalid.',
  policy_power_outside_model_range: 'Proposed watts are outside this model’s range.',
  policy_reserve_outside_current_caps: 'Proposed reserve conflicts with the current cap or discharge floor.',
  export_signal_stale: 'The manual export sample is stale. Enter a new sample.', price_signal_stale: 'The manual price sample is stale. Enter a new sample.',
  export_signal_future: 'Export sample time is ahead of the gateway.', price_signal_future: 'Price sample time is ahead of the gateway.',
  below_effective_reserve: 'Battery is below the effective reserve.', price_opportunity: 'Price meets the opportunity threshold.',
  export_opportunity: 'Export meets the opportunity threshold.', no_opportunity: 'No opportunity threshold is met.',
  reserve_raise_proposed: 'Raise the saved reserve.', charging_power_change_proposed: 'Change the saved charging-power limit.',
  settings_already_match: 'Saved settings already match.', unsupported_model: 'This model is unsupported.',
  unsupported_transport: 'This preview requires native MQTT.', clock_invalid: 'The gateway clock is invalid.',
  policy_state_future: 'Previous-preview time is ahead of the gateway.', manual_hold: 'Manual hold blocks proposals.',
  empty_saved_tariff_plan_required: 'Price proposals require zero saved tariff slots.',
  current_power_outside_policy_range: 'The saved charging limit is outside the configured policy range.',
  no_export_opportunity: 'No export opportunity; step toward the idle limit.', export_deadband: 'Export is within the target deadband.',
  surplus_step_proposed: 'Propose a bounded step toward the export target.',
  saved_limit_is_not_measured_battery_power: 'Saved charging watts do not measure battery charging power.',
  reserve_or_resume_margin_blocks_discharge: 'Reserve and resume margin block the battery-use proposal.',
  standard_empty_plan_baseline_only: 'This candidate assumes Standard mode with an empty saved tariff plan.',
  price_charge: 'Propose an off-peak charging plan.', price_discharge: 'Propose a peak battery-use plan.',
  grid_baseline: 'Retain the current Standard-mode baseline.', manual_override: 'A simulated manual override is selected.',
  disarmed: 'The simulated policy is disarmed.', unowned: 'No simulated policy owns this station.',
  another_policy_owns_station: 'Release the other policy before changing ownership.',
  policy_clock_regressed: 'The simulated ownership clock is ahead of the gateway.',
  manual_reconciliation_required: 'Pending or blocked ownership needs manual reconciliation.',
  manual_intervention_detected: 'Protected settings differ from the simulated owner’s confirmed baseline.',
  qualified_connected_c1000_gen2_required: 'HA execution requires a connected native C1000 Gen 2.',
  fresh_saved_plan_required: 'Fresh telemetry and an independently fresh saved-plan readback are required.',
  empty_or_owned_all_day_plan_required: 'Requires an empty Standard plan or a matching owned all-day plan.',
  protected_charging_baseline_required: 'Firmware, outputs, Fast, clock, disaster state, timers and limits must meet the controller guards.',
  plan_activation_readback_required: 'The saved plan and its activation readback must agree.',
  guarded_gateway_commands_required: 'The gateway must advertise guarded TOU and charging controls.',
  restore_empty_standard_plan_before_reset: 'Restore the empty Standard plan before resetting ownership.',
  restore_original_power_before_reset: 'Restore the original watts before resetting surplus ownership.',
  ownership_reset: 'The simulated ownership can be cleared without a station command.',
  fresh_finite_price_required: 'A fresh finite price sample is required.',
  fresh_finite_export_required: 'A fresh finite export sample is required.',
  export_sign_confirmation_required: 'Confirm that positive watts mean export before arming.',
  configure_reserve_before_arming: 'Configure reserve at or above the minimum before arming. The controller will not raise it.',
  empty_standard_baseline_required: 'Requires an empty Standard plan before acquiring ownership.',
  unchanged: 'The saved setting already matches; no command would be sent.',
  cooldown: 'Cooldown has not elapsed.', plan_change: 'Propose one guarded all-day plan change.',
  power_change: 'Propose one bounded charging-power change.',
};
const explanation = computed(() => Array.isArray(result.value?.reasons)
  ? result.value.reasons.flatMap((code) => typeof code === 'string' && Object.hasOwn(reasons, code) ? [reasons[code]] : []) : []);
const proposals = computed(() => Array.isArray(result.value?.proposed_settings)
  ? result.value.proposed_settings.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const setting = value as Record<string, unknown>;
    if (setting.command === 'set-charge-power' && typeof setting.watts === 'number') return [`Charging power → ${setting.watts} W`];
    if (setting.command === 'set-backup-reserve' && typeof setting.reserve === 'number') return [`Reserve → ${setting.reserve}%`];
    if (setting.setting === 'ac_charging_power_limit_w' && typeof setting.value === 'number') return [`Charging power → ${setting.value} W`];
    if (setting.setting === 'backup_reserve_percentage' && typeof setting.value === 'number') return [`Reserve → ${setting.value}%`];
    return [];
  }) : []);
const plan = computed(() => {
  const candidate = result.value?.proposed_plan;
  if (!candidate || typeof candidate !== 'object') return [];
  const periods = (candidate as Record<string, unknown>).periods;
  if ((candidate as Record<string, unknown>).enabled === false && Array.isArray(periods) && !periods.length) return ['Candidate TOU: clear the plan and return to Standard mode'];
  return Array.isArray(periods) ? periods.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const period = value as Record<string, unknown>;
    if (!['peak', 'off_peak'].includes(String(period.tariff)) || period.start_hour !== 0 || period.end_hour !== 24) return [];
    return [`Candidate TOU: ${period.tariff === 'peak' ? 'peak / battery use' : 'off-peak / charging'} · 00:00–24:00`];
  }) : [];
});

watch(form, () => { revision++; result.value = null; error.value = ''; });
watch(() => form.policy, () => { form.override = 'none'; form.previous = 'grid'; });
onUnmounted(() => { revision++; });
function sample(role: 'export' | 'price') { signalTime[role] = Date.now() / 1000; }
async function preview() {
  if (pending.value || props.busy) return;
  const now = Date.now() / 1000;
  const signals: Record<string, unknown> = {};
  if (source.value !== 'price') {
    if (!form.exportConfirmed || !String(form.exportValue).trim() || !Number.isFinite(Number(form.exportValue))) {
      error.value = 'Enter an export sample in watts and confirm that positive means export.'; return;
    }
    signals.export = { value: Number(form.exportValue), timestamp: signalTime.export, unit: 'W', positive_means: 'export' };
  }
  if (source.value !== 'export') {
    if (!String(form.priceValue).trim() || !Number.isFinite(Number(form.priceValue))) { error.value = 'Enter a numeric price sample.'; return; }
    signals.price = { value: Number(form.priceValue), timestamp: signalTime.price };
  }
  const currentRevision = revision;
  pending.value = true; error.value = ''; result.value = null;
  try {
    const fixed: Record<string, unknown> = {
      config: { signal_mode: form.mode, armed: form.simulateArmed, command_latch: false, latch_changed_at: now - 181,
        charging_watts: Number(form.high), idle_watts: Number(form.low), minimum_reserve: Number(form.reserve), cooldown: 180,
        price_start: Number(form.priceStart), price_stop: Number(form.priceStop), price_max_age: 3600,
        export_start: Number(form.exportStart), export_stop: Number(form.exportStop), export_max_age: 120 }, signals,
    };
    const adaptive: Record<string, unknown> = {
      config: { kind: form.policy, armed: form.simulateArmed, command_latch: form.latch,
        cooldown: Number(form.cooldown), manual_override: form.override, minimum_reserve: Number(form.reserve),
        idle_watts: Number(form.low), maximum_watts: Number(form.high),
        ...(form.policy === 'surplus' ? { export_start: Number(form.exportStart), export_stop: Number(form.exportStop),
          export_max_age: 120, target_export_w: Number(form.targetExport), deadband_w: Number(form.deadband), maximum_step_w: Number(form.maximumStep) }
        : { charge_start: Number(form.priceStart), charge_stop: Number(form.priceStop), discharge_start: Number(form.dischargeStart),
          discharge_stop: Number(form.dischargeStop), price_max_age: 3600, soc_resume_margin: Number(form.margin) }) },
      signals, state: { previous_decision: form.previous, last_changed_at: now - Number(form.previousAge) },
    };
    let ownership: unknown = null;
    if (controller.value && form.ownership.trim()) {
      try { ownership = JSON.parse(form.ownership); }
      catch { error.value = 'Enter valid ownership JSON, or leave it blank to assume an unowned station.'; return; }
    }
    const owned: Record<string, unknown> = { policy: surplus.value ? 'surplus' : 'price',
      config: { cooldown: Number(form.cooldown), minimum_reserve: Number(form.reserve),
        ...(surplus.value ? { idle_watts: Number(form.low), maximum_watts: Number(form.high), maximum_step_w: Number(form.maximumStep),
          export_start: Number(form.exportStart), export_stop: Number(form.exportStop), export_max_age: 120,
          target_export_w: Number(form.targetExport), deadband_w: Number(form.deadband), positive_export_confirmed: form.exportConfirmed }
        : { charge_start: Number(form.priceStart), charge_stop: Number(form.priceStop), discharge_start: Number(form.dischargeStart),
          discharge_stop: Number(form.dischargeStop), price_max_age: 3600, soc_resume_margin: Number(form.margin) }) },
      signals, ownership, armed: form.simulateArmed, override: form.override, action: form.controllerAction };
    const endpoint = controller.value ? 'controller-preview' : form.policy === 'fixed' ? 'charging-preview' : 'adaptive-preview';
    const response = await props.request(`/devices/${encodeURIComponent(props.station.name)}/${endpoint}`, controller.value ? owned : form.policy === 'fixed' ? fixed : adaptive);
    if (currentRevision !== revision) return;
    const parsed = response && typeof response === 'object' ? response as Record<string, unknown> : null;
    if (parsed?.schema_version === 1 && parsed.dry_run === true && parsed.commands_sent === 0
      && (form.policy === 'fixed' || parsed.executor_available === false)) result.value = parsed;
    else error.value = 'Preview unavailable. Check the input ranges and gateway version.';
  } finally { pending.value = false; }
}
</script>

<template>
  <section v-if="['c1000_gen2', 'c2000_gen2'].includes(station.model) && station.protocol === 'native_mqtt'" class="panel controls-panel preview-panel" data-testid="charging-preview-panel">
    <div class="panel-heading"><div><p class="eyebrow">Read-only simulation</p><h2>Charging policy preview</h2></div><span class="tag">No commands</span></div>
    <p class="schedule-note">Enter manual signal samples to see a proposed decision against fresh station telemetry. This does not enable your HA automation or change station settings.</p>
    <form @submit.prevent="preview">
      <div class="preview-grid">
        <label>Preview type<select v-model="form.policy" data-testid="preview-policy"><option value="owned_surplus">HA controller: solar surplus</option><option value="owned_price">HA controller: price-driven battery use</option><option value="fixed">Exploratory fixed charging limits</option><option value="surplus">Exploratory solar surplus</option><option value="price_tou">Exploratory price-driven battery use</option></select></label>
        <label v-if="form.policy === 'fixed'">Opportunity source<select v-model="form.mode" data-testid="preview-mode"><option value="export">Solar export</option><option value="price">Electricity price</option><option value="either">Either</option></select></label>
        <label>Opportunity charging (W)<input v-model.number="form.high" type="number" step="100" data-testid="preview-high" /></label>
        <label>Other-times charging (W)<input v-model.number="form.low" type="number" step="100" /></label>
        <label>Minimum reserve (%)<input v-model.number="form.reserve" type="number" step="5" /></label>
        <template v-if="source !== 'price'">
          <label>Manual export sample (W)<input v-model="form.exportValue" type="number" step="any" data-testid="preview-export" @input="sample('export')" /></label>
          <label>Export start / stop (W)<span class="preview-pair"><input v-model.number="form.exportStart" type="number" aria-label="Export start watts" /><input v-model.number="form.exportStop" type="number" aria-label="Export stop watts" /></span></label>
        </template>
        <template v-if="source !== 'export'">
          <label>Manual price sample<input v-model="form.priceValue" type="number" step="any" data-testid="preview-price" @input="sample('price')" /></label>
          <label>Price start / stop<span class="preview-pair"><input v-model.number="form.priceStart" type="number" step="any" aria-label="Price start" /><input v-model.number="form.priceStop" type="number" step="any" aria-label="Price stop" /></span></label>
        </template>
        <template v-if="surplus">
          <label>Target export (W)<input v-model.number="form.targetExport" type="number" min="0" max="20000" /></label>
          <label>Export deadband (W)<input v-model.number="form.deadband" type="number" min="0" max="1000" /></label>
          <label>Maximum step (W)<input v-model.number="form.maximumStep" type="number" step="100" /></label>
        </template>
        <template v-if="priceTou">
          <label>Battery-use start / stop price<span class="preview-pair"><input v-model.number="form.dischargeStart" type="number" step="any" aria-label="Discharge start price" /><input v-model.number="form.dischargeStop" type="number" step="any" aria-label="Discharge stop price" /></span></label>
          <label>SOC resume margin (%)<input v-model.number="form.margin" type="number" min="1" max="25" /></label>
        </template>
        <template v-if="form.policy !== 'fixed'">
          <label>Simulated override<select v-model="form.override" data-testid="preview-override"><option value="none">None</option><option value="hold">Hold</option><option value="charge">Charge</option><option v-if="priceTou" value="grid">Grid</option><option v-if="priceTou" value="battery">Battery</option></select></label>
          <label v-if="!controller">Previous preview decision<select v-model="form.previous"><option value="grid">Grid</option><option value="idle">Idle</option><option value="charge">Charge</option><option value="battery">Battery</option></select></label>
          <label v-if="!controller">Previous preview age (seconds)<input v-model.number="form.previousAge" type="number" min="0" /></label>
          <label>Cooldown (seconds)<input v-model.number="form.cooldown" type="number" min="60" max="3600" /></label>
        </template>
      </div>
      <template v-if="controller">
        <div class="preview-grid">
          <label>Simulated action<select v-model="form.controllerAction"><option value="evaluate">Evaluate</option><option value="release">Release this owner</option><option value="reset">Reset reconciled ownership</option></select></label>
          <label class="preview-ownership">Simulated ownership JSON (optional)<textarea v-model="form.ownership" rows="3" maxlength="4096" data-testid="preview-ownership" placeholder="Blank assumes no owner" /></label>
        </div>
        <p class="schedule-note" data-testid="controller-preview-scope">Uses the exact HA controller guards for C1000 Gen 2 1.1.4.9. Ownership here is a simulation: blank assumes unowned. Use HA’s charging_policy preview action to check its actual saved owner. Reserve must already be configured.</p>
      </template>
      <label v-if="source !== 'price'" class="preview-toggle"><input v-model="form.exportConfirmed" type="checkbox" data-testid="preview-export-sign" />Positive values in this manual sample mean export to the grid.</label>
      <label class="preview-toggle"><input v-model="form.simulateArmed" type="checkbox" />Evaluate an armed policy (simulation only).</label>
      <label v-if="form.policy !== 'fixed' && !controller" class="preview-toggle"><input v-model="form.latch" type="checkbox" />Simulate a latched policy.</label>
      <p v-if="form.policy === 'fixed'" class="schedule-note">Assumes a clear latch and elapsed 180-second cooldown.</p>
      <p v-if="form.policy === 'price_tou'" class="schedule-note">Requires Standard mode and zero saved tariff slots. Tariff proposals retain saved charging power; previous preview state does not confirm an action ran.</p>
      <p v-if="!controller" class="schedule-note">Exploratory rules differ from HA execution and may propose raising reserve. They do not verify ownership or qualify this model for automation.</p>
      <p class="schedule-note">Prices and thresholds use the same units. Saved settings do not predict actual charging, battery use or zero grid import. This panel sends no station commands.</p>
      <button class="secondary" type="submit" data-testid="run-charging-preview" :disabled="pending || busy">{{ pending ? 'Evaluating…' : 'Preview decision' }}</button>
    </form>
    <p v-if="error" class="validation-error" role="status">{{ error }}</p>
    <div v-if="result" class="preview-result" data-testid="charging-preview-result" role="status">
      <strong>{{ result.eligible ? 'Proposed decision' : 'Blocked' }} · No commands sent</strong>
      <ul><li v-for="reason in explanation" :key="reason">{{ reason }}</li></ul>
      <p v-for="setting in proposals" :key="setting">{{ setting }}</p>
      <p v-for="period in plan" :key="period">{{ period }}</p>
    </div>
  </section>
</template>

<style scoped>
.preview-ownership { grid-column: span 2; }
.preview-ownership textarea { width: 100%; resize: vertical; font: inherit; color: #e6edf7; background: #111923; border: 1px solid #344151; border-radius: 8px; padding: 11px 12px; }
.preview-ownership textarea:focus-visible { outline: 2px solid var(--mint); outline-offset: 3px; }
@media (max-width: 760px) { .preview-ownership { grid-column: auto; } }
</style>
