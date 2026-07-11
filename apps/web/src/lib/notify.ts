// Lightweight desktop notifications via the browser Notification API.
// Fires when an agent run finishes while the tab is backgrounded, so you don't
// have to babysit a chat/task. (Closed-tab delivery would need Web Push/VAPID —
// a documented follow-up.)
const KEY = 'proxima.notify'

export const notifySupported = () => typeof window !== 'undefined' && 'Notification' in window
export const notifyEnabled = () => notifySupported() && localStorage.getItem(KEY) === '1' && Notification.permission === 'granted'
export const setNotifyPref = (on: boolean) => localStorage.setItem(KEY, on ? '1' : '0')

export async function enableNotifications(): Promise<boolean> {
  if (!notifySupported()) return false
  let perm = Notification.permission
  if (perm === 'default') perm = await Notification.requestPermission()
  const ok = perm === 'granted'
  setNotifyPref(ok)
  return ok
}

export function notify(title: string, body?: string): void {
  try {
    if (!notifyEnabled()) return
    // Don't interrupt when the user is already looking at the tab.
    if (document.visibilityState === 'visible' && document.hasFocus()) return
    new Notification(title, { body })
  } catch { /* ignore */ }
}
