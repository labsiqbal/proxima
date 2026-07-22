import type { GraphNodeDefinition, WorkflowGraph, WorkflowInput } from '../../types'

// What the authoring chat may hand back. Every node field the inspector owns is here,
// so "make the research step stricter" can touch rules or a review gate — not just the
// instruction. The graph replaces the recipe's ordered `steps`: dependencies are edges,
// which is what lets the agent propose branches instead of a straight line.
export type GraphPatch = {
  name?: string
  description?: string
  category?: string
  inputs?: WorkflowInput[]
  graph?: WorkflowGraph
}
export type GraphSnapshot = {
  name: string
  description: string
  category: string
  inputs: WorkflowInput[]
  graph: WorkflowGraph
}

const OUTPUT_KINDS = ['text', 'json', 'artifact-ref']
const INPUT_KINDS = ['text', 'number', 'url', 'file']

// The same client-side move the recipe chat uses: the fat prompt (mode + schema +
// current graph + request) goes as the run's `message`, while the thread shows only the
// short `display_message`. No server mode is involved — the agent is steered entirely
// from here.
export function buildGraphPrompt(snapshot: GraphSnapshot, instruction: string, codeAreas: string[] = []): string {
  return [
    '⟦MODE: WORKFLOW GRAPH AUTHORING⟧ You are editing a Proxima workflow *graph*, not running it. A graph is nodes plus edges: each node is one instruction handed to an AI agent, and an edge means the target node depends on the source node\'s output. Nodes with no edge between them are independent and run AT THE SAME TIME, so use branches wherever work is genuinely parallel — that is the point of a graph over a list.',
    'A graph is JSON: {name, description, category, inputs[], graph:{nodes[], edges[]}}. inputs are typed placeholders any node can reference with {{id}}: {id, label, kind("text"|"number"|"url"|"file"), required(bool)}.',
    'A node is {id(short slug, unique), type("agent"|"trigger"|"script"), name, instruction, expected_output?(what a good result is), rules?(hard constraints), skill_ids?(string[] — skill/tool hints for the runner), review_required?(bool — pause for human approval after this node), output_kind?("text"|"json"|"artifact-ref"), output_schema?(JSON Schema, only when output_kind is "json"), target?(where the job works), target_ambiguous?(bool), target_question?(string), command?(script nodes: path in scripts/), args?(script nodes: string[])}.',
    'A node may be type:"script" — a deterministic step that runs a saved script from the project\'s scripts/ folder with no AI (free, repeatable). Use it only for steps needing no judgment, and only name a script the user says exists (never invent one). Its command is the script path, args its CLI arguments; it receives upstream outputs as JSON on stdin and its stdout is the step\'s output. Script nodes take no expected_output/rules/skills/target/agent.',
    codeAreas.length
      ? `A node's target is the ONE work area it binds to: a code area of this project (${codeAreas.map(area => `"${area}"`).join(', ')}; "." means the project root repo) when the job edits that repo, or "ops" for everything else. If it is genuinely unclear, do NOT guess: leave target null, set target_ambiguous true and put the owner's question in target_question. Keep every node's existing target unless the user asks to change it.`
      : 'This project has no registered code areas, so a node\'s target is "ops" (or omitted). Keep every node\'s existing target unless the user asks to change it.',
    'An edge is {from, to} — from the node that produces to the node that consumes. The graph must be acyclic. Prefer a few strong nodes over many thin ones, but size each node to finish comfortably within one agent turn (about 15 minutes of focused work).',
    'A node may be type:"trigger" — the workflow\'s entry point, at most one, with no incoming edges, and no instruction. Include one only if the user asks for it.',
    'Downstream nodes receive upstream output as explicit typed data, not shared chat history, so each instruction must be self-contained: say what to do with the upstream result rather than assuming the agent remembers it.',
    'Keep whatever the user did not ask you to change: keep existing node ids stable so their positions and agents survive, and do not rewrite a node they did not mention. Reference inputs with {{id}} rather than hardcoding values declared as inputs.',
    '',
    'Current graph:',
    '```json',
    JSON.stringify(snapshot),
    '```',
    '',
    `User request: ${instruction}`,
    '',
    'Reply with a one-sentence summary of what you changed, then the COMPLETE updated graph (every node and edge, not a diff) as:',
    '<workflow-graph>',
    '{ "name": "...", "description": "...", "category": "...", "inputs": [...], "graph": { "nodes": [...], "edges": [...] } }',
    '</workflow-graph>',
  ].join('\n')
}

