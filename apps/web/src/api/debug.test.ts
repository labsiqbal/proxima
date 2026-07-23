import { describe, expect, it } from 'vitest'
import { debugLogLineLabel } from './debug'

describe('debugLogLineLabel', () => {
  it('singularizes one line', () => {
    expect(debugLogLineLabel(1)).toBe('1 line')
  })

  it('pluralizes zero and many lines', () => {
    expect(debugLogLineLabel(0)).toBe('0 lines')
    expect(debugLogLineLabel(240)).toBe('240 lines')
  })
})
