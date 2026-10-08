<script setup lang="ts">
import { computed } from 'vue';
import type { Command, Draft, Proposal, Station } from './types';
import { numberMetric, planDraft, savedPlan } from './types';

const props = defineProps<{ station: Station; draft: Draft; writable: boolean; now: number }>();
const readback = computed(() => savedPlan(props.station, props.now));
function loadPlan() {
  const plan = savedPlan(props.station, props.now);
  if (plan) props.draft.periods = planDraft(plan);
}
const emit = defineEmits<{ propose: [proposal: Proposal] }>();
const allowed = (command: string) => props.station.controls.includes(command);
const observed = (key: string, unit = '') => {
  const value = props.station.metrics[key];
  return value === undefined || value === null ? 'Not reported' : `${value}${unit}`;
};
const powers = computed(() => {
  if (props.station.model === 'c300') return [100, 200, 300, 330];
  const maximum = props.station.model === 'c2000_gen2' ? 1800 : props.station.model === 'c1000_gen2' ? 1200 : 1000;
  const minimum = ['c1000', 'c1000_gen2'].includes(props.station.model) ? 100 : 300;
  return Array.from({ length: (maximum - minimum) / 100 + 1 }, (_, index) => minimum + index * 100);
});
const reserves = computed(() => {
  const lower = numberMetric(props.station, 'min_charge_percentage');
  const upper = numberMetric(props.station, 'max_charge_percentage');
  if (lower === null || upper === null) return [];
  const minimum = Math.max(5, Math.ceil((lower + 5) / 5) * 5);
  return Array.from({ length: Math.max(0, Math.floor((upper - minimum) / 5) + 1) }, (_, index) => minimum + index * 5);
});
const dischargeFloors = [1, 5, 10, 15, 20];
const floorAvailable = computed(() => props.station.model === 'c1000_gen2' && allowed('set-discharge-floor'));
const floorValid = computed(() => {
  const lower = Number(props.draft.lower);
  const reserve = numberMetric(props.station, 'backup_reserve_percentage');
  const upper = numberMetric(props.station, 'max_charge_percentage');
  return dischargeFloors.includes(lower) && reserve !== null && upper !== null
    && lower + 5 <= reserve && reserve <= upper;
});
const displayTimes = computed(() => nativeC1000.value ? [0, 10, 20, 30, 60, 300, 1800]
  : props.station.model === 'c1000' ? [20, 30, 60, 300, 1800] : [30, 60]);
const originalProfile = computed(() => props.station.model === 'c1000' && props.station.protocol === 'legacy');
const guardedDcSmart = computed(() => props.station.model === 'c1000' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? '')
  || props.station.model === 'c1000_gen2' && props.station.protocol === 'native_mqtt');
const guardedAcSmart = computed(() => props.station.model === 'c1000' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? '')
  || props.station.model === 'c1000_gen2' && props.station.protocol === 'native_mqtt');
const originalPreferenceProfile = computed(() => props.station.model === 'c1000' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? ''));
const nativeC1000 = computed(() => props.station.model === 'c1000_gen2' && props.station.protocol === 'native_mqtt');
const booleanReported = (key: string) => [0, 1].includes(numberMetric(props.station, key) ?? -1);
const brightnesses = ['Low', 'Medium', 'High'];
const brightnessReported = computed(() => [1, 2, 3].includes(numberMetric(props.station, 'display_brightness') ?? -1));
const displayValid = computed(() => /^\d+$/.test(props.draft.seconds) && displayTimes.value.includes(Number(props.draft.seconds))
  && (!(nativeC1000.value || originalPreferenceProfile.value) || displayTimes.value.includes(numberMetric(props.station, 'display_timeout_seconds') ?? -1)));
const portMemoryValid = computed(() => booleanReported('port_memory_enabled') && ['0', '1'].includes(props.draft.portMemory));
const fastAvailable = computed(() => allowed('set-fast-charge') && (originalProfile.value
  || props.station.model === 'c1000' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? '')
  || props.station.model === 'c1000_gen2' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? '')));
const fastCaution = computed(() => props.station.model === 'c1000'
  ? 'Use an adequate AC supply. The flag may clear when AC input is removed; stored readback does not establish charging speed or reboot persistence.'
  : 'Set the station’s fast-charging switch.');
