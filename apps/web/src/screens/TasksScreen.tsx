import React from 'react'
import type { Project, Task, TaskStatus } from '../types'
import { listTasks, createTask, getTask, updateTask, deleteTask } from '../api/tasks'
import { TaskChat } from '../components/tasks/TaskChat'
import { Dropdown } from '../components/ui/Dropdown'
import { confirmDialog } from '../components/ui/Dialog'
import { BackButton } from '../components/ui/BackButton'

const COLUMNS: { key: TaskStatus; label: string }[] = [
  { key: 'todo', label: 'To do' },
  { key: 'doing', label: 'In progress' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' }
]
const clean = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')

function TaskDetail({ token, task, busyAction, onBack, onStatus, onDelete, onTaskChanged }: { token: string; task: Task; busyAction: string | null; onBack: () => void; onStatus: (t: Task, s: TaskStatus) => void; onDelete: (t: Task) => void; onTaskChanged: () => void }) {
  const taskBusy = !!busyAction
  return <div className="task-detail">
    <div className="task-detail-head">
      <BackButton label="Board" onClick={onBack} />
      <strong className="task-title" title={task.title}>{task.title}</strong>
      <div className="seg sm task-status-seg">{COLUMNS.map(c => <button key={c.key} className={task.status === c.key ? 'active' : ''} disabled={taskBusy || task.status === c.key} onClick={() => onStatus(task, c.key)}>{c.label}</button>)}</div>
      <button className="ghost-button danger" onClick={() => onDelete(task)} disabled={taskBusy}>{busyAction === `delete:${task.id}` ? 'Deleting...' : 'Delete'}</button>
    </div>
    {task.status === 'review' && <div className="task-review-bar">
      <span>✅ Agent finished — review the result. Need changes? Just chat below.</span>
      <button className="primary-button" onClick={() => onStatus(task, 'done')} disabled={taskBusy}>{busyAction === `status:${task.id}` ? 'Saving...' : '✓ Approve & Done'}</button>
    </div>}
    {task.status === 'done' && <div className="task-review-bar done">
      <span>✓ Approved &amp; done.</span>
      <button className="ghost-button" onClick={() => onStatus(task, 'review')} disabled={taskBusy}>{busyAction === `status:${task.id}` ? 'Saving...' : 'Reopen'}</button>
    </div>}
    {task.description && <p className="task-desc">{task.description}</p>}
    {task.session_id ? <div className="task-thread"><TaskChat token={token} task={task} onTaskChanged={onTaskChanged} /></div> : <p className="muted" style={{ padding: 16 }}>No thread linked.</p>}
  </div>
}

export function TasksScreen({ token, projects, activeProject, onActiveProject, pendingTaskId, onPendingConsumed }: { token: string; projects: Project[]; activeProject: Project | null; onActiveProject?: (p: Project) => void; pendingTaskId?: number | null; onPendingConsumed?: () => void }) {
  const [slug, setSlug] = React.useState(activeProject?.slug || projects[0]?.slug || '')
  const [tasks, setTasks] = React.useState<Task[]>([])
  const [selected, setSelected] = React.useState<Task | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [busyAction, setBusyAction] = React.useState<string | null>(null)
  const [form, setForm] = React.useState({ title: '', description: '', assignee: '' })
  const [error, setError] = React.useState('')
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const pendingSeq = React.useRef(0)
  const mountedRef = React.useRef(true)
  const slugRef = React.useRef(slug)
  const project = projects.find(p => p.slug === slug) || null
  React.useEffect(() => { slugRef.current = slug }, [slug])
  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
      pendingSeq.current += 1
    }
  }, [])
  // Follow the global active project (sidebar) so the board, sidebar, and file
  // panel always show the same project.
  React.useEffect(() => { if (activeProject && activeProject.slug !== slug) setSlug(activeProject.slug) }, [activeProject?.slug])
  // Picking a project here updates the global active project too (two-way sync).
  const pickProject = React.useCallback((s: string) => {
    setSlug(s)
    const p = projects.find(x => x.slug === s)
    if (p) onActiveProject?.(p)
  }, [projects, onActiveProject])

  const reload = React.useCallback(async () => {
    const seq = ++loadSeq.current
    if (!project) { setTasks([]); return }
    const body = await listTasks(token, project.slug)
    if (mountedRef.current && seq === loadSeq.current) {
      setError('')
      setTasks(body.tasks)
    }
  }, [token, project?.slug])

  // On project change, drop a selection that belongs to the OLD project — but keep
  // one that matches the new project, so opening a task in another project from the
  // sidebar (which switches project, then selects) isn't immediately snapped shut.
  React.useEffect(() => {
    setSelected(sel => (sel && sel.project_slug === project?.slug ? sel : null))
    const p = reload()
    const seq = loadSeq.current
    void p.catch(e => {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    })
  }, [reload, project?.slug])

  // Open a specific task when navigated from the sidebar.
  React.useEffect(() => {
    if (!pendingTaskId) {
      pendingSeq.current += 1
      return
    }
    const seq = ++pendingSeq.current
    getTask(token, pendingTaskId)
      .then(t => {
        if (!mountedRef.current || seq !== pendingSeq.current) return
        if (t.project_slug) pickProject(t.project_slug)
        setError('')
        setSelected(t)
      })
      .catch(e => {
        if (mountedRef.current && seq === pendingSeq.current) setError(String(e))
      })
      .finally(() => {
        if (mountedRef.current && seq === pendingSeq.current) onPendingConsumed?.()
      })
  }, [token, pendingTaskId, pickProject, onPendingConsumed])

  async function submitCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!form.title.trim() || !project || busyAction) return
    const seq = ++actionSeq.current
    const projectSlug = project.slug
    setBusyAction('create'); setError('')
    try {
      const t = await createTask(token, projectSlug, { title: form.title.trim(), description: form.description.trim() || undefined, assignee: form.assignee.trim() || undefined })
      if (!mountedRef.current || seq !== actionSeq.current || slugRef.current !== projectSlug) return
      setCreating(false); setForm({ title: '', description: '', assignee: '' }); await reload(); if (mountedRef.current && seq === actionSeq.current) setSelected(t)
    } catch (e2) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e2))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusyAction(null)
    }
  }
  async function setStatus(t: Task, status: TaskStatus) {
    if (busyAction || t.status === status) return
    const seq = ++actionSeq.current
    setBusyAction(`status:${t.id}`); setError('')
    try {
      const u = await updateTask(token, t.id, { status })
      if (mountedRef.current && seq === actionSeq.current) {
        setTasks(cur => cur.map(x => x.id === t.id ? u : x))
        setSelected(cur => cur?.id === t.id ? u : cur)
      }
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusyAction(null)
    }
  }
  async function remove(t: Task) {
    if (busyAction) return
    if (!(await confirmDialog({ title: `Delete task "${t.title}"?`, message: 'Its thread will be removed too.', confirmLabel: 'Delete', danger: true }))) return
    const seq = ++actionSeq.current
    setBusyAction(`delete:${t.id}`); setError('')
    try {
      await deleteTask(token, t.id)
      if (mountedRef.current && seq === actionSeq.current) {
        setSelected(cur => cur?.id === t.id ? null : cur)
        await reload()
      }
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusyAction(null)
    }
  }
  // After an agent run finishes, re-fetch the task so its status (e.g. → Review) updates live.
  async function refreshTask(id: number) {
    const seq = ++actionSeq.current
    try {
      const u = await getTask(token, id)
      if (!mountedRef.current || seq !== actionSeq.current) return
      setSelected(cur => cur?.id === id ? u : cur)
      setTasks(cur => cur.map(x => x.id === id ? u : x))
    } catch { /* ignore */ }
  }

  if (projects.length === 0) return <section className="placeholder-view"><div className="assistant-bubble compact"><h1>Tasks</h1><p>No projects yet.</p></div></section>

  if (selected) return <section className="tasks-view"><TaskDetail token={token} task={selected} busyAction={busyAction} onBack={() => setSelected(null)} onStatus={setStatus} onDelete={remove} onTaskChanged={() => void refreshTask(selected.id)} /></section>

  return <section className="tasks-view">
    <div className="tasks-head">
      <Dropdown value={slug} onChange={pickProject} minWidth={200} options={projects.map(p => ({ value: p.slug, label: clean(p.name) }))} />
      <button className="primary-button" onClick={() => setCreating(true)} disabled={!!busyAction}>New task</button>
    </div>
    {creating && <div className="modal-scrim" onClick={() => { if (!busyAction) setCreating(false) }}><form className="modal-card" onClick={e => e.stopPropagation()} onSubmit={submitCreate}>
      <h3>New task</h3>
      <label>Title<input autoFocus value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} placeholder="e.g. Fix login bug" /></label>
      <label>Description <span className="muted">(optional)</span><textarea rows={3} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="What needs doing / context for the agent" /></label>
      <label>Assignee <span className="muted">(optional)</span><input value={form.assignee} onChange={e => setForm({ ...form, assignee: e.target.value })} placeholder="user or agent" /></label>
      <div className="modal-actions"><button type="button" className="ghost-button" onClick={() => setCreating(false)} disabled={busyAction === 'create'}>Cancel</button><button type="submit" className="primary-button" disabled={!form.title.trim() || busyAction === 'create'}>{busyAction === 'create' ? 'Creating...' : 'Create task'}</button></div>
    </form></div>}
    {error && <div className="error-bar">{error}</div>}
    <div className="kanban">{COLUMNS.map(col => {
      const items = tasks.filter(t => t.status === col.key)
      return <div className="kanban-col" key={col.key}>
        <div className="kanban-col-head"><span>{col.label}</span><span className="kanban-count">{items.length}</span></div>
        <div className="kanban-cards">{items.map(t => <div className="kanban-card" key={t.id} role="button" tabIndex={0} onClick={() => setSelected(t)} onKeyDown={e => { if (e.key === 'Enter') setSelected(t) }}>
          <button className="kanban-del" title="Delete task" aria-label="Delete task" disabled={!!busyAction} onClick={e => { e.stopPropagation(); void remove(t) }}>×</button>
          <strong>{t.title}</strong>
          {(t.assignee || t.description) && <small>{t.assignee || t.description.slice(0, 60)}</small>}
          {t.created_by && <small className="kanban-by">by {t.created_by}</small>}
        </div>)}</div>
      </div>
    })}</div>
  </section>
}
