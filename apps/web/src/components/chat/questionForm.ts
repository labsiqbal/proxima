// Parser for inline <question-form>...</question-form> blocks the agent emits to
// ask the user structured clarifying questions. Portable: it's plain text the
// agent writes (works over ACP), the UI renders it as an interactive form, and
// the answers go back as an ordinary user message. (Ported from Open Design.)

export type QuestionType = 'radio' | 'checkbox' | 'select' | 'text' | 'textarea'

export interface FormOption { label: string; value: string; description?: string }
export interface FormQuestion {
  id: string
  label: string
  type: QuestionType
  options?: FormOption[]
  placeholder?: string
  required?: boolean
  help?: string
  maxSelections?: number
  /** Choice questions (radio/checkbox/select) show an "Other…" free-text escape
   * by default so the user is never trapped; the agent sets false for strict enums. */
  allowOther?: boolean
}
export interface QuestionForm {
  id: string
  title: string
  description?: string
  questions: FormQuestion[]
  submitLabel?: string
  /** When set, the submitted answers are sent PREFIXED with this text — used by the
   * media-brief forms so answering re-issues the original slash command (e.g.
   * "/design") with the answers as an enriched brief, re-triggering generation. */
  submitAs?: string
}
export type FormSegment = { kind: 'text'; text: string } | { kind: 'form'; form: QuestionForm; raw: string }

const OPEN_RE = /<question-form\b([^>]*)>/i
const CLOSE_TAG = '</question-form>'

export function splitOnQuestionForms(input: string): FormSegment[] {
  const out: FormSegment[] = []
  let cursor = 0
  while (cursor < input.length) {
    const slice = input.slice(cursor)
    const m = OPEN_RE.exec(slice)
    if (!m) { out.push({ kind: 'text', text: slice }); break }
    const openStart = cursor + m.index
    const openEnd = openStart + m[0].length
    const closeIdx = input.indexOf(CLOSE_TAG, openEnd)
    if (closeIdx === -1) { out.push({ kind: 'text', text: slice }); break }
    if (openStart > cursor) out.push({ kind: 'text', text: input.slice(cursor, openStart) })
    const body = input.slice(openEnd, closeIdx)
    const form = tryParseForm(body, parseAttrs(m[1] ?? ''))
    if (form) out.push({ kind: 'form', form, raw: input.slice(openStart, closeIdx + CLOSE_TAG.length) })
    else out.push({ kind: 'text', text: input.slice(openStart, closeIdx + CLOSE_TAG.length) })
    cursor = closeIdx + CLOSE_TAG.length
  }
  return out
}

export function hasQuestionForm(input: string): boolean {
  return OPEN_RE.test(input) && input.includes(CLOSE_TAG)
}

