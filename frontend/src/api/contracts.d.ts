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