// The agent's reply carries the whole graph JSON, which is noise in the chat once it has
// landed on the canvas. Strip the block for display and keep the summary sentence — the
// same courtesy the recipe chat and Design Studio do with their blocks.
export function stripGraphBlock(text: string): string {
  if (!text) return text
  let out = text.replace(/<workflow-graph[^>]*>[\s\S]*?<\/workflow-graph>/gi, '')
  // Fallback: a fenced ```json block that is clearly the graph (has "nodes").
  out = out.replace(/```(?:json)?\s*([\s\S]*?)```/gi, (match, body) => /"nodes"\s*:/.test(body) ? '' : match)
  return out.trim() || 'Updated the graph.'
}

function parseNodes(raw: unknown): GraphNodeDefinition[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const nodes: GraphNodeDefinition[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const node = item as Record<string, unknown>
    const id = String(node.id ?? '').trim()
    // A node with no id cannot be an edge's endpoint, and a duplicate id would make
    // "which one?" unanswerable — the server rejects both, so drop them here.
    if (!id || seen.has(id)) continue
    const type = node.type === 'trigger' ? 'trigger' : node.type === 'script' ? 'script' : 'agent'
    const instruction = String(node.instruction ?? '').trim()
    // An agent node with no instruction is not a step the runner can act on.
    if (type === 'agent' && !instruction) continue
    // A script node with no command has nothing to run — same reasoning.
    if (type === 'script' && !String(node.command ?? '').trim()) continue
    seen.add(id)
    const parsed: GraphNodeDefinition = {
      id,
      type,
      name: String(node.name ?? id).trim() || id,
      instruction,
      output_kind: OUTPUT_KINDS.includes(node.output_kind as string)
        ? (node.output_kind as GraphNodeDefinition['output_kind'])
        : 'text',
    }
    if (type === 'trigger') {
      parsed.trigger_kind = 'manual'
      parsed.output_kind = 'json'
      parsed.instruction = ''
    } else if (type === 'script') {
      // Deterministic step: only what the server keeps — command, args, the
      // output contract, and an optional review gate. Agent-only fields are
      // dropped here for the same reason the server drops them.
      parsed.command = String(node.command ?? '').trim()
      if (Array.isArray(node.args)) {
        const args = node.args.map(item => String(item))
        if (args.length) parsed.args = args
      }
      if (node.review_required) parsed.review_required = true
      if (parsed.output_kind === 'json' && node.output_schema && typeof node.output_schema === 'object') {
        parsed.output_schema = node.output_schema as Record<string, unknown>
      }
    } else {
      if (typeof node.expected_output === 'string' && node.expected_output.trim()) parsed.expected_output = node.expected_output.trim()
      if (typeof node.rules === 'string' && node.rules.trim()) parsed.rules = node.rules.trim()
      if (Array.isArray(node.skill_ids)) {
        const skills = [...new Set(node.skill_ids.map(item => String(item).trim()).filter(Boolean))]
        if (skills.length) parsed.skill_ids = skills
      }
      if (node.review_required) parsed.review_required = true
      if (parsed.output_kind === 'json' && node.output_schema && typeof node.output_schema === 'object') {
        parsed.output_schema = node.output_schema as Record<string, unknown>
      }
      // The T1/T2 work binding. touches_repo mirrors the server's derivation
      // for immediate canvas display; the server recomputes and never trusts it.
      const target = typeof node.target === 'string' ? node.target.trim() : ''
      const question = typeof node.target_question === 'string' ? node.target_question.trim() : ''
      if (target) {
        parsed.target = target
        parsed.touches_repo = target !== 'ops'
      } else if (node.target_ambiguous || question) {
        parsed.target_ambiguous = true
        if (question) parsed.target_question = question
      }
    }
    nodes.push(parsed)
  }
  return nodes
}

