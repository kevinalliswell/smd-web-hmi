import type { components } from './generated/schema'

export type CommandRequest = components['schemas']['CommandRequest']
export type RecoverySummary = components['schemas']['RecoverySummary']
export type RecoveryDetail = components['schemas']['RecoveryDetail']
export type OperationStatus = 'pending' | 'sent' | 'accepted' | 'verified' | 'rejected' | 'unknown'
export type WireOperationStatus = 'pending' | 'sent' | 'accepted' | 'applied' | 'rejected' | 'interrupted' | 'unknown' | 'result_expired' | 'not_found'
export interface WireOperation {
  operation_id?: string
  status: WireOperationStatus
  command_seq?: string
  reason?: string
  result?: Record<string, unknown> | null
  reconciled?: 0 | 1
  locally_reconciled?: boolean
  reconciliation_reason?: string | null
}
export interface OperationResult {
  operation_id: string
  operation_status: OperationStatus
  request_msg_id?: string
  result: string
  command?: string
  wire_status?: WireOperationStatus
  wire_operation_id?: string
  command_seq?: string
  wire_operation?: WireOperation
  wire_reconciled?: boolean
  prerequisite_only?: boolean
  created_at?: string
  updated_at?: string
  reason_code?: string
  device_result?: Record<string, unknown>
  readback_ok?: boolean
  params?: Record<string, unknown>
}
export interface CommandFailure extends Error {
  operationId?: string
  outcomeUnknown?: boolean
}
export interface DeviceSnapshot {
  comm_quality?: 'online' | 'degraded' | 'offline'
  data_fresh?: boolean
  control_ready?: boolean
  last_update?: string | null
  system?: {
    current_state?: string
    operation_state?: 'idle' | 'running' | 'fault' | 'terminal' | 'unknown'
    protocol_version?: string
    can_activate_recipe?: boolean
    can_ack_run?: boolean
    can_ack_alarm?: boolean
    can_reset_fault?: boolean
    run_recovery_required?: boolean
    control_lease_acquire_required?: boolean
    can_start_test?: boolean
    can_stop_test?: boolean
    can_set_parameters?: boolean
    is_running?: boolean
  }
  _hostcomm?: { protocol_version?: string; capabilities?: string[] }
  alarm?: { ack_required?: boolean | null; latched_alarm_count?: number | null }
  _v2?: { recovery?: RecoverySummary | null; alarms_reconciled?: boolean; status?: Record<string, unknown>; telemetry?: Record<string, unknown>; profile?: Record<string, unknown>; channel_quality?: Record<string, unknown> }
  state_machine?: { current_state?: string; test_id?: string; run_id?: string; stage_index?: number | null; measurement_complete?: boolean; safe_complete?: boolean; outcome?: string }
  temperature?: Record<string, unknown>
  measurement?: Record<string, unknown>
  gas?: Record<string, unknown>
  safety?: Record<string, unknown>
}
export interface MaintenanceState {
  state: 'idle' | 'prepared' | 'claimed'
  target_version?: string
  current_version: string
  upgrade_id?: string
}

export interface RecipeStage {
  name: string
  kind: 'ramp' | 'hold' | 'gas' | 'cool'
  heater_mode?: 'off' | 'ramp' | 'hold' | null
  furnace_target_c?: number | null
  ramp_c_min?: number | null
  n2_l_min: number
  co_l_min: number
  exit: { signal: 'furnace_c' | 'burden_c' | 'elapsed_s'; comparison: 'gte' | 'lt'; value: number; stable_s?: number | null }
  timeout_s: number
}
export interface RecipeDefinition {
  schema_version: 1
  name: string
  mode: 'standard' | 'custom'
  description: string
  stages: RecipeStage[]
}
export interface RecipeVersion { recipe_id: string; version: number; digest: string; definition: RecipeDefinition; created_at?: string }

export interface BackgroundJob {
  task_id: string
  kind?: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress?: number
  result?: Record<string, unknown>
  message?: string
}
