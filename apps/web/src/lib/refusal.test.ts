import { describe, expect, it } from 'vitest'
import { refusalText, splitRefusal } from './refusal'

describe('splitRefusal', () => {
  it('separates the diagnosis from the next step so neither is rendered twice', () => {
    expect(splitRefusal(
      'Port 4600 belongs to another process. Stop whatever holds it.',
      'Stop whatever holds it.',
    )).toEqual({
      reason: 'Port 4600 belongs to another process.',
      nextStep: 'Stop whatever holds it.',
    })
  })

  it('keeps the whole message when it does not end with the next step', () => {
    expect(splitRefusal('Something else went wrong.', 'Stop whatever holds it.')).toEqual({
      reason: 'Something else went wrong.',
      nextStep: 'Stop whatever holds it.',
    })
  })

  it('survives a refusal with no next step at all', () => {
    expect(splitRefusal('Only a reason.', undefined)).toEqual({
      reason: 'Only a reason.',
      nextStep: '',
    })
  })

  it('survives a next step with no message', () => {
    expect(splitRefusal(undefined, 'Do the thing.')).toEqual({
      reason: '',
      nextStep: 'Do the thing.',
    })
  })
})

describe('refusalText', () => {
  it('keeps the server sentence and drops the transport noise around it', () => {
    expect(refusalText(new Error(
      'Failed to write file (400 Bad Request): That path crosses a symlink, '
      + 'which Proxima never follows. Open the real folder instead.',
    ))).toBe(
      'That path crosses a symlink, which Proxima never follows. '
      + 'Open the real folder instead.',
    )
  })

  it('strips the client.ts method/status prefix', () => {
    expect(refusalText(new Error(
      'POST /api/projects/demo/files failed (400): That path leaves the project folder.',
    ))).toBe('That path leaves the project folder.')
  })

  it('falls back to the raw message when there is no server sentence', () => {
    expect(refusalText(new Error('Network down'))).toBe('Network down')
  })

  it('handles a non-Error rejection', () => {
    expect(refusalText('plain string')).toBe('plain string')
    expect(refusalText(null)).toBe('Something went wrong.')
  })
})