function parseEdges(raw: unknown, nodes: GraphNodeDefinition[]): WorkflowGraph['edges'] {
  if (!Array.isArray(raw)) return []
  const ids = new Set(nodes.map(node => node.id))
  const seen = new Set<string>()
  const edges: WorkflowGraph['edges'] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const edge = item as Record<string, unknown>
    const from = String(edge.from ?? edge.source ?? '').trim()
    const to = String(edge.to ?? edge.target ?? '').trim()
    // Drop what the server would reject anyway: dangling ends, self-edges, duplicates.
    // Better to land a usable graph than to have the whole reply refused.
    if (!ids.has(from) || !ids.has(to) || from === to) continue
    const key = `${from} ${to}`
    if (seen.has(key)) continue
    seen.add(key)
    edges.push({ from, to })
  }
  return edges
}

// Pull the graph back out of the agent's reply. Tolerant of a fenced ```json block as a
// fallback, mirroring parseRecipeDraft. Returns null when there is nothing to apply, so
// an ordinary conversational turn never disturbs the canvas.
export function parseGraphDraft(text: string): GraphPatch | null {
  if (!text) return null
  let body = ''
  const tag = text.match(/<workflow-graph[^>]*>([\s\S]*?)<\/workflow-graph>/i)
  if (tag) body = tag[1]
  else {
    const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
    if (fence && /"nodes"\s*:/.test(fence[1])) body = fence[1]
  }
  if (!body.trim()) return null
  let decoded: any
  try { decoded = JSON.parse(body.trim()) } catch { return null }
  if (!decoded || typeof decoded !== 'object') return null

  const patch: GraphPatch = {}
  if (typeof decoded.name === 'string') patch.name = decoded.name
  if (typeof decoded.description === 'string') patch.description = decoded.description
  if (typeof decoded.category === 'string') patch.category = decoded.category
  if (Array.isArray(decoded.inputs)) {
    patch.inputs = decoded.inputs
      .filter((x: any) => x && typeof x === 'object' && (x.id || x.label))
      .map((x: any) => ({
        id: String(x.id || x.label || '').trim(),
        label: String(x.label || x.id || '').trim(),
        kind: INPUT_KINDS.includes(x.kind) ? x.kind : 'text',
        required: !!x.required,
      }))
  }
  // Accept both {graph:{nodes,edges}} and a bare {nodes,edges}: the schema asks for the
  // former, but a model that inlines it should not lose the user's work.
  const source = decoded.graph && typeof decoded.graph === 'object' ? decoded.graph : decoded
  const nodes = parseNodes(source.nodes)
  // An empty graph is not an edit — treat the reply as conversational and leave the plan.
  if (nodes.length) patch.graph = { nodes, edges: parseEdges(source.edges, nodes) }

  return (patch.name || patch.description || patch.category || patch.inputs || patch.graph) ? patch : null
}

