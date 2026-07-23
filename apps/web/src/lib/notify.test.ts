import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  enableNotifications,
  notifyBlocked,
  notifyEnableFailureHint,
  notifyEnabled,
  notifyPermission,
  notifySupported,
  setNotifyPref,
} from './notify'

describe('notify helpers', () => {
  const originalNotification = globalThis.Notification
  let permission: NotificationPermission = 'default'
  const requestPermission = vi.fn(async () => permission)

  beforeEach(() => {
    permission = 'default'
    requestPermission.mockReset()
    requestPermission.mockImplementation(async () => permission)
    localStorage.clear()
    // Minimal Notification stub for jsdom.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).Notification = class {
      static get permission() { return permission }
      static requestPermission = requestPermission
      constructor(_title: string, _opts?: NotificationOptions) { /* no-op */ }
    }
  })

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).Notification = originalNotification
    localStorage.clear()
  })

  it('reports support and permission states', () => {
    expect(notifySupported()).toBe(true)
    expect(notifyPermission()).toBe('default')
    expect(notifyBlocked()).toBe(false)
    permission = 'denied'
    expect(notifyPermission()).toBe('denied')
    expect(notifyBlocked()).toBe(true)
    expect(notifyEnabled()).toBe(false)
  })

  it('enableNotifications grants and remembers preference', async () => {
    permission = 'granted'
    await expect(enableNotifications()).resolves.toBe(true)
    expect(localStorage.getItem('proxima.notify')).toBe('1')
    expect(notifyEnabled()).toBe(true)
  })

  it('enableNotifications leaves pref off when the browser denies', async () => {
    permission = 'denied'
    await expect(enableNotifications()).resolves.toBe(false)
    expect(localStorage.getItem('proxima.notify')).toBe('0')
    expect(notifyEnabled()).toBe(false)
  })

  it('requests permission only while still default', async () => {
    permission = 'default'
    requestPermission.mockImplementation(async () => {
      permission = 'granted'
      return permission
    })
    await expect(enableNotifications()).resolves.toBe(true)
    expect(requestPermission).toHaveBeenCalledOnce()
  })

  it('setNotifyPref toggles the local flag without changing browser permission', () => {
    permission = 'granted'
    setNotifyPref(true)
    expect(notifyEnabled()).toBe(true)
    setNotifyPref(false)
    expect(notifyEnabled()).toBe(false)
  })

  it('notifyEnableFailureHint explains blocked and unsupported cases', () => {
    expect(notifyEnableFailureHint('denied')).toMatch(/blocked/i)
    expect(notifyEnableFailureHint('unsupported')).toMatch(/not supported/i)
    expect(notifyEnableFailureHint('default')).toMatch(/Could not enable/i)
  })
})
