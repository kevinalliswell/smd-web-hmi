import type { components } from './generated/schema'

export type CommandRequest = components['schemas']['CommandRequest']
export type OperationStatus = 'pending' | 'sent' | 'accepted' | 'verified' | 'rejected' | 'unknown'
export interface OperationResult {
  operation_id: string
  operation_status: OperationStatus
  request_msg_id?: string
  result: string
  command?: string
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
    operation_state?: 'idle' | 'running' | 'fault' | 'unknown'
    can_start_test?: boolean
    can_stop_test?: boolean
    can_set_parameters?: boolean
    is_running?: boolean
  }
  state_machine?: { current_state?: string; test_id?: string }
  temperature?: Record<string, unknown>
  measurement?: Record<string, unknown>
  gas?: Record<string, unknown>
  safety?: Record<string, unknown>
}
export interface MaintenanceState {
  state: 'idle' | 'prepared' | 'claimed'
  target_version?: string
  current_version?: string
  upgrade_id?: string
}

export interface RecipeStage {
  name: string
  kind: 'ramp' | 'hold' | 'gas' | 'cool'
  furnace_target_c?: number | null
  ramp_c_min?: number | null
  n2_l_min: number
  co_l_min: number
  exit: { signal: 'furnace_c' | 'burden_c' | 'elapsed_s'; comparison: 'gte' | 'lt'; value: number }
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