const fastValid = computed(() => booleanReported('ac_fast_charge_enabled') && ['0', '1'].includes(props.draft.fast)
  && (!nativeC1000.value || numberMetric(props.station, 'ac_input_connected') === 1)
  && (props.draft.fast === '0' || props.station.model !== 'c1000_gen2'
    || props.station.metrics.usage_mode === 'standard' && props.station.metrics.active_tariff === 'none'));
const temperatureAvailable = computed(() => allowed('set-temperature-unit') && (originalProfile.value || originalPreferenceProfile.value || nativeC1000.value));
const savingPorts = ['ac', 'dc'] as const;
const savingProfile = (port: 'ac' | 'dc') => originalProfile.value
  || guardedDcSmart.value && port === 'dc' || guardedAcSmart.value && port === 'ac';
const dcSmartReady = computed(() => numberMetric(props.station, 'dc_output_enabled') === 0
  && (!nativeC1000.value || props.station.metrics.software_version === '1.1.4.9'
    && numberMetric(props.station, 'ac_output_timeout_seconds') === 0
    && numberMetric(props.station, 'dc_output_timeout_seconds') === 0));
const acSmartReady = computed(() => numberMetric(props.station, 'ac_output_enabled') === 0
  && (nativeC1000.value ? props.station.metrics.software_version === '1.1.4.9'
    && numberMetric(props.station, 'ac_output_timeout_seconds') === 0 && numberMetric(props.station, 'dc_output_timeout_seconds') === 0
    : numberMetric(props.station, 'ac_output_timer_remaining_seconds') === 0));
const clockWindows = [{ window: 1, draft: 'clockFirst', metric: 'clock_screen_first_brightness_flag_raw' },
  { window: 2, draft: 'clockSecond', metric: 'clock_screen_second_brightness_flag_raw' }] as const;
const clockReady = computed(() => nativeC1000.value && props.station.metrics.software_version === '1.1.4.9'
  && props.station.metrics.usage_mode === 'standard' && props.station.metrics.active_tariff === 'none'
  && ['clock_screen_enabled', 'clock_screen_transfer_status_raw', 'ac_output_timeout_seconds', 'dc_output_timeout_seconds'].every((key) => numberMetric(props.station, key) === 0)
  && clockWindows.every((item) => booleanReported(item.metric)));
const guardedSaving = (port: 'ac' | 'dc') => port === 'dc' ? guardedDcSmart.value : guardedAcSmart.value;
const savingValid = (port: 'ac' | 'dc') => booleanReported(`${port}_power_saving_mode_enabled`)
  && (!(guardedDcSmart.value && port === 'dc') || dcSmartReady.value)
  && (!(guardedAcSmart.value && port === 'ac') || acSmartReady.value)
  && ['0', '1'].includes(props.draft[port === 'ac' ? 'acSaving' : 'dcSaving']);
function powerSaving(port: 'ac' | 'dc') {
  if (!savingProfile(port) || !savingValid(port)) return;
  const enabled = props.draft[port === 'ac' ? 'acSaving' : 'dcSaving'] === '1';
  const guarded = guardedDcSmart.value && port === 'dc' || guardedAcSmart.value && port === 'ac';
  propose({ command: `set-${port}-power-saving`, enabled }, guarded ? `Change ${port.toUpperCase()} Smart mode?` : `Change ${port.toUpperCase()} power saving?`,
    guarded ? `Requires fresh ${port.toUpperCase()} output OFF${nativeC1000.value ? ', main 1.1.4.9 and inactive AC/DC countdowns' : port === 'ac' ? ' and an inactive AC countdown' : ''}. Smart may inherit an inactivity counter and later turn ${port.toUpperCase()} output off at low load; enabling does not guarantee a new grace period.`
      : 'Power saving may automatically turn the output off at low load.',
    [guarded ? enabled ? 'Smart' : 'Normal' : enabled ? 'On' : 'Off', `Applies to the ${port.toUpperCase()} output`]);
}
const deviceTimeouts = [0, 30, 60, 120, 240, 360, 720, 1440];
const timeoutProfile = computed(() => props.station.model === 'c1000' && ['legacy', 'prime', 'native_mqtt'].includes(props.station.protocol ?? '')
  || props.station.model === 'c1000_gen2' && ['prime', 'native_mqtt'].includes(props.station.protocol ?? ''));
