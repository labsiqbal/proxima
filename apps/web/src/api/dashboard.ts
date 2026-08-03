import { api } from './client'

export type Dashboard = {
  counts: { projects: number; chats: number; activeRuns: number }
  jobsByStatus?: { queued: number; running: number; review: number; done: number }
  recent: { id: number; title: string; workflow_id: number | null; updated_at: string; project_slug: string | null; goal_status?: string | null; last_run_status?: string | null; mode?: string | null }[]
  activeSessions: { id: number; title: string; workflow_id: number | null; updated_at: string; project_slug: string | null; last_active_at: string; mode?: string | null }[]
  projects: { slug: string; name: string; visibility: string; chats: number; last_activity: string | null }[]
  workflows: { id: number; name: string; category: string; steps: number }[]
  schedules: { id: number; workflow_name: string; cron: string; cadence: string; enabled: boolean; next_run: string | null }[]
  reviewCount: number
  reviewJobs?: { id: number; title: string; updated_at: string; workflow_id: number | null; engine?: string; project_slug: string | null; workflow_name: string | null }[]
  recentArtifacts?: { type: string; title: string; path: string; project_slug: string; updated_at: string }[]
  systemHealth?: { activeRuns: number; failedRuns24h: number; staleRuns: number; runnersReady: number; runnersTotal: number }
  pendingApprovals?: { id: number; title: string; project_slug: string | null; mode?: string | null }[]
  authHealth?: { status: 'checking' | 'ok' | 'error'; checks: { id: string; area: 'image' | 'video' | 'runner'; label: string; ok: boolean; detail: string }[]; checkedAt?: string }
}

export const getDashboard = (token: string) => api<Dashboard>('/api/dashboard', token)
