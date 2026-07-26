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
