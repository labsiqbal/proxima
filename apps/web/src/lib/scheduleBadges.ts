/**
 * Workflow library “how it runs” badges (UI Flow Q6).
 * Always manual-capable; Scheduled when at least one schedule exists.
 * Optional short cron text when a single known cadence is available.
 */

export type ScheduleBadge = {
  kind: 'manual' | 'scheduled'
  /** Visible label, e.g. "Manual", "Scheduled", "Scheduled · every hour". */
  label: string
}

export type HowItRunsInput = {
  /** True when the workflow has one or more schedule rows. */
  scheduled: boolean
  /**
   * Optional short cron labels (from presets or raw cron).
   * When exactly one unique label is present, it is appended to Scheduled.
   */
  cronLabels?: string[]
}

/** Derive Manual / Scheduled badges from real schedule data. */
export function howItRunsBadges(input: HowItRunsInput): ScheduleBadge[] {
  const badges: ScheduleBadge[] = [{ kind: 'manual', label: 'Manual' }]
  if (!input.scheduled) return badges

  const labels = (input.cronLabels || [])
    .map(s => s.trim())
    .filter(Boolean)
  const unique = [...new Set(labels)]
  const suffix = unique.length === 1 ? ` · ${unique[0]}` : ''
  badges.push({ kind: 'scheduled', label: `Scheduled${suffix}` })
  return badges
}

/** Map of workflow_id → short cron labels for card derivation. */
export function cronLabelsByWorkflow(
  rows: Array<{ workflow_id: number; cron: string }>,
  cronHint: (cron: string) => string,
): Map<number, string[]> {
  const map = new Map<number, string[]>()
  for (const row of rows) {
    const label = cronHint(row.cron)
    const list = map.get(row.workflow_id) || []
    list.push(label)
    map.set(row.workflow_id, list)
  }
  return map
}
