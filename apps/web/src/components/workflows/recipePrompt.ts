import type { WorkflowInput } from '../../types'

// A recipe the authoring chat can hand back. Every step field the editor owns is here,
// so "make step 2 stricter" can touch rules or a review gate — not just the instruction.
export type RecipeStep = {
  name: string
  instruction: string
  expected_output?: string
  type?: string
  rules?: string | null
  skill_ids?: string[] | null
  review_required?: boolean
}
export type RecipePatch = {
  name?: string
  description?: string
  category?: string
  inputs?: WorkflowInput[]
  steps?: RecipeStep[]
}
export type RecipeSnapshot = {
  name: string
  description: string
  category: string
  inputs: WorkflowInput[]
  steps: RecipeStep[]
}

// Same client-side move Design Studio uses: the fat prompt (mode + schema + current
// recipe + request) goes as the run's `message`, while the thread shows only the short
// `display_message`. No server mode is involved — the agent is steered entirely here.
export function buildRecipePrompt(recipe: RecipeSnapshot, instruction: string): string {
  return [
    '⟦MODE: WORKFLOW AUTHORING⟧ You are editing a Proxima workflow *recipe*, not running it. A recipe is an ordered list of steps; each step is one instruction handed to an AI agent, and steps run top to bottom with the earlier steps\' output available to the later ones.',
    'A recipe is JSON: {name, description, category, inputs[], steps[]}. inputs are typed placeholders the steps reference with {{id}}: {id, label, kind("text"|"number"|"url"|"file"), required(bool)}. Each step is {name, instruction, expected_output?, rules?(hard constraints), skill_ids?(string[] of skill hints), review_required?(bool — pause for human approval after this step)}.',
    'Write instructions an agent can act on: concrete, single-purpose, and ordered so each step builds on the last. Prefer a few strong steps over many thin ones. Put a review gate (review_required:true) only where a human genuinely must approve before spending the next step.',
    'Keep whatever the user did not ask you to change. If they rename step 2, do not rewrite step 1. Reference inputs with {{id}} rather than hardcoding values the user declared as inputs.',
    '',
    'Current recipe:',
    '```json',
    JSON.stringify(recipe),
    '```',
    '',
    `User request: ${instruction}`,
    '',
    'Reply with a one-sentence summary of what you changed, then the COMPLETE updated recipe (all steps, not a diff) as:',
    '<workflow-recipe>',
    '{ "name": "...", "description": "...", "category": "...", "inputs": [...], "steps": [...] }',
    '</workflow-recipe>',
  ].join('\n')
}

// A self-contained "run the recipe through step N" prompt. It inlines the steps from the
// live form (not the saved copy) so a test reflects unsaved edits and does not depend on
// what the session happens to remember. Steps beyond N are omitted — we only run this far.
export function buildRunThroughPrompt(recipe: RecipeSnapshot, throughIndex: number): string {
  const steps = recipe.steps.slice(0, throughIndex + 1)
  const lines = [
    '⟦MODE: WORKFLOW TEST RUN⟧ Execute this workflow recipe from the beginning through the final step below, in order, using each step\'s output as context for the next. This is a dry run so I can see how the recipe behaves — do the actual work, then show me the result of the LAST step (and briefly note what each earlier step produced).',
    '',
    ...(recipe.inputs.length ? [
      'Declared inputs (use sensible sample values where a step references {{id}}):',
      ...recipe.inputs.map(x => `- {{${x.id}}} — ${x.label}${x.required ? ' (required)' : ''}`),
      '',
    ] : []),
    'Steps:',
    ...steps.map((s, i) => {
      const parts = [`${i + 1}. ${s.name || 'Step ' + (i + 1)}: ${s.instruction}`]
      if (s.expected_output) parts.push(`   Expected: ${s.expected_output}`)
      if (s.rules) parts.push(`   Rules: ${s.rules}`)
      return parts.join('\n')
    }),
  ]
  return lines.join('\n')
}

// The agent's reply carries the whole recipe JSON, which is noise in the chat once it
// has landed in the form. Strip the block for display and keep the summary sentence —
// the same courtesy Design Studio does with its scene block.
export function stripRecipeBlock(text: string): string {
  if (!text) return text
  let out = text.replace(/<workflow-recipe[^>]*>[\s\S]*?<\/workflow-recipe>/gi, '')
  // Fallback: a fenced ```json block that is clearly the recipe (has "steps").
  out = out.replace(/```(?:json)?\s*([\s\S]*?)```/gi, (m, body) => /"steps"\s*:/.test(body) ? '' : m)
  return out.trim() || 'Updated the recipe.'
}

// Pull the recipe back out of the agent's reply. Tolerant of a fenced ```json block as
// a fallback, mirroring parseDesignScene. Returns null when there is nothing to apply,
// so an ordinary conversational turn (e.g. a test run) never disturbs the form.
export function parseRecipeDraft(text: string): RecipePatch | null {
  if (!text) return null
  let body = ''
  const tag = text.match(/<workflow-recipe[^>]*>([\s\S]*?)<\/workflow-recipe>/i)
  if (tag) body = tag[1]
  else {
    const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
    if (fence && /"steps"\s*:/.test(fence[1])) body = fence[1]
  }
  if (!body.trim()) return null
  let d: any
  try { d = JSON.parse(body.trim()) } catch { return null }
  if (!d || typeof d !== 'object') return null

  const patch: RecipePatch = {}
  if (typeof d.name === 'string') patch.name = d.name
  if (typeof d.description === 'string') patch.description = d.description
  if (typeof d.category === 'string') patch.category = d.category
  if (Array.isArray(d.inputs)) {
    patch.inputs = d.inputs
      .filter((x: any) => x && typeof x === 'object' && (x.id || x.label))
      .map((x: any) => ({
        id: String(x.id || x.label || '').trim(),
        label: String(x.label || x.id || '').trim(),
        kind: ['text', 'number', 'url', 'file'].includes(x.kind) ? x.kind : 'text',
        required: !!x.required,
      }))
  }
  if (Array.isArray(d.steps)) {
    // Drop steps with no instruction — a step an agent can't act on isn't a step. If
    // that empties the list, treat the whole reply as non-structural and leave the form.
    const steps = d.steps
      .filter((s: any) => s && typeof s === 'object' && String(s.instruction || '').trim())
      .map((s: any) => ({
        name: String(s.name || '').trim(),
        instruction: String(s.instruction || '').trim(),
        expected_output: typeof s.expected_output === 'string' ? s.expected_output : undefined,
        type: typeof s.type === 'string' ? s.type : undefined,
        rules: typeof s.rules === 'string' ? s.rules : null,
        skill_ids: Array.isArray(s.skill_ids) ? s.skill_ids.map((x: any) => String(x)).filter(Boolean) : null,
        review_required: !!s.review_required,
      }))
    if (steps.length) patch.steps = steps
  }
  return (patch.name || patch.description || patch.category || patch.inputs || patch.steps) ? patch : null
}
