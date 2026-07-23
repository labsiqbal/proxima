// Lightweight desktop notifications via the browser Notification API.
// Fires when an agent run finishes while the tab is backgrounded, so you don't
// have to babysit a chat/task. (Closed-tab delivery would need Web Push/VAPID —
// a documented follow-up.)
const KEY = 'proxima.notify'

export type NotifyPermission = NotificationPermission | 'unsupported'

export const notifySupported = () => typeof window !== 'undefined' && 'Notification' in window

/** Current browser permission, or `unsupported` when the API is missing. */
export function notifyPermission(): NotifyPermission {
  if (!notifySupported()) return 'unsupported'
  return Notification.permission
}

/** True when the browser has permanently blocked the site from notifying. */
export const notifyBlocked = () => notifyPermission() === 'denied'

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

/** Owner-facing copy when enable fails (denied or unsupported). */
export function notifyEnableFailureHint(permission: NotifyPermission = notifyPermission()): string {
  if (permission === 'unsupported') {
    return 'Desktop notifications are not supported in this browser.'
  }
  if (permission === 'denied') {
    return 'Desktop notifications are blocked for this site. Allow notifications in the browser site settings, then try again.'
  }
  return 'Could not enable desktop notifications.'
}

export function notify(title: string, body?: string): void {
  try {
    if (!notifyEnabled()) return
    // Don't interrupt when the user is already looking at the tab.
    if (document.visibilityState === 'visible' && document.hasFocus()) return
    new Notification(title, { body })
  } catch { /* ignore */ }
}
