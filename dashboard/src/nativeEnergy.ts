import type { Station } from './types';

export const groups = ['time_of_use', 'standard', 'backup_variant_1', 'backup_variant_2'] as const;
export const channels = ['ac_input', 'ac_output', 'dc_input', 'other_output'] as const;
const rawKeys = [...channels.map((channel) => `${channel}_energy_raw`),
  'ac_output_duration_raw', 'ac_charge_duration_raw', 'other_output_duration_raw', 'dc_charge_duration_raw'];

export interface NativeEnergy {
  schema_version: 1;
  source: 'device_energy_report';
  model: string;
  firmware_version: string | null;
  units_verified: false;
  reported_at: number;
  counter_epoch_started_at: number;
  counter_epoch: number;
  received_reports: number;
  batch_reports: number;
  continuity: string;
  conversion_basis: 'nominal_wh' | 'assumed_wh';
  available: boolean;
  max_report_age_seconds: 1800;
  groups: Record<string, { raw: Record<string, number>; energy_kwh: Record<string, number> }>;
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const timestamp = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 253402300800;

export function nativeEnergy(station: Station, now: number): NativeEnergy | null {
  const value = station.native_energy;
  if (station.protocol !== 'native_mqtt' || !['c1000_gen2', 'c2000_gen2'].includes(station.model)
    || !object(value) || value.schema_version !== 1 || value.source !== 'device_energy_report'
    || value.units_verified !== false || value.model !== station.model || !timestamp(value.reported_at)
    || !timestamp(value.counter_epoch_started_at) || value.counter_epoch_started_at > value.reported_at
    || !integer(value.counter_epoch) || value.counter_epoch < 1
    || !integer(value.received_reports) || value.received_reports < 1
    || !integer(value.batch_reports) || value.batch_reports < 1 || value.batch_reports > 32 || value.batch_reports > value.received_reports
    || typeof value.continuity !== 'string' || !['first_report', 'increasing', 'counter_decreased', 'batch_order_unknown'].includes(value.continuity)
    || value.firmware_version !== null && (typeof value.firmware_version !== 'string' || !/^[0-9]{1,3}(?:\.[0-9]{1,3}){1,5}$/.test(value.firmware_version))
    || !object(value.groups) || !Object.keys(value.groups).length || Object.keys(value.groups).length > 4) return null;
  const result: NativeEnergy['groups'] = {};
  for (const [group, report] of Object.entries(value.groups)) {
    if (!groups.includes(group as typeof groups[number]) || !object(report) || !object(report.raw)) return null;
    const raw: Record<string, number> = {};
    for (const [key, number] of Object.entries(report.raw)) {
      if (!rawKeys.includes(key)) continue;
      if (!integer(number)) return null;
      raw[key] = number;
    }
    const energy: Record<string, number> = {};
    for (const channel of channels) if (`${channel}_energy_raw` in raw) energy[channel] = raw[`${channel}_energy_raw`]! / 1000;
    result[group] = { raw, energy_kwh: energy };
  }
  if (!Object.values(result).some((group) => Object.keys(group.raw).length)) return null;
  return { schema_version: 1, source: 'device_energy_report', model: station.model, units_verified: false,
    reported_at: value.reported_at, counter_epoch_started_at: value.counter_epoch_started_at, counter_epoch: value.counter_epoch,
    received_reports: value.received_reports, batch_reports: value.batch_reports, continuity: value.continuity,
    firmware_version: value.firmware_version as string | null, groups: result,
    conversion_basis: station.model === 'c1000_gen2' && value.firmware_version === '1.1.4.9' ? 'nominal_wh' : 'assumed_wh',
    available: now / 1000 - value.reported_at >= -5 && now / 1000 - value.reported_at < 1800,
    max_report_age_seconds: 1800 };
}
