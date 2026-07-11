import { api } from './client'

export type AuditEntry = { id: number; action: string; target_type: string; target_id: string; metadata: string; created_at: string; actor: string | null }

export const listAudit = (token: string) => api<{ entries: AuditEntry[] }>('/api/audit', token)
