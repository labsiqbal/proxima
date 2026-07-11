// Client-side default for the autonomous goal loop's iteration cap. Stored in
// localStorage so the user can tune it in Settings; read when starting a /goal.
const KEY = 'proxima.goalMaxIter'
const DEFAULT = 20

export function getGoalMaxIter(): number {
  const v = Number(localStorage.getItem(KEY))
  return Number.isFinite(v) && v >= 1 && v <= 100 ? Math.round(v) : DEFAULT
}

export function setGoalMaxIter(n: number): void {
  const clamped = Math.max(1, Math.min(100, Math.round(n) || DEFAULT))
  localStorage.setItem(KEY, String(clamped))
}
