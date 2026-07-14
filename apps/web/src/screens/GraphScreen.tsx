import React from 'react'
import {
  approveGraphJob,
  approveGraphNode,
  createGraphJob,
  editGraphNodeOutput,
  getGraphJob,
  listGraphJobs,
  listGraphTemplates,
  rerunGraphNode,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import type {
  GraphJob,
  GraphNodeDefinition,
  GraphNodeState,
  GraphOutputKind,
  GraphTemplate,
  GraphWorkflowDraft,
  Project,
  WorkflowGraph,
} from '../types'
import { layoutGraph } from './graphLayout'

const OUTPUT_KINDS: GraphOutputKind[] = ['text', 'json', 'artifact-ref']

function stateFor(job: GraphJob, nodeId: string): GraphNodeState | undefined {
  return job.node_states.find(state => state.node_id === nodeId)
}

function outputText(state?: GraphNodeState): string {
  if (state?.output == null) return ''
  return typeof state.output === 'string' ? state.output : JSON.stringify(state.output, null, 2)
}

function statusLabel(status: GraphJob['status'] | GraphNodeState['status']): string {
  return status.replaceAll('_', ' ')
}

function GraphCanvas({ job, selectedId, onSelect }: {
  job: GraphJob
  selectedId: string | null
  onSelect: (nodeId: string) => void
}) {
  const layout = React.useMemo(() => layoutGraph(job.graph), [job.graph])
  const positions = React.useMemo(
    () => new Map(layout.nodes.map(node => [node.id, node])),
    [layout.nodes],
  )

  return <div className="graph-canvas-scroll">
    <svg
      className="graph-canvas"
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="img"
      aria-label={`${job.title} workflow graph`}
    >
      <defs>
        <marker id="graph-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">
          <path className="graph-arrow-head" d="M0,0 L0,6 L9,3 z" />
        </marker>
      </defs>
      {job.graph.edges.map(edge => {
        const source = positions.get(edge.from)
        const target = positions.get(edge.to)
        if (!source || !target) return null
        const x1 = source.x + source.width
        const y1 = source.y + source.height / 2
        const x2 = target.x
        const y2 = target.y + target.height / 2
        const bend = Math.max(36, (x2 - x1) / 2)
        return <path
          key={`${edge.from}:${edge.to}`}
          className="graph-edge"
          d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
          markerEnd="url(#graph-arrow)"
        />
      })}
      {layout.nodes.map(position => {
        const definition = job.graph.nodes.find(node => node.id === position.id)
        if (!definition) return null
        const state = stateFor(job, definition.id)
        const selected = definition.id === selectedId
        return <g
          key={definition.id}
          className={`graph-node st-${state?.status ?? 'pending'}${selected ? ' selected' : ''}`}
          role="button"
          tabIndex={0}
          aria-label={`${definition.name}, ${statusLabel(state?.status ?? 'pending')}`}
          onClick={() => onSelect(definition.id)}
          onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') onSelect(definition.id)
          }}
        >
          <rect x={position.x} y={position.y} width={position.width} height={position.height} rx="12" />
          <circle cx={position.x + 22} cy={position.y + 24} r="6" />
          <text className="graph-node-name" x={position.x + 38} y={position.y + 29}>
            {definition.name.length > 24 ? `${definition.name.slice(0, 23)}…` : definition.name}
          </text>
          <text className="graph-node-kind" x={position.x + 22} y={position.y + 55}>{definition.output_kind}</text>
          <text className="graph-node-status" x={position.x + 22} y={position.y + 76}>
            {statusLabel(state?.status ?? 'pending')}{definition.review_required ? ' · review gate' : ''}
          </text>
        </g>
      })}
    </svg>
  </div>
}