function parseAttrs(raw: string): Record<string, string> {
  const re = /([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g
  const out: Record<string, string> = {}
  let m: RegExpExecArray | null
  while ((m = re.exec(raw)) !== null) out[m[1] as string] = (m[2] ?? m[3] ?? '') as string
  return out
}

function tryParseForm(body: string, attrs: Record<string, string>): QuestionForm | null {
  const stripped = body.trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim()
  if (!stripped) return null
  let data: unknown
  try { data = JSON.parse(stripped) } catch { return null }
  if (!data || typeof data !== 'object') return null
  const obj = data as Record<string, unknown>
  const rawQuestions = Array.isArray(obj.questions) ? obj.questions : null
  if (!rawQuestions) return null
  const questions: FormQuestion[] = []
  rawQuestions.forEach((q, i) => {
    if (!q || typeof q !== 'object') return
    const qo = q as Record<string, unknown>
    const id = typeof qo.id === 'string' && qo.id.trim() ? qo.id.trim() : `q${i + 1}`
    const label = typeof qo.label === 'string' ? qo.label : id
    const type = normalizeType(qo.type)
    const options = parseOptions(qo.options)
    const placeholder = typeof qo.placeholder === 'string' ? qo.placeholder : undefined
    const help = typeof qo.help === 'string' ? qo.help : undefined
    const required = qo.required === true
    const maxSelections = typeof qo.maxSelections === 'number' && Number.isInteger(qo.maxSelections) && qo.maxSelections > 0 ? qo.maxSelections : undefined
    const allowOther = qo.allowOther === false ? false : undefined  // default (undefined) ⇒ allowed
    questions.push({ id, label, type, ...(options ? { options } : {}), ...(placeholder ? { placeholder } : {}), ...(help ? { help } : {}), ...(required ? { required } : {}), ...(maxSelections !== undefined && type === 'checkbox' ? { maxSelections } : {}), ...(allowOther === false ? { allowOther } : {}) })
  })
  if (!questions.length) return null
  const id = attrs.id ?? (typeof obj.id === 'string' ? obj.id : 'questions')
  const title = attrs.title ?? (typeof obj.title === 'string' ? obj.title : 'A few quick questions')
  const description = typeof obj.description === 'string' ? obj.description : undefined
  const submitLabel = typeof obj.submitLabel === 'string' ? obj.submitLabel : undefined
  const submitAs = attrs['submit-as'] ?? attrs.submitas ?? (typeof obj.submitAs === 'string' ? obj.submitAs : undefined)
  return { id, title, questions, ...(description ? { description } : {}), ...(submitLabel ? { submitLabel } : {}), ...(submitAs ? { submitAs } : {}) }
}

function normalizeType(raw: unknown): QuestionType {
  if (typeof raw !== 'string') return 'text'
  const l = raw.toLowerCase().trim()
  if (l === 'radio' || l === 'single' || l === 'choice') return 'radio'
  if (l === 'checkbox' || l === 'multi' || l === 'multiple') return 'checkbox'
  if (l === 'select' || l === 'dropdown') return 'select'
  if (l === 'textarea' || l === 'long' || l === 'paragraph') return 'textarea'
  return 'text'
}

function parseOptions(raw: unknown): FormOption[] | undefined {
  if (!Array.isArray(raw)) return undefined
  const options = raw.map(parseOption).filter((o): o is FormOption => o !== null)
  return options.length ? options : undefined
}
function parseOption(raw: unknown): FormOption | null {
  if (typeof raw === 'string') { const label = raw.trim(); return label ? { label, value: label } : null }
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const label = typeof obj.label === 'string' ? obj.label.trim() : ''
  if (!label) return null
  const value = typeof obj.value === 'string' && obj.value.trim() ? obj.value.trim() : label
  const description = typeof obj.description === 'string' && obj.description.trim() ? obj.description.trim() : undefined
  return { label, value, ...(description ? { description } : {}) }
}

const labelFor = (q: Pick<FormQuestion, 'options'>, value: string): string =>
  q.options?.find(o => o.value === value || o.label === value)?.label ?? value

// Format answers into a prose user message the agent reads on its next turn.
export function formatFormAnswers(form: QuestionForm, answers: Record<string, string | string[]>): string {
  const lines = [`[form answers — ${form.id}]`]
  for (const q of form.questions) {
    const v = answers[q.id]
    let display: string
    if (Array.isArray(v)) display = v.length ? v.map(x => labelFor(q, x)).join(', ') : '(skipped)'
    else if (typeof v === 'string' && v.trim()) display = labelFor(q, v.trim())
    else display = '(skipped)'
    lines.push(`- ${q.label}: ${display}`)
  }
  return lines.join('\n')
}

// Drop <question-form> blocks, keep only the prose. Used where a form can't be
// answered (e.g. an autonomous job's step output shown read-only in Activity).
export function stripQuestionForms(input: string): string {
  return splitOnQuestionForms(input || '')
    .filter((s): s is { kind: 'text'; text: string } => s.kind === 'text')
    .map(s => s.text).join('\n').trim()
}