// The ancestors a node's test must execute first, in dependency order — a node's
// output only makes sense with its upstream context, the same reason the linear
// editor's "run through step N" ran steps 1..N. Triggers are skipped: they have no
// instruction to execute.
export function testChainFor(graph: WorkflowGraph, nodeId: string): GraphNodeDefinition[] {
  const wanted = new Set<string>([nodeId])
  const stack = [nodeId]
  while (stack.length) {
    const current = stack.pop() as string
    for (const edge of graph.edges) {
      if (edge.to === current && !wanted.has(edge.from)) { wanted.add(edge.from); stack.push(edge.from) }
    }
  }
  // Kahn over the wanted subgraph keeps siblings in authoring order.
  const nodes = graph.nodes.filter(node => wanted.has(node.id) && node.type !== 'trigger')
  const pending = new Map(nodes.map(node => [node.id,
    graph.edges.filter(edge => edge.to === node.id && wanted.has(edge.from)
      && graph.nodes.some(n => n.id === edge.from && n.type !== 'trigger')).length]))
  const ordered: GraphNodeDefinition[] = []
  let progress = true
  while (progress) {
    progress = false
    for (const node of nodes) {
      if (!pending.has(node.id) || (pending.get(node.id) ?? 0) > 0) continue
      ordered.push(node)
      pending.delete(node.id)
      for (const edge of graph.edges) if (edge.from === node.id && pending.has(edge.to)) {
        pending.set(edge.to, (pending.get(edge.to) ?? 1) - 1)
        progress = true
      }
      progress = true
    }
  }
  return ordered
}

// A self-contained dry run of one node and its upstream chain, shown in the chat
// thread. It inlines the LIVE plan, so a test reflects unsaved edits; it carries no
// graph block, so the reply never disturbs the canvas.
export function buildNodeTestPrompt(
  snapshot: GraphSnapshot,
  nodeId: string,
  jobInput?: Record<string, unknown>,
): string {
  const chain = testChainFor(snapshot.graph, nodeId)
  const target = chain[chain.length - 1]
  const inputEntries = Object.entries(jobInput ?? {}).filter(([, v]) => v != null && v !== '')
  const lines = [
    '⟦MODE: WORKFLOW TEST RUN⟧ This is a dry run of ONE node of a workflow graph, so I can judge its instruction before approving the plan. Execute the steps below in order — each later step uses the earlier steps\' output — then show me the result of the LAST step in full (and briefly note what each earlier step produced). Do the actual work; do not describe what you would do.',
    'This is a REHEARSAL, but produce the REAL end result — the point is judging the actual output, not a description of it. If a step produces a file, design, or artifact, actually create it, under these hard rules: (1) NEVER modify or overwrite any existing project file — a rehearsal must not touch real deliverables, and such changes cannot be undone; (2) every file you create must be clearly named as a test: insert "-test" before the extension or into the artifact name (e.g. design-test, post-x-test.md); (3) end your reply with a list of every file you created, so they are easy to find and delete.',
    'REUSE earlier rehearsals: if this conversation already contains a tested result for an upstream step and that step\'s instruction below is unchanged, reuse that result as the step\'s output instead of redoing the work — say "(reusing earlier test result)" next to it. Redo a step only if its instruction changed or I ask for a fresh take.',
    '',
    ...(inputEntries.length ? [
      'Workflow input (use these values where a step references {{id}}):',
      ...inputEntries.map(([key, value]) => `- {{${key}}} = ${typeof value === 'string' ? value : JSON.stringify(value)}`),
      '',
    ] : snapshot.inputs.length ? [
      'Declared inputs (use sensible sample values where a step references {{id}}):',
      ...snapshot.inputs.map(x => `- {{${x.id}}} — ${x.label}${x.required ? ' (required)' : ''}`),
      '',
    ] : []),
    'Steps:',
    ...chain.map((node, index) => {
      // A script step's "instruction" is its command line — say so, so the
      // rehearsal actually runs the library script rather than improvising.
      const step = node.type === 'script'
        ? `Run the project script scripts/${node.command ?? ''}${node.args?.length ? ` with args: ${node.args.join(' ')}` : ''} and use its stdout as this step's output.`
        : node.instruction
      const parts = [`${index + 1}. ${node.name || node.id}${node.id === target?.id ? '  ← the node under test' : ''}: ${step}`]
      if (node.expected_output) parts.push(`   Expected: ${node.expected_output}`)
      if (node.rules) parts.push(`   Rules: ${node.rules}`)
      return parts.join('\n')
    }),
  ]
  return lines.join('\n')
}