const timeoutAvailable = computed(() => timeoutProfile.value && allowed('set-device-timeout'));
const timeoutReported = computed(() => {
  const value = numberMetric(props.station, 'device_timeout_minutes');
  return value !== null && Number.isInteger(value) && deviceTimeouts.includes(value);
});
const timeoutValid = computed(() => /^\d+$/.test(props.draft.timeoutMinutes)
  && deviceTimeouts.includes(Number(props.draft.timeoutMinutes)));
const timeoutLabel = (minutes: number) => minutes === 0 ? 'Never' : minutes === 30 ? '30 minutes' : `${minutes / 60} ${minutes === 60 ? 'hour' : 'hours'}`;
function timeout() {
  if (!timeoutReported.value || !timeoutValid.value) return;
  const minutes = Number(props.draft.timeoutMinutes);
  propose({ command: 'set-device-timeout', minutes }, 'Change Device Timeout?',
    minutes ? 'The station may turn off when idle, interrupting remote access.' : 'Never disables this timeout; other sleep behavior may still interrupt remote access.',
    [timeoutLabel(minutes), ...(minutes ? ['Never disables this timeout; other sleep behavior may still interrupt remote access.'] : []),
      'An already armed sleep timer may remain until normal wake or reset.']);
}
const lights = computed(() => props.station.model === 'c1000' ? ['Off', 'Low', 'Medium', 'High', 'SOS'] : ['Off', 'Low', 'Medium', 'High']);
const lightReported = computed(() => Number.isInteger(numberMetric(props.station, 'light_mode'))
  && Boolean(lights.value[numberMetric(props.station, 'light_mode') ?? -1]));
const clock = computed(() => {
  if (!props.station.timezone_name) return 'Timezone unknown';
  try { return new Intl.DateTimeFormat([], { timeZone: props.station.timezone_name, hour: '2-digit', minute: '2-digit' }).format(new Date()); }
  catch { return 'Timezone unknown'; }
});
const planError = computed(() => {
  if (props.draft.periods.length > 6) return 'Use no more than six periods.';
  const periods = props.draft.periods;
  for (const period of periods) {
    if (!['peak', 'mid_peak', 'off_peak'].includes(period.tariff)
      || !/^\d{1,2}$/.test(period.start) || !/^\d{1,2}$/.test(period.end)
      || Number(period.start) < 0 || Number(period.start) >= Number(period.end) || Number(period.end) > 24) {
      return 'Each period needs whole hours with 0 ≤ start < end ≤ 24. Split overnight periods at midnight.';
    }
  }
  const sorted = [...periods].sort((a, b) => Number(a.start) - Number(b.start));
  if (sorted.some((period, index) => index > 0 && Number(period.start) < Number(sorted[index - 1]!.end))) return 'Periods must not overlap.';
  return '';
});

function propose(body: Command, title: string, detail: string, summary: string[]) {
  if (!props.writable || !allowed(body.command)) return;
  emit('propose', { station: props.station.name, body, title, detail, summary });
}

function plan(enabled: boolean) {
  if (planError.value || (enabled && !props.draft.periods.length)) return;
  const periods = props.draft.periods.map((period) => ({ tariff: period.tariff, start_hour: Number(period.start), end_hour: Number(period.end) }));
  propose({ command: 'set-tou-plan', periods, enabled }, enabled ? 'Activate hourly plan?' : 'Save plan in Standard mode?',
    enabled ? 'This replaces the station schedule and activates Time-of-Use. It persists until you change the plan or return to grid.'
      : 'This replaces the station schedule in Standard mode. Use Return to grid to confirm grid supply.',
    periods.length ? periods.map((period) => `${period.tariff.replace('_', ' ')} · ${period.start_hour}:00–${period.end_hour}:00`) : ['Clear all schedule periods']);
}

function addPeriod() {
  if (props.draft.periods.length >= 6) return;
  props.draft.periods.push({ id: Math.max(0, ...props.draft.periods.map((period) => period.id)) + 1,
    tariff: 'off_peak', start: '0', end: '24' });
}
</script>

