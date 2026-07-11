import React from 'react'
import type { QuestionForm as QForm, FormQuestion } from './questionForm'
import { formatFormAnswers } from './questionForm'

const OTHER = '__other__'

// Renders an agent-emitted <question-form> as an interactive card. Choice
// questions (radio/checkbox/select) include an "Other…" free-text escape by
// default so the user can always answer manually — unless the agent set
// allowOther:false. On submit the answers go back as a prose message.
export function QuestionForm({ form, disabled, onSubmit }: { form: QForm; disabled?: boolean; onSubmit: (text: string) => void }) {
  const [answers, setAnswers] = React.useState<Record<string, string | string[]>>({})
  const [otherOn, setOtherOn] = React.useState<Record<string, boolean>>({})
  const [otherText, setOtherText] = React.useState<Record<string, string>>({})
  const [sent, setSent] = React.useState(false)

  const allowOther = (q: FormQuestion) => q.type !== 'text' && q.type !== 'textarea' && q.allowOther !== false
  const setOne = (id: string, v: string | string[]) => setAnswers(a => ({ ...a, [id]: v }))
  const setOther = (id: string, text: string) => setOtherText(o => ({ ...o, [id]: text }))
  const pickRadio = (id: string, value: string) => { setOne(id, value); setOtherOn(o => ({ ...o, [id]: false })) }
  const toggleOther = (id: string) => setOtherOn(o => ({ ...o, [id]: !o[id] }))
  const toggleMulti = (id: string, value: string, max?: number) => setAnswers(a => {
    const cur = Array.isArray(a[id]) ? (a[id] as string[]) : []
    if (cur.includes(value)) return { ...a, [id]: cur.filter(x => x !== value) }
    if (max && cur.length >= max) return a
    return { ...a, [id]: [...cur, value] }
  })

  // The value(s) actually submitted for a question, folding in any "Other" text.
  const effective = (q: FormQuestion): string | string[] => {
    const ot = (otherText[q.id] || '').trim()
    if (q.type === 'checkbox') {
      const arr = Array.isArray(answers[q.id]) ? [...(answers[q.id] as string[])] : []
      if (otherOn[q.id] && ot) arr.push(ot)
      return arr
    }
    if (q.type === 'select') return answers[q.id] === OTHER ? ot : ((answers[q.id] as string) || '')
    if ((q.type === 'radio') && otherOn[q.id]) return ot
    return (answers[q.id] as string) || ''
  }

  const ready = form.questions.every(q => {
    if (!q.required) return true
    const v = effective(q)
    return Array.isArray(v) ? v.length > 0 : !!v.trim()
  })

  const submit = () => {
    if (!ready || sent) return
    setSent(true)
    const eff: Record<string, string | string[]> = {}
    for (const q of form.questions) eff[q.id] = effective(q)
    onSubmit(formatFormAnswers(form, eff))
  }
  const locked = disabled || sent

  const otherChip = (q: FormQuestion) => allowOther(q) && q.type !== 'select' ? (
    <button type="button" disabled={locked} className={`qform-chip ${otherOn[q.id] ? 'active' : ''}`} onClick={() => toggleOther(q.id)}>Other…</button>
  ) : null
  const otherInput = (q: FormQuestion) => (otherOn[q.id] || (q.type === 'select' && answers[q.id] === OTHER)) ? (
    <input className="qform-input qform-other" disabled={locked} value={otherText[q.id] || ''} placeholder="Type your own answer…" onChange={e => setOther(q.id, e.target.value)} />
  ) : null

  return <div className={`qform enter ${locked ? 'locked' : ''}`}>
    <div className="qform-head"><strong>{form.title}</strong>{sent && <span className="qform-sent">✓ Sent</span>}</div>
    {form.description && <p className="qform-desc">{form.description}</p>}
    <div className="qform-body">
      {form.questions.map(q => <div className="qform-q" key={q.id}>
        <label className="qform-label">{q.label}{q.required && <span className="qform-req">*</span>}</label>
        {q.help && <p className="qform-help">{q.help}</p>}
        {(q.type === 'radio') && <><div className="qform-chips">{(q.options || []).map(o => <button key={o.value} type="button" disabled={locked} className={`qform-chip ${!otherOn[q.id] && answers[q.id] === o.value ? 'active' : ''}`} onClick={() => pickRadio(q.id, o.value)} title={o.description}>{o.label}</button>)}{otherChip(q)}</div>{otherInput(q)}</>}
        {(q.type === 'checkbox') && <><div className="qform-chips">{(q.options || []).map(o => <button key={o.value} type="button" disabled={locked} className={`qform-chip ${Array.isArray(answers[q.id]) && (answers[q.id] as string[]).includes(o.value) ? 'active' : ''}`} onClick={() => toggleMulti(q.id, o.value, q.maxSelections)} title={o.description}>{o.label}</button>)}{otherChip(q)}{q.maxSelections && <span className="qform-hint">pick up to {q.maxSelections}</span>}</div>{otherInput(q)}</>}
        {(q.type === 'select') && <><select className="qform-select" disabled={locked} value={(answers[q.id] as string) || ''} onChange={e => setOne(q.id, e.target.value)}><option value="">Select…</option>{(q.options || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}{allowOther(q) && <option value={OTHER}>Other…</option>}</select>{otherInput(q)}</>}
        {(q.type === 'text') && <input className="qform-input" disabled={locked} value={(answers[q.id] as string) || ''} placeholder={q.placeholder} onChange={e => setOne(q.id, e.target.value)} />}
        {(q.type === 'textarea') && <textarea className="qform-input" rows={3} disabled={locked} value={(answers[q.id] as string) || ''} placeholder={q.placeholder} onChange={e => setOne(q.id, e.target.value)} />}
      </div>)}
    </div>
    {!locked && <div className="qform-foot"><button className="primary-button" disabled={!ready} onClick={submit}>{form.submitLabel || 'Submit'}</button></div>}
  </div>
}
