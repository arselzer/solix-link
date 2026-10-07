export type Metric = number | string | boolean | null;

export interface TouPlanReadback {
  schema_version: 1;
  enabled: boolean;
  periods: { tariff: string; start_hour: number; end_hour: number }[];
  reported_at: number;
  source: 'status_d9';
}

export interface Station {
  name: string;
  model: string;
  protocol?: string;
  connected: boolean;
  available: boolean;
  last_seen_timestamp: number | null;
  power_flow?: string;
  timezone_name?: string | null;
  tou_plan_readback?: TouPlanReadback | null;
  native_energy?: unknown;
  ups_state?: { battery_reserve_low?: boolean | null };
  command_context?: { schema_version: 1; gateway_instance: string; issued_at: number;
    request_window_seconds: number; busy: boolean; preconditions_supported: boolean; expected: Record<string, unknown> };
  control_availability?: { schema_version: 1; commands: { command: string; advertised: boolean;
    permitted: boolean; ready: boolean; reasons: string[]; missing_metrics: string[] }[] };
  controls: string[];
  metrics: Record<string, Metric>;
}

export interface Sample {
  time: number;
  input: number | null;
  output: number | null;
  battery: number | null;
  gap?: boolean;
}

export type Command = { command: string } & Record<string, unknown>;
export interface Proposal {
  station: string;
  body: Command;
  title: string;
  detail: string;
  summary: string[];
}

export interface DraftPeriod {
  id: number;
  tariff: string;
  start: string;
  end: string;
}

export interface Draft {
  watts: string;
  upper: string;
  lower: string;
  reserve: string;
  seconds: string;
  brightness: string;
  portMemory: string;
  timeoutMinutes: string;
  fast: string;
  light: string;
  fahrenheit: string;
  alert: string;
  acSaving: string;
  dcSaving: string;
  clockFirst: string;
  clockSecond: string;
  periods: DraftPeriod[];
}

export function numberMetric(station: Station, key: string): number | null {
  const value = station.metrics[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function powerMetric(station: Station, direction: 'input' | 'output'): number | null {
  const total = numberMetric(station, `total_${direction}_power_w`) ?? numberMetric(station, `${direction}_power_w`);
  if (total !== null) return total;
  const ac = numberMetric(station, `ac_${direction}_power_w`);
  const dc = numberMetric(station, `dc_${direction}_power_w`)
    ?? (direction === 'input' ? numberMetric(station, 'solar_input_power_w') : null);
  return ac === null && dc === null ? null : (ac ?? 0) + (dc ?? 0);
}

export function telemetryFresh(station: Station, now: number): boolean {
  const latest = station.last_seen_timestamp;
  const age = typeof latest === 'number' ? now / 1000 - latest : NaN;
  return station.available && station.connected && Number.isFinite(age)
    && age >= -5 && age < (station.protocol === 'native_mqtt' ? 30 : 90);
}

export function modelLabel(model: string): string {
  return { c300: 'C300 AC', c1000: 'C1000', c1000_gen2: 'C1000 Gen 2', c2000_gen2: 'C2000 Gen 2' }[model] ?? 'SOLIX station';
}

export function savedPlan(station: Station, now: number): TouPlanReadback | null {
  const plan = station.tou_plan_readback;
  if (!['c1000_gen2', 'c2000_gen2'].includes(station.model) || station.protocol !== 'native_mqtt'
    || !telemetryFresh(station, now) || !plan || plan.schema_version !== 1 || typeof plan.enabled !== 'boolean'
    || plan.source !== 'status_d9' || !Number.isFinite(plan.reported_at)
    || now / 1000 - plan.reported_at < -5 || now / 1000 - plan.reported_at >= 30
    || !Array.isArray(plan.periods) || plan.periods.length > 6 || plan.enabled && !plan.periods.length) return null;
  if (plan.periods.some((p) => !p || !['peak', 'mid_peak', 'off_peak'].includes(p.tariff)
    || !Number.isInteger(p.start_hour) || !Number.isInteger(p.end_hour)
    || p.start_hour < 0 || p.start_hour >= p.end_hour || p.end_hour > 24)) return null;
  const sorted = [...plan.periods].sort((a, b) => a.start_hour - b.start_hour);
  if (sorted.some((p, i) => i > 0 && sorted[i - 1]!.end_hour > p.start_hour)) return null;
  return plan;
}

export function planDraft(plan: TouPlanReadback): DraftPeriod[] {
  return plan.periods.map((period, i) => ({ id: i, tariff: period.tariff,
    start: String(period.start_hour), end: String(period.end_hour) }));
}

export function draftFor(station: Station): Draft {
  const current = (key: string, fallback: string) => String(station.metrics[key] ?? fallback);
  return {
    watts: current('ac_charging_power_limit_w', station.model === 'c300' ? '300' : '800'),
    upper: current('max_charge_percentage', '100'),
    lower: current('min_charge_percentage', '1'),
    reserve: current('backup_reserve_percentage', '10'),
    seconds: current('display_timeout_seconds', '30'),
    brightness: current('display_brightness', ''),
    portMemory: current('port_memory_enabled', ''),
    timeoutMinutes: current('device_timeout_minutes', ''),
    fast: current('ac_fast_charge_enabled', '0'),
    light: current('light_mode', '0'),
    fahrenheit: current('temperature_unit_fahrenheit', '0'),
    alert: current('ac_off_grid_alert_enabled', '0'),
    acSaving: current('ac_power_saving_mode_enabled', ''),
    dcSaving: current('dc_power_saving_mode_enabled', ''),
    clockFirst: current('clock_screen_first_brightness_flag_raw', ''),
    clockSecond: current('clock_screen_second_brightness_flag_raw', ''),
    periods: planDraft(savedPlan(station, Date.now()) ?? { schema_version: 1, enabled: false,
      periods: [], reported_at: 0, source: 'status_d9' }),
  };
}
