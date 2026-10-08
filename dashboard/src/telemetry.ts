import type { Station } from './types';
import { numberMetric, telemetryFresh } from './types';

export interface TelemetryRow { key: string; label: string; value: string; }
const ports: [string, string, string[]][] = [
  ['dc_input_power_w', 'DC input', ['c1000']], ['solar_input_power_w', 'Solar input', ['c300']],
  ['input_power_w', 'Total input', ['c300']], ['dc_output_power_w', 'DC output', ['c1000', 'c300', 'c1000_gen2', 'c2000_gen2']],
  ['usb_c1_power_w', 'USB-C1', ['c1000', 'c300']], ['usb_c2_power_w', 'USB-C2', ['c1000', 'c300']],
  ['usb_c3_power_w', 'USB-C3', ['c300']], ['usb_a1_power_w', 'USB-A1', ['c1000', 'c300']], ['usb_a2_power_w', 'USB-A2', ['c1000']],
];
const versions = [['software_version_controller', 'Controller firmware'], ['software_version_inverter', 'Inverter firmware'],
  ['software_version_bms', 'BMS firmware'], ['software_version_module', 'Radio/module firmware']];

export function telemetryRows(station: Station, now: number): TelemetryRow[] {
  const rows: TelemetryRow[] = [];
  const fresh = telemetryFresh(station, now);
  const add = (key: string, label: string, value: string) => rows.push({ key, label, value });
  if (['c1000', 'c2000_gen2'].includes(station.model)) {
    const present = station.metrics.expansion_battery_count;
    if (present === 0 || present === 1) {
      add('expansion_battery_present', 'Expansion pack', fresh ? present ? 'Present' : 'Absent' : 'Unavailable');
      if (present === 1 && station.model === 'c1000') {
        for (const [key, label, low, high, unit] of [
          ['expansion_battery_percentage', 'Expansion battery', 0, 100, '%'],
          ['expansion_temperature_c', 'Expansion temperature', -40, 125, '°C'],
        ] as const) {
          const value = numberMetric(station, key);
          add(key, label, fresh && value !== null && Number.isInteger(value) && value >= low && value <= high ? `${value} ${unit}` : 'Unavailable');
        }
      }
    }
  }
  for (const [key, label, models] of ports) {
    if (!models.includes(station.model) || !(key in station.metrics)) continue;
    const value = numberMetric(station, key);
    add(key, label, fresh && value !== null && value >= 0 ? `${value} W` : 'Unavailable');
  }
  if ('time_remaining_minutes' in station.metrics) {
    const value = numberMetric(station, 'time_remaining_minutes');
    const activity = station.metrics.battery_status;
    const limit = ['c1000', 'c300'].includes(station.model) ? 5994 : 65534 * 6;
    const valid = fresh && value !== null && Number.isInteger(value) && value > 0 && value <= limit && value % 6 === 0
      && (station.model === 'c1000' || activity === 'charging' || activity === 'discharging');
    const label = activity === 'charging' ? 'Time to full (estimate)' : activity === 'discharging' ? 'Time to empty (estimate)' : 'Remaining time (estimate)';
    add('time_remaining_minutes', label, valid ? `${value} min` : 'Unavailable');
  }
  if (['c1000', 'c2000_gen2', 'c300'].includes(station.model)) {
    for (const channel of ['ac', 'dc']) {
      const key = `${channel}_output_timer_remaining_seconds`;
      if (!(key in station.metrics)) continue;
      const value = numberMetric(station, key);
      const valid = fresh && value !== null && Number.isInteger(value) && value >= 0 && value < 0xffffffff;
      add(key, `${channel.toUpperCase()} countdown remaining`, valid ? value === 0 ? 'No active countdown' : `${value} s` : 'Unavailable');
    }
  }
  for (const [key, label] of versions) {
    if (!key || !label || !(key in station.metrics)
      || !(station.model === 'c2000_gen2' || station.model === 'c1000_gen2' && key === 'software_version_module')) continue;
    const value = station.metrics[key];
    add(key, label, fresh && typeof value === 'string' && value.length <= 24 && /^[0-9]{1,3}(?:\.[0-9]{1,3}){1,4}$/.test(value) ? value : 'Unavailable');
  }
  const signal = station.wifi_signal;
  const raw = station.original_counters;
  if (station.model === 'c1000' && station.protocol === 'native_mqtt' && raw && typeof raw === 'object') {
    const report = raw as Record<string, unknown>;
    const age = typeof report.reported_at === 'number' ? now / 1000 - report.reported_at : NaN;
    const reports = report.reports;
    if (report.schema_version === 1 && report.protobuf_name === 'charging_pps_series_c_0002'
      && report.units_verified === false && report.layout_provenance === 'main_1_5_9_encoder'
      && Array.isArray(reports) && reports.length > 0 && reports.length <= 32) {
      if (reports.length !== 1) add('original_counter_batch', 'Original report counters', 'Batch ordering unknown');
      else {
        const counters = reports[0]?.counters;
        if (counters && typeof counters === 'object') {
          for (let index = 1; index <= 8; index++) {
            const key = `counter_${index}_raw`;
            if (!(key in counters)) continue;
            const value = counters[key];
            const valid = fresh && Number.isFinite(age) && age >= -5 && age < 7200
              && typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
            add(`original_${key}`, `Original counter ${index} (raw; units unknown)`, valid ? String(value) : 'Unavailable');
          }
        }
      }
    }
  }
  if (station.model === 'c1000_gen2' && station.protocol === 'native_mqtt' && signal && typeof signal === 'object') {
    const report = signal as Record<string, unknown>;
    const age = typeof report.observed_at === 'number' ? now / 1000 - report.observed_at : NaN;
    const value = report.wifi_rssi_dbm;
    const valid = fresh && report.schema_version === 1 && report.source === 'radio_ap_info'
      && report.main_version === '1.1.4.9' && report.radio_version === '0.3.3.0' && report.settings_unchanged === true
      && Number.isFinite(age) && age >= -5 && age < 600 && typeof value === 'number' && Number.isInteger(value) && value >= -128 && value < 0;
    add('wifi_rssi_dbm', 'Wi-Fi signal (recent query)', valid ? `${value} dBm` : 'Unavailable');
  }
  return rows;
}