export function GraphScreen({
  token,
  projects,
  activeProject,
  onActiveProject,
  profileId,
  pendingDraft,
  onDraftConsumed,
  pendingJobId,
  onPendingConsumed,
}: {
  token: string
  projects: Project[]
  activeProject: Project | null
  onActiveProject?: (project: Project) => void
  profileId?: number | null
  pendingDraft?: GraphWorkflowDraft | null
  onDraftConsumed?: () => void
  pendingJobId?: number | null
  onPendingConsumed?: () => void
}) {
  const [jobs, setJobs] = React.useState<GraphJob[]>([])
  const [templates, setTemplates] = React.useState<GraphTemplate[]>([])
  const [job, setJob] = React.useState<GraphJob | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [plan, setPlan] = React.useState<WorkflowGraph | null>(null)
  const [dirty, setDirty] = React.useState(false)
  const [outputEdit, setOutputEdit] = React.useState('')
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const [busy, setBusy] = React.useState<string | null>(null)
  const mounted = React.useRef(true)
  const loadSeq = React.useRef(0)
  const draftSeq = React.useRef(0)

  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      loadSeq.current += 1
      draftSeq.current += 1
    }
  }, [])

  const refreshList = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [jobResponse, templateResponse] = await Promise.all([
        listGraphJobs(token, activeProject?.slug),
        listGraphTemplates(token, activeProject?.slug),
      ])
      if (mounted.current && seq === loadSeq.current) {
        setJobs(jobResponse.items)
        setTemplates(templateResponse.items)
      }
    } catch (cause) {
      if (mounted.current && seq === loadSeq.current) setError(String(cause))
    }
  }, [token, activeProject?.slug])

  const loadJob = React.useCallback(async (jobId: number) => {
    const seq = ++loadSeq.current
    try {
      const next = await getGraphJob(token, jobId)
      if (!mounted.current || seq !== loadSeq.current) return
      setJob(next)
      setPlan(next.graph)
      setDirty(false)
      setSelectedId(current => current && next.graph.nodes.some(node => node.id === current)
        ? current
        : next.graph.nodes[0]?.id ?? null)
    } catch (cause) {
      if (mounted.current && seq === loadSeq.current) setError(String(cause))
    }
  }, [token])

  React.useEffect(() => { void refreshList() }, [refreshList])

  React.useEffect(() => {
    if (!pendingJobId) return
    void loadJob(pendingJobId)
    onPendingConsumed?.()
  }, [pendingJobId, loadJob, onPendingConsumed])

  React.useEffect(() => {
    if (!pendingDraft) return
    const seq = ++draftSeq.current
    onDraftConsumed?.()
    setBusy('create')
    setError('')
    void createGraphJob(token, {
      title: pendingDraft.name,
      graph: pendingDraft.graph,
      project_slug: activeProject?.slug,
      profile_id: profileId,
    }).then(created => {
      if (!mounted.current || seq !== draftSeq.current) return
      setJob(created)
      setPlan(created.graph)
      setSelectedId(created.graph.nodes[0]?.id ?? null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('Architect draft ready. Review or edit the frozen plan before starting.')
    }).catch(cause => {
      if (mounted.current && seq === draftSeq.current) setError(String(cause))
    }).finally(() => {
      if (mounted.current && seq === draftSeq.current) setBusy(null)
    })
  }, [pendingDraft, token, activeProject?.slug, profileId, onDraftConsumed])

  React.useEffect(() => {
    if (!job || !['running', 'review'].includes(job.status)) return
    const timer = window.setInterval(() => { void loadJob(job.id) }, 1500)
    return () => clearInterval(timer)
  }, [job?.id, job?.status, loadJob])

  const definition = plan?.nodes.find(node => node.id === selectedId)
  const selectedState = job && selectedId ? stateFor(job, selectedId) : undefined
  React.useEffect(() => { setOutputEdit(outputText(selectedState)) }, [selectedState?.id, selectedState?.version])

  function updateSelected(patch: Partial<GraphNodeDefinition>) {
    if (!definition || !plan) return
    setPlan({ ...plan, nodes: plan.nodes.map(node => node.id === definition.id ? { ...node, ...patch } : node) })
    setDirty(true)
  }

  function toggleDependency(dependencyId: string) {
    if (!definition || !plan) return
    const exists = plan.edges.some(edge => edge.from === dependencyId && edge.to === definition.id)
    const edges = exists
      ? plan.edges.filter(edge => !(edge.from === dependencyId && edge.to === definition.id))
      : [...plan.edges, { from: dependencyId, to: definition.id }]
    setPlan({ ...plan, edges })
    setDirty(true)
  }

  function addNode() {
    if (!plan) return
    let index = plan.nodes.length + 1
    while (plan.nodes.some(node => node.id === `node-${index}`)) index += 1
    const node: GraphNodeDefinition = {
      id: `node-${index}`,
      name: `Node ${index}`,
      instruction: '',
      output_kind: 'text',
    }
    setPlan({ ...plan, nodes: [...plan.nodes, node] })
    setSelectedId(node.id)
    setDirty(true)
  }

  function removeNode() {
    if (!definition || !plan || plan.nodes.length <= 1) return
    const nodes = plan.nodes.filter(node => node.id !== definition.id)
    setPlan({
      nodes,
      edges: plan.edges.filter(edge => edge.from !== definition.id && edge.to !== definition.id),
    })
    setSelectedId(nodes[0]?.id ?? null)
    setDirty(true)
  }

  async function act(label: string, action: () => Promise<GraphJob>, message?: string) {
    if (busy) return
    setBusy(label)
    setError('')
    setNotice('')
    try {
      const next = await action()
      if (!mounted.current) return
      setJob(next)
      setPlan(next.graph)
      setDirty(false)
      setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
      if (message) setNotice(message)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function savePlan() {
    if (!job || !plan) return
    await act('save-plan', () => updateGraphPlan(token, job.id, plan), 'Plan changes saved.')
  }

  async function saveOutput() {
    if (!job || !definition) return
    let value: unknown = outputEdit
    if (definition.output_kind !== 'text') {
      try {
        value = JSON.parse(outputEdit)
      } catch {
        setError('JSON and artifact-reference outputs must be valid JSON.')
        return
      }
    }
    await act('save-output', () => editGraphNodeOutput(token, job.id, definition.id, value), 'Output corrected; dependent nodes were marked stale.')
  }

  async function saveTemplate() {
    if (!job) return
    if (busy) return
    setBusy('save-template')
    setError('')
    try {
      const template = await saveGraphTemplate(token, job.id, { name: job.title })
      if (mounted.current) {
        setTemplates(current => [{
          id: template.id,
          project_id: job.project_id,
          project_slug: job.project_slug,
          name: template.name,
          status: 'active',
          graph: job.graph,
        }, ...current.filter(item => item.id !== template.id)])
        setNotice(`Saved reusable workflow “${template.name}”.`)
      }
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function createFromTemplate(template: GraphTemplate) {
    if (busy) return
    setBusy('use-template')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: template.name,
        graph: template.graph,
        workflow_id: template.id,
        project_slug: activeProject?.slug ?? template.project_slug,
        profile_id: profileId,
      })
      if (!mounted.current) return
      setJob(created)
      setPlan(created.graph)
      setSelectedId(created.graph.nodes[0]?.id ?? null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice(`Created a queued run from “${template.name}”. Review the frozen plan before starting.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  const allDone = !!job?.node_states.length && job.node_states.every(state => state.status === 'done')

  return <section className="graph-screen">
    <header className="graph-header">
      <div>
        <p className="graph-eyebrow">Workflow graph</p>
        <h1>{job?.title ?? 'Graph workflows'}</h1>
        <p className="muted">Review the frozen DAG, then explicitly approve its execution.</p>
      </div>
      <div className="graph-header-actions">
        {job?.status === 'queued' && <>
          <button className="ghost-button" onClick={() => void saveTemplate()} disabled={!!busy || dirty}>Save template</button>
          <button className="primary-button" onClick={() => void act('start', () => startGraphJob(token, job.id), 'Plan approved. Execution started.')} disabled={!!busy || dirty}>
            {busy === 'start' ? 'Starting…' : 'Approve plan & start'}
          </button>
        </>}
        {job?.status === 'review' && allDone && <button className="primary-button" onClick={() => void act('approve-job', () => approveGraphJob(token, job.id))} disabled={!!busy}>
          {busy === 'approve-job' ? 'Approving…' : 'Approve final result'}
        </button>}
      </div>
    </header>

    {error && <div className="error-bar">{error}</div>}
    {notice && <div className="graph-notice">{notice}</div>}
    {busy === 'create' && <p className="graph-loading">Materializing architect draft…</p>}

    <div className="graph-workspace">
      <aside className="graph-job-list">
        <label className="graph-project-filter">Project
          <select value={activeProject?.slug ?? ''} onChange={event => {
            const project = projects.find(item => item.slug === event.target.value)
            if (project) onActiveProject?.(project)
          }}>
            {projects.map(project => <option key={project.slug} value={project.slug}>{project.name}</option>)}
          </select>
        </label>
        <div className="graph-list-head"><strong>Plans</strong><button className="row-action" onClick={() => void refreshList()} aria-label="Refresh graph plans">↻</button></div>
        {jobs.length === 0
          ? <p className="muted graph-empty-list">No graph plans yet. Promote a chat to create one.</p>
          : jobs.map(item => <button key={item.id} className={`graph-job-row${job?.id === item.id ? ' selected' : ''}`} onClick={() => void loadJob(item.id)}>
              <span>{item.title}</span><small>{statusLabel(item.status)}</small>
            </button>)}
        <div className="graph-list-head"><strong>Templates</strong></div>
        {templates.length === 0
          ? <p className="muted graph-empty-list">No saved graph templates.</p>
          : templates.map(template => <button key={template.id} className="graph-job-row" onClick={() => void createFromTemplate(template)} disabled={!!busy}>
              <span>{template.name}</span><small>New queued run</small>
            </button>)}
      </aside>

      <main className="graph-main">
        {!job || !plan
          ? <div className="graph-empty"><strong>Select a graph plan</strong><p className="muted">Architect drafts and graph executions appear here.</p></div>
          : <>
            <div className="graph-status-bar">
              <span className={`graph-status st-${job.status}`}>{statusLabel(job.status)}</span>
              <span>{job.node_states.filter(state => state.status === 'done').length}/{job.node_states.length} nodes done</span>
              {dirty && <span className="graph-dirty">Unsaved plan edits</span>}
            </div>
            <GraphCanvas job={{ ...job, graph: plan }} selectedId={selectedId} onSelect={setSelectedId} />
          </>}
      </main>

      <aside className="graph-inspector">
        {!job || !plan || !definition
          ? <div className="graph-empty"><strong>Node inspector</strong><p className="muted">Select a node to inspect its contract and output.</p></div>
          : <>
            <div className="graph-inspector-head">
              <div><p className="graph-eyebrow">Node</p><h2>{definition.name}</h2></div>
              <span className={`graph-status st-${selectedState?.status ?? 'pending'}`}>{statusLabel(selectedState?.status ?? 'pending')}</span>
            </div>
            {job.status === 'queued' ? <div className="graph-plan-form">
              <label>Name<input value={definition.name} onChange={event => updateSelected({ name: event.target.value })} /></label>
              <label>Instruction<textarea rows={6} value={definition.instruction} onChange={event => updateSelected({ instruction: event.target.value })} /></label>
              <label>Output contract<select value={definition.output_kind} onChange={event => updateSelected({ output_kind: event.target.value as GraphOutputKind })}>
                {OUTPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
              </select></label>
              <label className="graph-check"><input type="checkbox" checked={!!definition.review_required} onChange={event => updateSelected({ review_required: event.target.checked })} />Require human review</label>
              <fieldset><legend>Dependencies</legend>{plan.nodes.filter(node => node.id !== definition.id).map(node => <label className="graph-check" key={node.id}>
                <input type="checkbox" checked={plan.edges.some(edge => edge.from === node.id && edge.to === definition.id)} onChange={() => toggleDependency(node.id)} />{node.name}
              </label>)}</fieldset>
              <div className="graph-form-actions">
                <button className="ghost-button" onClick={addNode}>Add node</button>
                <button className="ghost-button danger" onClick={removeNode} disabled={plan.nodes.length <= 1}>Remove node</button>
                <button className="primary-button" onClick={() => void savePlan()} disabled={!dirty || !!busy}>{busy === 'save-plan' ? 'Saving…' : 'Save plan'}</button>
              </div>
            </div> : <div className="graph-run-detail">
              <p>{definition.instruction || 'No instruction.'}</p>
              <dl><div><dt>Output</dt><dd>{definition.output_kind}</dd></div><div><dt>Attempt</dt><dd>{selectedState?.run_id ?? '—'}</dd></div></dl>
              {selectedState?.inputs != null && <details><summary>Resolved inputs</summary><pre>{JSON.stringify(selectedState.inputs, null, 2)}</pre></details>}
              {selectedState?.error && <p className="error-text">{selectedState.error}</p>}
              {selectedState?.output != null ? <pre className="graph-output">{outputText(selectedState)}</pre> : <p className="muted">No validated output yet.</p>}
              {job.status === 'review' && selectedState && ['done', 'review', 'failed'].includes(selectedState.status) && <>
                <label>Correct output<textarea rows={8} value={outputEdit} onChange={event => setOutputEdit(event.target.value)} /></label>
                <div className="graph-form-actions">
                  <button className="ghost-button" onClick={() => void act('rerun', () => rerunGraphNode(token, job.id, definition.id))} disabled={!!busy}>Rerun node</button>
                  <button className="ghost-button" onClick={() => void saveOutput()} disabled={!!busy || !outputEdit.trim()}>Save correction</button>
                  {selectedState.status === 'review' && <button className="primary-button" onClick={() => void act('approve-node', () => approveGraphNode(token, job.id, definition.id))} disabled={!!busy}>Approve node</button>}
                </div>
              </>}
            </div>}
          </>}
      </aside>
    </div>
  </section>
}