<template>
  <section class="panel controls-panel">
    <div class="panel-heading"><div><p class="eyebrow">Station settings</p><h2>Charging & preferences</h2></div><span class="tag">Confirm before applying</span></div>
    <p v-if="!station.controls.length" class="empty-note">This gateway is read-only. Monitoring remains available.</p>
    <p v-else-if="!writable" class="disabled-note">Controls need a fresh connection and no command in progress.</p>
    <div class="settings-grid">
      <div v-if="allowed('set-charge-power')" class="setting">
        <label for="charging-power">AC charging power</label><p>Current {{ observed('ac_charging_power_limit_w', ' W') }}</p>
        <div class="setting-input"><select id="charging-power" v-model="draft.watts" :disabled="!writable"><option v-for="watts in powers" :key="watts" :value="String(watts)">{{ watts }} W</option></select>
          <button class="secondary" :disabled="!writable || !powers.includes(Number(draft.watts))" @click="propose({ command: 'set-charge-power', watts: Number(draft.watts) }, 'Change charging power?', 'Set the station’s AC charging-power limit.', [`${draft.watts} W`])">Apply</button></div>
      </div>
      <div v-if="allowed('set-charge-cap')" class="setting">
        <label for="charge-cap">Upper charge limit</label><p>Current {{ observed('max_charge_percentage', '%') }}</p>
        <div class="setting-input"><select id="charge-cap" v-model="draft.upper" :disabled="!writable"><option v-for="upper in [80, 85, 90, 95, 100]" :key="upper" :value="String(upper)">{{ upper }}%</option></select>
          <button class="secondary" :disabled="!writable || ![80, 85, 90, 95, 100].includes(Number(draft.upper)) || Number(draft.upper) < (numberMetric(station, 'backup_reserve_percentage') ?? 0)" @click="propose({ command: 'set-charge-cap', upper: Number(draft.upper) }, 'Change upper charge limit?', 'Set the maximum battery charge percentage.', [`${draft.upper}%`])">Apply</button></div>
      </div>
      <div v-if="allowed('set-backup-reserve')" class="setting">
        <label for="backup-reserve">Backup reserve</label><p>Current {{ observed('backup_reserve_percentage', '%') }}</p>
        <div class="setting-input"><select id="backup-reserve" v-model="draft.reserve" :disabled="!writable || !reserves.length"><option v-for="reserve in reserves" :key="reserve" :value="String(reserve)">{{ reserve }}%</option></select>
          <button class="secondary" :disabled="!writable || !reserves.includes(Number(draft.reserve))" @click="propose({ command: 'set-backup-reserve', reserve: Number(draft.reserve) }, 'Change backup reserve?', 'Set reserve within the station’s current charge limits.', [`${draft.reserve}%`])">Apply</button></div>
      </div>
      <div v-if="floorAvailable" class="setting">
        <label for="discharge-floor">Lower discharge limit</label><p>Current {{ observed('min_charge_percentage', '%') }}</p>
        <div class="setting-input"><select id="discharge-floor" v-model="draft.lower" :disabled="!writable"><option v-for="lower in dischargeFloors" :key="lower" :value="String(lower)">{{ lower }}%</option></select>
          <button class="secondary" :disabled="!writable || !floorValid" @click="propose({ command: 'set-discharge-floor', lower: Number(draft.lower) }, 'Change lower discharge limit?', 'Set the station’s lower discharge limit.', [`${draft.lower}% lower limit`, `Observed backup reserve: ${observed('backup_reserve_percentage', '%')}`])">Apply</button></div>
        <p v-if="!floorValid" class="validation-error">Reserve must be at least 5 percentage points above this limit and within the upper charge limit.</p>
      </div>
      <div v-if="allowed('set-display-timeout')" class="setting">
        <label for="display-timeout">Screen timeout</label><p>Current {{ observed('display_timeout_seconds', ' s') }}</p>
        <p v-if="station.model === 'c2000_gen2' && station.protocol === 'native_mqtt'">30/60 seconds only. MQTT support awaits a device test.</p>
        <div class="setting-input"><select id="display-timeout" v-model="draft.seconds" :disabled="!writable"><option v-for="seconds in displayTimes" :key="seconds" :value="String(seconds)">{{ seconds === 0 ? 'Never' : `${seconds} seconds` }}</option></select>
          <button class="secondary" :disabled="!writable || !displayValid" @click="propose({ command: 'set-display-timeout', seconds: Number(draft.seconds) }, 'Change screen timeout?', 'Set the display timeout.', [draft.seconds === '0' ? 'Never' : `${draft.seconds} seconds`])">Apply</button></div>
      </div>
      <div v-if="(nativeC1000 || originalPreferenceProfile) && allowed('set-display-brightness')" class="setting">
        <label for="display-brightness">Display brightness</label><p>Current {{ brightnesses[(numberMetric(station, 'display_brightness') ?? 0) - 1] ?? 'Not reported' }}</p>
        <div class="setting-input"><select id="display-brightness" v-model="draft.brightness" :disabled="!writable || !brightnessReported"><option v-for="(label, index) in brightnesses" :key="label" :value="String(index + 1)">{{ label }}</option></select>
          <button class="secondary" :disabled="!writable || !brightnessReported || !['1', '2', '3'].includes(draft.brightness)" @click="propose({ command: 'set-display-brightness', level: Number(draft.brightness) }, 'Change display brightness?', 'Set the saved display brightness level.', [brightnesses[Number(draft.brightness) - 1] ?? 'Not reported'])">Apply</button></div>
      </div>
      <div v-if="nativeC1000 && allowed('set-port-memory')" class="setting">
        <label for="port-memory">Output port memory</label><p>Current {{ numberMetric(station, 'port_memory_enabled') === 1 ? 'On' : numberMetric(station, 'port_memory_enabled') === 0 ? 'Off' : 'Not reported' }}</p>
        <div class="setting-input"><select id="port-memory" v-model="draft.portMemory" :disabled="!writable || !booleanReported('port_memory_enabled')"><option value="0">Off</option><option value="1">On</option></select>
          <button class="secondary" :disabled="!writable || !portMemoryValid" @click="propose({ command: 'set-port-memory', enabled: draft.portMemory === '1' }, 'Change output port memory?', 'Off clears output-recovery bookkeeping; turning On does not restore that transient state.', [draft.portMemory === '1' ? 'On' : 'Off'])">Apply</button></div>
        <p class="hint">Off clears output-recovery bookkeeping; turning On does not restore that transient state.</p>
      </div>
      <div v-if="timeoutAvailable" class="setting">
        <label for="device-timeout">Device Timeout</label><p>Current {{ timeoutReported ? timeoutLabel(Number(station.metrics.device_timeout_minutes)) : 'Not reported' }}</p>
        <div class="setting-input"><select id="device-timeout" v-model="draft.timeoutMinutes" :disabled="!writable || !timeoutReported"><option v-for="minutes in deviceTimeouts" :key="minutes" :value="String(minutes)">{{ timeoutLabel(minutes) }}</option></select>
          <button class="secondary" :disabled="!writable || !timeoutReported || !timeoutValid" @click="timeout">Apply</button></div>
        <p v-if="draft.timeoutMinutes === '0'" class="hint">Never disables this timeout; other sleep behavior may still interrupt remote access.</p>
        <p v-if="draft.timeoutMinutes !== '' && draft.timeoutMinutes !== '0'" class="validation-error">The station may turn off when idle, interrupting remote access.</p>
      </div>
      <div v-if="fastAvailable" class="setting">
        <label for="fast-charge">Fast charging</label><p>Current {{ numberMetric(station, 'ac_fast_charge_enabled') === 1 ? 'On' : numberMetric(station, 'ac_fast_charge_enabled') === 0 ? 'Off' : 'Not reported' }}</p>
        <div class="setting-input"><select id="fast-charge" v-model="draft.fast" :disabled="!writable"><option value="0">Off</option><option value="1">On</option></select>
          <button class="secondary" :disabled="!writable || !fastValid" @click="propose({ command: 'set-fast-charge', enabled: draft.fast === '1' }, 'Change fast charging?', fastCaution, [draft.fast === '1' ? 'On' : 'Off'])">Apply</button></div>
        <p v-if="station.model === 'c1000_gen2'" class="hint">Enabling requires Standard mode with no active tariff. Native MQTT also requires connected mains.</p>
        <p v-else-if="station.model === 'c1000'" class="hint">{{ fastCaution }}</p>
      </div>
      <template v-if="nativeC1000 && allowed('set-clock-brightness')">
        <div v-for="item in clockWindows" :key="item.window" class="setting">
          <label :for="`clock-brightness-${item.window}`">Clock window {{ item.window }} brightness</label>
          <p>Current {{ numberMetric(station, item.metric) === 1 ? 'High' : numberMetric(station, item.metric) === 0 ? 'Normal' : 'Not reported' }}</p>
          <div class="setting-input"><select :id="`clock-brightness-${item.window}`" v-model="draft[item.draft]" :disabled="!writable || !clockReady"><option value="0">Normal</option><option value="1">High</option></select>
            <button class="secondary" :disabled="!writable || !clockReady || !['0', '1'].includes(draft[item.draft])" @click="propose({ command: 'set-clock-brightness', window: item.window, high: draft[item.draft] === '1' }, 'Change clock-window brightness?', 'Changes one saved selector while the clock is disabled. Does not enable the clock or change its theme or assets.', [`Window ${item.window}`, draft[item.draft] === '1' ? 'High' : 'Normal'])">Apply</button></div>
          <p class="hint">Saved window setting; requires disabled clock, no asset transfer and inactive countdowns.</p>
        </div>
      </template>
      <div v-if="allowed('set-light')" class="setting">
        <label for="light-mode">Light</label><p>Current {{ lights[numberMetric(station, 'light_mode') ?? -1] ?? 'Not reported' }}</p>
        <div class="setting-input"><select id="light-mode" v-model="draft.light" :disabled="!writable || !lightReported"><option v-for="(light, mode) in lights" :key="mode" :value="String(mode)">{{ light }}</option></select>
          <button class="secondary" :disabled="!writable || !lightReported || !lights[Number(draft.light)]" @click="propose({ command: 'set-light', mode: Number(draft.light) }, 'Change light mode?', 'Set the station light.', [lights[Number(draft.light)] ?? 'Off'])">Apply</button></div>
      </div>
      <div v-if="temperatureAvailable" class="setting">
        <label for="temperature-unit">Temperature display</label><p>Current {{ numberMetric(station, 'temperature_unit_fahrenheit') === 1 ? 'Fahrenheit' : numberMetric(station, 'temperature_unit_fahrenheit') === 0 ? 'Celsius' : 'Not reported' }}</p>
        <div class="setting-input"><select id="temperature-unit" v-model="draft.fahrenheit" :disabled="!writable"><option value="0">Celsius · °C</option><option value="1">Fahrenheit · °F</option></select>
          <button class="secondary" :disabled="!writable || !booleanReported('temperature_unit_fahrenheit') || !['0', '1'].includes(draft.fahrenheit)" @click="propose({ command: 'set-temperature-unit', fahrenheit: draft.fahrenheit === '1' }, 'Change temperature display?', 'Set the station’s temperature display unit.', [draft.fahrenheit === '1' ? 'Fahrenheit' : 'Celsius'])">Apply</button></div>
      </div>
      <div v-if="allowed('set-off-grid-alert')" class="setting">
        <label for="off-grid-alert">Off-grid alert</label><p>Current {{ numberMetric(station, 'ac_off_grid_alert_enabled') === 1 ? 'On' : numberMetric(station, 'ac_off_grid_alert_enabled') === 0 ? 'Off' : 'Not reported' }}</p>
        <div class="setting-input"><select id="off-grid-alert" v-model="draft.alert" :disabled="!writable"><option value="0">Off</option><option value="1">On</option></select>
          <button class="secondary" :disabled="!writable" @click="propose({ command: 'set-off-grid-alert', enabled: draft.alert === '1' }, 'Change off-grid alert?', 'Set the station’s AC off-grid alert preference.', [draft.alert === '1' ? 'On' : 'Off'])">Apply</button></div>
      </div>
      <template v-for="port in savingPorts" :key="port">
        <div v-if="savingProfile(port) && allowed(`set-${port}-power-saving`)" class="setting">
          <label :for="`${port}-power-saving`">{{ guardedSaving(port) ? `${port.toUpperCase()} Smart mode` : `${port.toUpperCase()} power saving` }}</label><p>Current {{ numberMetric(station, `${port}_power_saving_mode_enabled`) === 1 ? guardedSaving(port) ? 'Smart' : 'On' : numberMetric(station, `${port}_power_saving_mode_enabled`) === 0 ? guardedSaving(port) ? 'Normal' : 'Off' : 'Not reported' }}</p>
          <div class="setting-input"><select :id="`${port}-power-saving`" v-model="draft[port === 'ac' ? 'acSaving' : 'dcSaving']" :disabled="!writable || !booleanReported(`${port}_power_saving_mode_enabled`) || guardedSaving(port) && !(port === 'dc' ? dcSmartReady : acSmartReady)"><option value="0">{{ guardedSaving(port) ? 'Normal' : 'Off' }}</option><option value="1">{{ guardedSaving(port) ? 'Smart' : 'On' }}</option></select>
            <button class="secondary" :disabled="!writable || !savingValid(port)" @click="powerSaving(port)">Apply</button></div>
          <p class="validation-error">Power saving may automatically turn the output off at low load.</p>
          <p v-if="guardedSaving(port)" class="validation-error">Requires fresh {{ port.toUpperCase() }} output OFF{{ nativeC1000 ? ', main 1.1.4.9 and inactive AC/DC countdowns' : port === 'ac' ? ' and an inactive AC countdown' : '' }}. Smart may inherit an inactivity counter; enabling does not guarantee a new grace period.</p>
        </div>
      </template>
    </div>
  </section>

  <section v-if="allowed('set-tou-plan') || allowed('return-grid') || station.tou_plan_readback" class="panel schedule-panel">
    <div class="panel-heading"><div><p class="eyebrow">Energy scheduling</p><h2>Hourly plan <span class="draft-label">Draft</span></h2></div><span class="tag">{{ station.timezone_name || 'Timezone unknown' }} · {{ clock }}</span></div>
    <p class="schedule-note" data-testid="saved-plan-status">{{ readback ? `Fresh saved plan · ${readback.enabled ? 'Enabled' : 'Stored in Standard'} · ${readback.periods.length} periods` : 'Saved plan readback unavailable or stale.' }} Changes persist until you replace the plan or return to grid.</p>
    <ul v-if="readback" data-testid="saved-plan-periods"><li v-for="(period, i) in readback.periods" :key="i">{{ period.tariff.replace('_', ' ') }} · {{ period.start_hour }}:00–{{ period.end_hour }}:00</li></ul>
    <button class="secondary" data-testid="load-saved-plan" :disabled="!readback" @click="loadPlan">Load saved plan into draft</button>
    <div v-if="allowed('set-tou-plan') || station.tou_plan_readback" class="plan-editor">
      <div v-if="!draft.periods.length" class="empty-plan"><span class="empty-symbol">⌁</span><strong>No periods in this draft</strong><span>Add whole-hour periods, or save this empty draft to clear the schedule.</span></div>
      <div v-for="(period, index) in draft.periods" :key="period.id" class="period-row">
        <span class="period-index">{{ String(index + 1).padStart(2, '0') }}</span>
        <label><span>Tariff</span><select v-model="period.tariff" :disabled="!writable"><option value="off_peak">Off-Peak · grid / charge</option><option value="mid_peak">Mid-Peak · grid</option><option value="peak">Peak · battery</option></select></label>
        <label><span>From</span><input v-model="period.start" type="number" min="0" max="23" step="1" :disabled="!writable" /></label>
        <label><span>Until</span><input v-model="period.end" type="number" min="1" max="24" step="1" :disabled="!writable" /></label>
        <button class="icon-button" :aria-label="`Remove period ${index + 1}`" :disabled="!writable" @click="draft.periods.splice(index, 1)">×</button>
      </div>
      <p v-if="planError" class="validation-error" role="status">{{ planError }}</p>
      <div class="plan-actions">
        <button class="secondary" :disabled="!writable || draft.periods.length >= 6" @click="addPeriod">＋ Add period</button>
        <div class="plan-actions-right"><button class="secondary" :disabled="!writable || !!planError" @click="plan(false)">Save in Standard</button><button class="primary" :disabled="!writable || !!planError || !draft.periods.length" @click="plan(true)">Activate plan</button></div>
      </div>
    </div>
    <div v-if="allowed('return-grid')" class="grid-return"><div><strong>Return to grid</strong><p>Clear the plan and wait for observed grid supply. AC output stays enabled.</p></div><button class="secondary" :disabled="!writable" @click="propose({ command: 'return-grid', timeout: 30 }, 'Return to grid power?', 'Clear the hourly plan and wait for fresh telemetry to confirm grid supply. Charging limits stay unchanged.', ['Keep AC output enabled', 'Clear the saved hourly plan', 'Confirm actual grid supply'])">Return to grid <span aria-hidden="true">↗</span></button></div>
  </section>
</template>
