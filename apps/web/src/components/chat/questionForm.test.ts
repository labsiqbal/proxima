import { describe, it, expect } from 'vitest'
import { hasQuestionForm, splitOnQuestionForms, formatFormAnswers, stripQuestionForms } from './questionForm'

const FORM = `<question-form>{"id":"prefs","title":"Setup","questions":[` +
  `{"id":"color","label":"Favorite color?","type":"radio","options":["Red",{"label":"Blue","value":"b"}]},` +
  `{"id":"tags","label":"Tags","type":"checkbox","options":["a","b","c"]},` +
  `{"id":"note","label":"Notes","type":"long"}` +
  `]}</question-form>`

describe('questionForm parser', () => {
  it('detects a form vs plain prose', () => {
    expect(hasQuestionForm(FORM)).toBe(true)
    expect(hasQuestionForm('just a normal message')).toBe(false)
    expect(hasQuestionForm('<question-form>no closing tag')).toBe(false)
  })

  it('splits prose around the form and parses it', () => {
    const segs = splitOnQuestionForms(`Hi there\n${FORM}\nbye`)
    expect(segs.map(s => s.kind)).toEqual(['text', 'form', 'text'])
    const form = segs.find(s => s.kind === 'form')!
    if (form.kind !== 'form') throw new Error('expected form')
    expect(form.form.id).toBe('prefs')
    expect(form.form.questions).toHaveLength(3)
    expect(form.form.questions[0].type).toBe('radio')
    expect(form.form.questions[2].type).toBe('textarea') // 'long' normalized
    // option value: bare string -> value=label; object -> its value
    expect(form.form.questions[0].options).toEqual([
      { label: 'Red', value: 'Red' },
      { label: 'Blue', value: 'b' },
    ])
  })

  it('falls back to text when the body is not valid JSON', () => {
    const segs = splitOnQuestionForms('<question-form>not json</question-form>')
    expect(segs.map(s => s.kind)).toEqual(['text'])
  })

  it('formats answers into prose, mapping values to labels and marking skips', () => {
    const form = splitOnQuestionForms(FORM).find(s => s.kind === 'form')
    if (!form || form.kind !== 'form') throw new Error('expected form')
    const out = formatFormAnswers(form.form, { color: 'b', tags: ['a', 'c'] })
    expect(out).toContain('[form answers — prefs]')
    expect(out).toContain('Favorite color?: Blue') // value 'b' -> label 'Blue'
    expect(out).toContain('Tags: a, c')
    expect(out).toContain('Notes: (skipped)') // unanswered
  })

  it('strips form blocks, keeping only prose', () => {
    const stripped = stripQuestionForms(`Please answer:\n${FORM}\nThanks`)
    expect(stripped).toContain('Please answer:')
    expect(stripped).toContain('Thanks')
    expect(stripped).not.toContain('question-form')
    expect(stripped).not.toContain('prefs')
    expect(stripQuestionForms('no forms here')).toBe('no forms here')
  })
})
