import React from 'react'
import {
  answerGraphNode,
  approveGraphJob,
  approveGraphNode,
  approveGraphNodeScript,
  createGraphJob,
  deleteGraphJob,
  deleteGraphTemplate,
  editGraphNodeOutput,
  setGraphTemplateStatus,
  getGraphJob,
  listGraphJobs,
  listGraphTemplates,
  rerunGraphNode,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import { listSchedules } from '../api/schedules'
import {
  cronHint,
  DEFAULT_GRAPH_SCHEDULE,
  ScheduleManager,
  ScheduleSettingsEditor,
} from '../components/workflows/ScheduleManager'
import { cronLabelsByWorkflow } from '../lib/scheduleBadges'
import { activeRuns } from '../api/runs'
import { getJobDiff } from '../api/jobs'
import { runnerCapabilities } from '../api/profiles'
import { listProjectAreas } from '../api/projects'
import { IconArtifacts, IconLock, IconPencil, IconPlus, IconSearch, IconTrash } from '../components/shell/icons'
import { GraphCanvas, stateFor, statusLabel } from '../components/workflows/GraphCanvas'
import { SatpamCard } from '../components/tasks/SatpamCard'
import { ScriptApprovalCard } from '../components/workflows/ScriptApprovalCard'
import { SaveTemplateModal, WorkflowInputsEditor } from '../components/workflows/SaveTemplateModal'
import { MentionTextarea } from '../components/ui/MentionTextarea'
import { confirmDialog } from '../components/ui/Dialog'
import { RunModal } from '../components/workflows/RunModal'
import { AuthoringChat, type WorkflowChatHandle } from '../components/workflows/AuthoringChat'
import { buildGraphPrompt, buildNodeTestPrompt, parseGraphDraft, stripGraphBlock } from '../components/workflows/graphPrompt'
import { useDragWidth } from '../hooks/useDragWidth'
import { useEventStream } from '../hooks/useEventStream'
import { usePolling } from '../hooks/usePolling'
import { useProjectMentionItems } from '../hooks/useProjectMentionItems'
import type {
  AppFeatures,
  GraphJob,
  GraphNodeDefinition,
  GraphNodeState,
  GraphOutputKind,
  GraphTemplate,
  GraphWorkflowDraft,
  DetectedSkill,
  Profile,
  Project,
  Schedule,
  WorkflowGraph,
  WorkflowInput,
} from '../types'
import { planStatusLabel, planStatusTone } from '../components/tasks/planProjection'
import { formatRunAge, formatRunDuration, projectRun } from '../lib/runProjection'
import { layoutGraph } from './graphLayout'

const OUTPUT_KINDS: GraphOutputKind[] = ['text', 'json', 'artifact-ref']
const HOME_TAB_KEY = 'proxima.graph.homeTab'
const DRAFT_META_KEY_PREFIX = 'proxima.graph.draftMeta.'
const AUTOSAVE_DELAY_MS = 700

function normalizedPlanTitle(value: string): string {
  return value.trim() || 'Untitled plan'
}

type DraftTemplateMeta = {
  name?: string
  description?: string
  category?: string
}

type RunTarget =
  | { kind: 'job'; job: GraphJob }
  | { kind: 'template'; template: GraphTemplate; createdJobId?: number }

function readDraftMeta(jobId: number): DraftTemplateMeta {
  try {
    const raw = localStorage.getItem(`${DRAFT_META_KEY_PREFIX}${jobId}`)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as DraftTemplateMeta
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function graphTrigger(graph: WorkflowGraph | null | undefined): GraphNodeDefinition | undefined {
  return graph?.nodes.find(node => node.type === 'trigger')
}

function triggerInputs(graph: WorkflowGraph | null | undefined): WorkflowInput[] {
  return graphTrigger(graph)?.inputs ?? []
}

type WorkflowHomeTab = 'drafts' | 'workflows' | 'runs'

function readWorkflowHomeTab(): WorkflowHomeTab {
  try {
    const value = localStorage.getItem(HOME_TAB_KEY)
    if (value === 'drafts' || value === 'workflows' || value === 'runs') return value
  } catch { /* storage disabled */ }
  return 'workflows'
}

function outputText(state?: GraphNodeState): string {
  if (state?.output == null) return ''
  return typeof state.output === 'string' ? state.output : JSON.stringify(state.output, null, 2)
}

/** Per-job Plan Chat open preference — survives leave/reopen of Workflows editor. */
export function graphChatOpenKey(jobId: number): string {
  return `proxima.graph.chatOpen.${jobId}`
}

function readChatOpen(jobId: number): boolean | null {
  try {
    const raw = localStorage.getItem(graphChatOpenKey(jobId))
    if (raw === '1') return true
    if (raw === '0') return false
  } catch { /* storage disabled */ }
  return null
}

function writeChatOpen(jobId: number, open: boolean) {
  try {
    localStorage.setItem(graphChatOpenKey(jobId), open ? '1' : '0')
  } catch { /* storage disabled */ }
}

/** True when `from` can already be reached from `to`, i.e. the edge would cycle. */
function wouldCycle(graph: WorkflowGraph, from: string, to: string): boolean {
  const seen = new Set<string>()
  const stack = [to]
  while (stack.length) {
    const current = stack.pop() as string
    if (current === from) return true
    if (seen.has(current)) continue
    seen.add(current)
    for (const edge of graph.edges) if (edge.from === current) stack.push(edge.to)
  }
  return false
}

// Module-level so React Strict Mode remounts share one in-flight create. A plain
// component ref is wiped on remount and would POST /api/graph/jobs twice — the
// live failure mode behind duplicate todo-storage-decision rows on Tasks.
const inflightDraftCreates = new WeakMap<GraphWorkflowDraft, Promise<GraphJob>>()

/**
 * Start at most one createGraphJob per draft object. Strict Mode effect re-runs
 * and parent-prop churn reuse the same promise; failures drop the entry so retry
 * can try again.
 */
export function getOrStartDraftCreate(
  draft: GraphWorkflowDraft,
  start: () => Promise<GraphJob>,
): Promise<GraphJob> {
  const existing = inflightDraftCreates.get(draft)
  if (existing) return existing
  const promise = start().catch(err => {
    inflightDraftCreates.delete(draft)
    throw err
  })
  inflightDraftCreates.set(draft, promise)
  return promise
}

/**
 * One workflow/plan owns one project. Prefer the owned entity's project_slug over
 * the shell's active project so switching the shell filter cannot retarget an open
 * template, job, or schedule mid-edit/run.
 */
export function resolveOwnedProjectSlug(
  owned: { project_slug?: string | null } | null | undefined,
  shellSlug?: string | null,
): string | null {
  const ownedSlug = owned?.project_slug?.trim()
  if (ownedSlug) return ownedSlug
  const shell = shellSlug?.trim()
  return shell || null
}

export function GraphScreen({
  token,
  projects,
  activeProject,
  onActiveProject,
  profiles,
  profileId,
  features,
  activeProfile,
  pendingDraft,
  onDraftConsumed,
  pendingJobId,
  onPendingConsumed,
  onStageChange,
  backNonce,
}: {
  token: string
  projects: Project[]
  activeProject: Project | null
  onActiveProject?: (project: Project) => void
  profiles: Profile[]
  profileId?: number | null
  features: AppFeatures
  activeProfile: Profile | null
  pendingDraft?: GraphWorkflowDraft | null
  onDraftConsumed?: () => void
  pendingJobId?: number | null
  onPendingConsumed?: () => void
  /** Lets the shell place the back control in its own chrome (the tab row). */
  onStageChange?: (stage: 'home' | 'editor', jobId: number | null) => void
  backNonce?: number
}) {
  const [jobs, setJobs] = React.useState<GraphJob[]>([])
  const [templates, setTemplates] = React.useState<GraphTemplate[]>([])
  /** Real schedule rows are the current trigger truth until scheduled trigger nodes ship. */
  const [schedules, setSchedules] = React.useState<Schedule[]>([])
  /** Short cron labels per workflow for how-it-runs badges. */
  const [scheduleCronByWorkflow, setScheduleCronByWorkflow] = React.useState<Map<number, string[]>>(() => new Map())
  const [job, setJob] = React.useState<GraphJob | null>(null)
  /** Target id while open/load is in flight so stage reports never republish a prior job. */
  const [openingJobId, setOpeningJobId] = React.useState<number | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [chatWidth, dragChat] = useDragWidth('proxima.graph.chatWidth', 352, 240, 620)
  const [inspectorWidth, dragInspector] = useDragWidth('proxima.graph.inspectorWidth', 336, 260, 720)
  const [homeTab, setHomeTab] = React.useState<WorkflowHomeTab>(readWorkflowHomeTab)
  const [homeQueries, setHomeQueries] = React.useState<Record<WorkflowHomeTab, string>>({
    drafts: '',
    workflows: '',
    runs: '',
  })
  const [showArchived, setShowArchived] = React.useState(false)
  const [schedulingTemplate, setSchedulingTemplate] = React.useState<GraphTemplate | null>(null)
  React.useEffect(() => {
    try { localStorage.setItem(HOME_TAB_KEY, homeTab) } catch { /* storage disabled */ }
  }, [homeTab])
  // Two stages, Design Studio's shape: a browsable home, and an editor focused on
  // one workflow. Browsing and editing are different modes of work.
  const [stage, setStage] = React.useState<'home' | 'editor'>('home')
  // Hero hand-off: the description the chat should speak first once the editor opens.
  const [initialAuthorText, setInitialAuthorText] = React.useState<string | null>(null)
  // Default closed; restored per job from localStorage (and auto-opened when that
  // plan's authoring session still has an in-flight run).
  const [chatOpen, setChatOpen] = React.useState(false)
  const chatRef = React.useRef<WorkflowChatHandle>(null)
  // A test asked for while the chat panel is closed: the panel must mount before the
  // ref exists, so the request waits one render here.
  const [pendingTest, setPendingTest] = React.useState<string | null>(null)
  const [skillsByRunner, setSkillsByRunner] = React.useState<Record<string, DetectedSkill[]>>({})
  const skillFetches = React.useRef(new Set<string>())
  // The plan project's code areas (T1) — the vocabulary of the "Works in" field
  // and the authoring chat's target instruction. Keyed per slug so switching
  // projects never shows another project's repos.
  const [areasBySlug, setAreasBySlug] = React.useState<Record<string, string[]>>({})
  const loadSkills = React.useCallback((runnerId: string | undefined) => {
    if (!runnerId || skillFetches.current.has(runnerId)) return
    skillFetches.current.add(runnerId)
    runnerCapabilities(token, runnerId)
      .then(caps => { if (mounted.current) setSkillsByRunner(current => ({ ...current, [runnerId]: caps.skills })) })
      .catch(() => { skillFetches.current.delete(runnerId) })
  }, [token])
  const [savingTemplate, setSavingTemplate] = React.useState(false)
  // Drafts and reusable workflows share one execution intake boundary.
  const [runTarget, setRunTarget] = React.useState<RunTarget | null>(null)
  // Template metadata the authoring chat proposed. It stays secondary to the canvas,
  // and one-click promotion carries it into the reusable workflow record.
  const [draftMeta, setDraftMeta] = React.useState<DraftTemplateMeta>({})
  const [plan, setPlan] = React.useState<WorkflowGraph | null>(null)
  const [draftTitle, setDraftTitle] = React.useState('Untitled plan')
  const [renamingTitle, setRenamingTitle] = React.useState(false)
  const [saveState, setSaveState] = React.useState<'saved' | 'saving' | 'error'>('saved')
  const [intakeEditState, setIntakeEditState] = React.useState({ dirty: false, valid: true })
  const [outputEdit, setOutputEdit] = React.useState('')
  const [answerText, setAnswerText] = React.useState('')
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const [busy, setBusy] = React.useState<string | null>(null)
  const mounted = React.useRef(true)
  const listLoadSeq = React.useRef(0)
  const jobLoadSeq = React.useRef(0)
  const wantedJobIdRef = React.useRef<number | null>(null)
  const focusedJobIdRef = React.useRef<number | null>(null)
  const draftSeq = React.useRef(0)

  // Intentional navigation / create / start paths retarget focus.
  const focusJob = React.useCallback((next: GraphJob | null) => {
    wantedJobIdRef.current = next?.id ?? null
    focusedJobIdRef.current = next?.id ?? null
    setJob(next)
  }, [])
  // Autosave, live poll, and other same-job refreshes must never steal focus.
  const applyFocusedJob = React.useCallback((next: GraphJob) => {
    if (wantedJobIdRef.current !== next.id) return false
    focusedJobIdRef.current = next.id
    setJob(next)
    return true
  }, [])
  React.useEffect(() => {
    setRenamingTitle(false)
  }, [job?.id])
  const saveTimer = React.useRef<number | undefined>(undefined)
  const saveInFlight = React.useRef<Promise<void> | null>(null)
  const pendingSave = React.useRef<{
    jobId: number
    title: string
    graph?: WorkflowGraph
    graphBody?: string
  } | null>(null)
  const lastSavedTitle = React.useRef('')
  const lastSavedGraph = React.useRef('')
  const autosaveJobId = React.useRef<number | null>(null)
  const latestDraft = React.useRef<{
    jobId: number
    status: GraphJob['status']
    title: string
    graph: WorkflowGraph
  } | null>(null)
  // Plan Chat open preference is per job; only re-read storage when the job id changes
  // (loadJob also runs on the live poll, which must not clobber a mid-session toggle).
  const chatJobRef = React.useRef<number | null>(null)

  if (job && plan) {
    latestDraft.current = {
      jobId: job.id,
      status: job.status,
      title: normalizedPlanTitle(draftTitle),
      graph: plan,
    }
  } else {
    latestDraft.current = null
  }

  function adoptAutosave(next: GraphJob, meta: DraftTemplateMeta = readDraftMeta(next.id)) {
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    pendingSave.current = null
    autosaveJobId.current = next.id
    lastSavedTitle.current = next.title
    lastSavedGraph.current = JSON.stringify(next.graph)
    setDraftTitle(next.title || 'Untitled plan')
    setDraftMeta(meta)
    setSaveState('saved')
  }

  async function primeAutosave(
    next: GraphJob,
    meta: DraftTemplateMeta = readDraftMeta(next.id),
    opts?: { seq?: number; requireWantedId?: number },
  ): Promise<boolean> {
    await flushAutosave()
    if (!mounted.current) return false
    if (opts?.seq != null && opts.seq !== jobLoadSeq.current) return false
    if (opts?.requireWantedId != null && wantedJobIdRef.current !== opts.requireWantedId) return false
    adoptAutosave(next, meta)
    return true
  }

  const drainAutosave = React.useCallback((): Promise<void> => {
    if (saveInFlight.current) return saveInFlight.current
    const work = (async () => {
      while (pendingSave.current) {
        const snapshot = pendingSave.current
        pendingSave.current = null
        let next: GraphJob
        try {
          next = await updateGraphPlan(token, snapshot.jobId, {
            title: snapshot.title,
            ...(snapshot.graph ? { graph: snapshot.graph } : {}),
          })
        } catch (cause) {
          if (!pendingSave.current) pendingSave.current = snapshot
          if (mounted.current) {
            setSaveState('error')
            setError(String(cause))
          }
          throw cause
        }
        if (autosaveJobId.current === snapshot.jobId) {
          lastSavedTitle.current = next.title
          if (snapshot.graph) lastSavedGraph.current = JSON.stringify(next.graph)
        }
        const latest = latestDraft.current
        const isLatest = latest?.jobId === snapshot.jobId
          && latest.title === snapshot.title
          && (!snapshot.graphBody || JSON.stringify(latest.graph) === snapshot.graphBody)
        if (mounted.current && isLatest) {
          setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
          if (applyFocusedJob(next)) {
            if (snapshot.graph) setPlan(next.graph)
            setDraftTitle(next.title)
          }
          if (!pendingSave.current) setSaveState('saved')
        }
      }
    })()
    saveInFlight.current = work
    void work.finally(() => {
      if (saveInFlight.current === work) saveInFlight.current = null
    }).catch(() => undefined)
    return work
  }, [applyFocusedJob, token])

  const queueLatestAutosave = React.useCallback((announce = true) => {
    const latest = latestDraft.current
    if (!latest || autosaveJobId.current !== latest.jobId) return false
    const graphBody = JSON.stringify(latest.graph)
    const graphChanged = latest.status === 'queued' && graphBody !== lastSavedGraph.current
    const titleChanged = latest.title !== lastSavedTitle.current
    if (!graphChanged && !titleChanged) return false
    pendingSave.current = {
      jobId: latest.jobId,
      title: latest.title,
      ...(graphChanged ? { graph: latest.graph, graphBody } : {}),
    }
    if (announce) setSaveState('saving')
    return true
  }, [])

  const flushAutosave = React.useCallback(async () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    queueLatestAutosave()
    while (pendingSave.current || saveInFlight.current) {
      if (pendingSave.current) await drainAutosave()
      else if (saveInFlight.current) await saveInFlight.current
    }
  }, [drainAutosave, queueLatestAutosave])

  const retryAutosave = React.useCallback(async () => {
    setSaveState('saving')
    setError('')
    queueLatestAutosave()
    try {
      await drainAutosave()
    } catch {
      // drainAutosave owns the actionable error and preserves the pending snapshot.
    }
  }, [drainAutosave, queueLatestAutosave])

  React.useEffect(() => {
    mounted.current = true
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      mounted.current = false
      queueLatestAutosave(false)
      void drainAutosave().catch(() => undefined)
      listLoadSeq.current += 1
      jobLoadSeq.current += 1
      draftSeq.current += 1
    }
  }, [drainAutosave, queueLatestAutosave])

  React.useEffect(() => {
    if (!job || !plan || autosaveJobId.current !== job.id) return
    if (!queueLatestAutosave()) {
      if (!saveInFlight.current && !pendingSave.current) setSaveState('saved')
      return
    }
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = undefined
      void drainAutosave().catch(() => undefined)
    }, AUTOSAVE_DELAY_MS)
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
    }
  }, [job?.id, job?.status, plan, draftTitle, drainAutosave, queueLatestAutosave])

  const refreshList = React.useCallback(async () => {
    const seq = ++listLoadSeq.current
    try {
      const [jobResponse, templateResponse, scheduleRows] = await Promise.all([
        listGraphJobs(token, activeProject?.slug),
        listGraphTemplates(token, activeProject?.slug, true),
        listSchedules(token).catch(() => [] as Schedule[]),
      ])
      if (mounted.current && seq === listLoadSeq.current) {
        setJobs(jobResponse.items)
        setTemplates(templateResponse.items)
        setSchedules(scheduleRows)
        setScheduleCronByWorkflow(cronLabelsByWorkflow(scheduleRows, cronHint))
      }
    } catch (cause) {
      if (mounted.current && seq === listLoadSeq.current) setError(String(cause))
    }
  }, [token, activeProject?.slug])

  const loadJob = React.useCallback(async (jobId: number, options?: { background?: boolean }) => {
    const background = options?.background === true
    // Background refreshes must not bump jobLoadSeq: a live poll for a prior job
    // would otherwise cancel View / Edit / pending / Run-now navigation loads.
    let seq: number
    if (background) {
      if (wantedJobIdRef.current !== jobId) return null
      seq = jobLoadSeq.current
    } else {
      wantedJobIdRef.current = jobId
      seq = ++jobLoadSeq.current
    }
    try {
      const next = await getGraphJob(token, jobId)
      if (!mounted.current || seq !== jobLoadSeq.current) return null
      if (wantedJobIdRef.current !== jobId) return null
      const latest = latestDraft.current
      const titleHasLocalEdit = autosaveJobId.current === next.id
        && latest?.jobId === next.id
        && latest.title !== lastSavedTitle.current
      const graphHasLocalEdit = autosaveJobId.current === next.id
        && latest?.jobId === next.id
        && JSON.stringify(latest.graph) !== lastSavedGraph.current
      if (
        autosaveJobId.current === next.id
        && next.status === 'queued'
        && (titleHasLocalEdit || graphHasLocalEdit || pendingSave.current || saveInFlight.current)
      ) {
        // Polling may refresh a clean draft, but it must never overwrite or bless
        // local state whose PATCH has not been accepted.
        return
      }
      if (autosaveJobId.current === next.id && ['running', 'review'].includes(next.status)) {
        // Live execution polling refreshes node state, but an in-progress inline
        // rename still belongs to the owner. Do not let a GET erase it before its
        // debounced title-only PATCH lands.
        lastSavedGraph.current = JSON.stringify(next.graph)
        if (!titleHasLocalEdit) {
          lastSavedTitle.current = next.title
          setDraftTitle(next.title)
        }
      } else {
        const primed = await primeAutosave(next, readDraftMeta(next.id), {
          seq,
          requireWantedId: jobId,
        })
        if (!primed) return null
      }
      if (!applyFocusedJob(next)) return null
      setPlan(next.graph)
      setOpeningJobId(current => (current === next.id ? null : current))
      setJobs(current => {
        const idx = current.findIndex(item => item.id === next.id)
        if (idx < 0) return [next, ...current]
        if (current[idx] === next) return current
        const copy = current.slice()
        copy[idx] = next
        return copy
      })
      // Open on the graph, not on a node nobody asked about. Keeps the live poll
      // from clearing a selection, but drops one whose node is gone.
      setSelectedId(current => current && next.graph.nodes.some(node => node.id === current) ? current : null)
      if (chatJobRef.current !== next.id) {
        chatJobRef.current = next.id
        // Restore Plan Chat preference for this plan (not a global toggle).
        setChatOpen(readChatOpen(next.id) ?? false)
        // Active authoring run on this job's session → force the panel open so
        // leave/reopen mid-generate does not look like a broken empty editor.
        if (next.session_id && next.status === 'queued') {
          void activeRuns(token).then(r => {
            if (!mounted.current || seq !== jobLoadSeq.current || chatJobRef.current !== next.id) return
            if (r.session_ids.includes(next.session_id)) setChatOpen(true)
          }).catch(() => { /* optional signal */ })
        }
      }
      return next
    } catch (cause) {
      if (!background && mounted.current && seq === jobLoadSeq.current && wantedJobIdRef.current === jobId) {
        setError(String(cause))
        wantedJobIdRef.current = focusedJobIdRef.current
      }
      return null
    }
  }, [applyFocusedJob, token])

  // Align shell project with the open plan so tools/mentions match ownership;
  // never the reverse (shell must not retarget this plan).
  React.useEffect(() => {
    if (!job?.project_slug) return
    const owned = projects.find(item => item.slug === job.project_slug)
    if (owned && owned.slug !== activeProject?.slug) onActiveProject?.(owned)
  }, [job?.id, job?.project_slug, projects, activeProject?.slug, onActiveProject])

  // Persist the panel toggle per job so Workflows → leave → same plan keeps chat open.
  React.useEffect(() => {
    if (job?.id != null) writeChatOpen(job.id, chatOpen)
  }, [job?.id, chatOpen])

  React.useEffect(() => {
    if (!job || job.status !== 'queued') return
    try {
      localStorage.setItem(`${DRAFT_META_KEY_PREFIX}${job.id}`, JSON.stringify(draftMeta))
    } catch {
      // Storage is a convenience for optional pre-promotion metadata. The graph and
      // title still save through the server when browser storage is unavailable.
    }
  }, [job?.id, job?.status, draftMeta])

  React.useEffect(
    () => {
      if (stage !== 'editor') {
        onStageChange?.(stage, null)
        return
      }
      // Prefer the requested open target over any keep-alive job still in state.
      onStageChange?.(stage, openingJobId ?? job?.id ?? null)
    },
    [stage, openingJobId, job?.id, onStageChange],
  )
  React.useEffect(() => {
    if (!selectedId) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      setSelectedId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId])
  // The back control lives in the shell's tab row; it pokes this nonce.
  const lastBack = React.useRef(backNonce ?? 0)
  React.useEffect(() => {
    if (backNonce == null || backNonce === lastBack.current) return
    lastBack.current = backNonce
    setOpeningJobId(null)
    setStage('home')
    setNotice('')
    void refreshList()
  }, [backNonce, refreshList])

  const jobIdRef = React.useRef<number | null>(null)
  jobIdRef.current = job?.id ?? null

  const openJob = React.useCallback(async (jobId: number) => {
    setOpeningJobId(jobId)
    // Drop a mismatched keep-alive job so loading never displays or reports it.
    // Read via ref so this callback stays stable when job loads - otherwise the
    // pendingJobId effect re-fires, re-GETs, and can clear the editor mid-edit.
    if (jobIdRef.current !== jobId) {
      focusJob(null)
      setPlan(null)
      setSelectedId(null)
    }
    const selected = await loadJob(jobId)
    if (!selected || selected.id !== jobId) {
      setOpeningJobId(current => (current === jobId ? null : current))
      return null
    }
    setStage('editor')
    onStageChange?.('editor', jobId)
    return selected
  }, [loadJob, onStageChange])

  React.useEffect(() => { void refreshList() }, [refreshList])

  React.useEffect(() => {
    // Opening is idempotent (GET + set state). Do not once-claim at module scope:
    // Strict Mode discards the first loadJob via jobLoadSeq, so the re-run must open again.
    if (!pendingJobId) return
    void openJob(pendingJobId)
    onPendingConsumed?.()
  }, [pendingJobId, openJob, onPendingConsumed])

  React.useEffect(() => {
    if (!pendingDraft) return
    const draft = pendingDraft
    const seq = ++draftSeq.current
    setBusy('create')
    setError('')
    // One POST per draft object (Strict Mode remounts reuse the promise). Parent
    // draft prop is cleared only after this instance applies the created job so a
    // remount can still attach and open the editor.
    void getOrStartDraftCreate(draft, () => createGraphJob(token, {
      title: draft.name,
      graph: draft.graph,
      project_slug: activeProject?.slug,
      profile_id: profileId,
    })).then(async created => {
      if (!mounted.current || seq !== draftSeq.current) return
      onDraftConsumed?.()
      const primed = await primeAutosave(created, {
        name: draft.name,
        description: draft.description,
        category: draft.category,
      })
      if (!primed || seq !== draftSeq.current) return
      setOpeningJobId(null)
      setStage('editor')
      focusJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      chatJobRef.current = created.id
      // Drafts arrive from the architect; open Plan Chat so the owner can refine.
      setChatOpen(true)
      writeChatOpen(created.id, true)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('Architect draft ready. Review or edit the frozen plan before starting.')
    }).catch(cause => {
      if (mounted.current && seq === draftSeq.current) {
        onDraftConsumed?.()
        setError(String(cause))
      }
    }).finally(() => {
      if (mounted.current && seq === draftSeq.current) setBusy(null)
    })
  }, [pendingDraft, token, activeProject?.slug, profileId, onDraftConsumed])

  // Every external Task mutation publishes one durable job.update event to
  // this Task's session. Running/review polling remains a liveness fallback.
  useEventStream(token, job?.session_id ?? null, event => {
    if (
      event.type === 'job.update'
      && job
      && Number(event.payload?.job_id ?? job.id) === job.id
    ) {
      void loadJob(job.id)
      if (stage === 'home') void refreshList()
    }
  })
  usePolling(
    async () => {
      if (job) await loadJob(job.id, { background: true })
    },
    1500,
    {
      // Home keeps the last job mounted for return trips, but must not keep
      // polling it - those GETs share cancellation with explicit opens.
      enabled: stage === 'editor' && !!job && ['running', 'review'].includes(job.status),
      immediate: false,
    },
  )
  usePolling(
    () => refreshList(),
    2500,
    { enabled: stage === 'home', immediate: false },
  )

  const definition = plan?.nodes.find(node => node.id === selectedId)
  const selectedState = job && selectedId ? stateFor(job, selectedId) : undefined
  React.useEffect(() => { setOutputEdit(outputText(selectedState)) }, [selectedState?.id, selectedState?.version])
  React.useEffect(() => { setAnswerText('') }, [selectedState?.id])

  function updateSelected(patch: Partial<GraphNodeDefinition>) {
    if (!definition || !plan) return
    setPlan({ ...plan, nodes: plan.nodes.map(node => node.id === definition.id ? { ...node, ...patch } : node) })
  }

  function toggleDependency(dependencyId: string) {
    if (!definition || !plan) return
    const exists = plan.edges.some(edge => edge.from === dependencyId && edge.to === definition.id)
    if (exists) {
      disconnect(dependencyId, definition.id)
      return
    }
    connect(dependencyId, definition.id)
  }

  function connect(from: string, to: string) {
    if (!plan) return
    if (plan.edges.some(edge => edge.from === from && edge.to === to)) return
    if (wouldCycle(plan, from, to)) {
      setError('That connection would make the plan loop back on itself.')
      return
    }
    setError('')
    setPlan({ ...plan, edges: [...plan.edges, { from, to }] })
  }

  function disconnect(from: string, to: string) {
    if (!plan) return
    setPlan({
      ...plan,
      edges: plan.edges.filter(edge => !(edge.from === from && edge.to === to)),
    })
  }

  /** Fold an agent-authored graph into the plan on screen — never the database: the plan
   *  is on screen, so a background write would leave it stale and let the next Save undo
   *  the agent's work. Hand-placed positions survive by node id, so a redraw does not
   *  scatter the canvas the owner already arranged. */
  function applyGraphPatch(next: WorkflowGraph) {
    setPlan(current => {
      const placed = new Map((current?.nodes ?? []).map(node => [node.id, node]))
      return {
        ...next,
        nodes: next.nodes.map(node => {
          const previous = placed.get(node.id)
          return previous && typeof previous.x === 'number'
            ? { ...node, x: previous.x, y: previous.y }
            : node
        }),
      }
    })
    setSelectedId(current => current && next.nodes.some(node => node.id === current) ? current : null)
  }

  function moveNode(nodeId: string, x: number, y: number) {
    setPlan(current => current && {
      ...current,
      nodes: current.nodes.map(node => node.id === nodeId ? { ...node, x, y } : node),
    })
  }

  /** Drop a new node clear of the ones already placed, so it never lands hidden. */
  function freeSlot(current: WorkflowGraph): { x: number; y: number } {
    const placed = layoutGraph(current).nodes
    const right = Math.max(0, ...placed.map(node => node.x + node.width))
    const top = Math.min(...placed.map(node => node.y))
    return { x: right + 110, y: Number.isFinite(top) ? top : 40 }
  }

  function addNode() {
    if (!plan) return
    let index = plan.nodes.length + 1
    while (plan.nodes.some(node => node.id === `node-${index}`)) index += 1
    const node: GraphNodeDefinition = {
      id: `node-${index}`,
      type: 'agent',
      name: `Node ${index}`,
      instruction: '',
      output_kind: 'text',
      ...freeSlot(plan),
    }
    setPlan({ ...plan, nodes: [...plan.nodes, node] })
    setSelectedId(node.id)
  }

  const mentionSlug = job?.project_slug ?? activeProject?.slug ?? undefined
  React.useEffect(() => {
    if (!mentionSlug || areasBySlug[mentionSlug]) return
    let live = true
    listProjectAreas(token, mentionSlug)
      .then(areas => { if (live && mounted.current) setAreasBySlug(current => ({ ...current, [mentionSlug]: areas.code_areas.map(area => area.rel_path) })) })
      .catch(() => { /* areas are an enhancement — the selector still offers Ops */ })
    return () => { live = false }
  }, [token, mentionSlug, areasBySlug])
  const codeAreas = (mentionSlug ? areasBySlug[mentionSlug] : undefined) ?? []
  const mentionItems = useProjectMentionItems(token, mentionSlug)

  React.useEffect(() => {
    if (!pendingTest || !chatOpen || !plan) return
    const index = plan.nodes.findIndex(node => node.id === pendingTest)
    if (index < 0) { setPendingTest(null); return }
    let cancelled = false
    let frames = 0
    // The authoring chat mounts with chatOpen; retry a few frames until its
    // imperative handle exists so Test in chat is not dropped on first paint.
    const tryRun = () => {
      if (cancelled) return
      if (!chatRef.current) {
        if (frames++ < 30) requestAnimationFrame(tryRun)
        else setPendingTest(null)
        return
      }
      chatRef.current.runThrough(index, plan.nodes[index].name || pendingTest)
      setPendingTest(null)
    }
    tryRun()
    return () => { cancelled = true }
  }, [pendingTest, chatOpen, plan])

  async function deletePlan(item: { id: number; title: string }) {
    const ok = await confirmDialog({
      title: 'Delete this plan?',
      message: `“${item.title}” and its run threads will be permanently deleted. Anything it already produced (artifacts, project files) stays.`,
      confirmLabel: 'Delete plan',
      danger: true,
    })
    if (!ok || busy) return
    setBusy('delete')
    setError('')
    try {
      await deleteGraphJob(token, item.id)
      if (!mounted.current) return
      setJobs(current => current.filter(row => row.id !== item.id))
      if (job?.id === item.id) { focusJob(null); setPlan(null); setSelectedId(null); setChatOpen(false) }
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  // Pause ⇄ resume: the owner's rule is that only active templates run on a schedule,
  // so "this workflow needs fixing" is one click out of rotation, not a deletion.
  async function toggleTemplatePaused(template: GraphTemplate) {
    if (busy) return
    setBusy('template-status')
    setError('')
    try {
      const next = await setGraphTemplateStatus(token, template.id, template.status === 'active' ? 'draft' : 'active')
      if (mounted.current) setTemplates(current => current.map(row => row.id === template.id ? { ...row, status: next.status } : row))
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function archiveTemplate(template: GraphTemplate) {
    const ok = await confirmDialog({
      title: 'Archive this workflow?',
      message: `“${template.name}” will leave the active library and its schedules will stop. Its project ownership and past runs stay intact, and you can restore it later.`,
      confirmLabel: 'Archive workflow',
    })
    if (!ok || busy) return
    setBusy('template-status')
    setError('')
    try {
      const next = await setGraphTemplateStatus(token, template.id, 'archived')
      if (!mounted.current) return
      setTemplates(current => current.map(row => row.id === template.id ? { ...row, status: next.status } : row))
      setNotice(`Archived “${template.name}”.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function restoreTemplate(template: GraphTemplate) {
    if (busy) return
    setBusy('template-status')
    setError('')
    try {
      const next = await setGraphTemplateStatus(token, template.id, 'active')
      if (!mounted.current) return
      setTemplates(current => current.map(row => row.id === template.id ? { ...row, status: next.status } : row))
      setNotice(`Restored “${template.name}” to Workflows.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function deleteTemplate(template: GraphTemplate) {
    const ok = await confirmDialog({
      title: 'Delete this workflow permanently?',
      message: `“${template.name}” will be permanently deleted, along with any schedules that run it. Past runs keep their frozen copy of the graph.`,
      confirmLabel: 'Delete workflow',
      danger: true,
    })
    if (!ok || busy) return
    setBusy('delete')
    setError('')
    try {
      await deleteGraphTemplate(token, template.id)
      if (mounted.current) setTemplates(current => current.filter(row => row.id !== template.id))
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function duplicatePlan() {
    if (!job || busy) return
    setBusy('duplicate')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: job.title,
        // The frozen snapshot, positions included — the copy starts as exactly what
        // ran, which is the whole point of revising rather than rebuilding.
        graph: job.graph,
        input: job.input,
        project_slug: resolveOwnedProjectSlug(job, activeProject?.slug) ?? undefined,
        profile_id: profileId,
      })
      if (!mounted.current) return
      if (!await primeAutosave(created, { ...draftMeta, name: draftTitle })) return
      focusJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice('Editable copy created — the original stays as the run record.')
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  // The blank-plan entry point. Sequential's "New workflow" retired with it, and chat
  // promotion cannot be the only door into the editor — a starter trigger + first step
  // gives the canvas (or the authoring chat) something to build on.
  async function newPlan(description?: string) {
    if (busy) return
    setBusy('create')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: 'Untitled plan',
        graph: {
          nodes: [
            { id: 'trigger', type: 'trigger', trigger_kind: 'manual', name: 'When I run it', instruction: '', output_kind: 'json', inputs: [] },
            { id: 'step-1', type: 'agent', name: 'Step 1', instruction: '', output_kind: 'text' },
          ],
          edges: [{ from: 'trigger', to: 'step-1' }],
        },
        project_slug: activeProject?.slug,
        profile_id: profileId,
      })
      if (!mounted.current) return
      if (!await primeAutosave(created, {})) return
      focusJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      chatJobRef.current = created.id
      setChatOpen(true)
      writeChatOpen(created.id, true)
      setOpeningJobId(null)
      setStage('editor')
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      if (description?.trim()) setInitialAuthorText(description.trim())
      else setNotice('New plan. Describe it in the chat, or build it on the canvas.')
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  function addScriptNode() {
    if (!plan) return
    let index = plan.nodes.length + 1
    while (plan.nodes.some(node => node.id === `script-${index}`)) index += 1
    const node: GraphNodeDefinition = {
      id: `script-${index}`,
      type: 'script',
      name: 'Run a script',
      instruction: '',
      command: '',
      output_kind: 'text',
      ...freeSlot(plan),
    }
    setPlan({ ...plan, nodes: [...plan.nodes, node] })
    setSelectedId(node.id)
  }

  function addTrigger() {
    if (!plan || plan.nodes.some(node => node.type === 'trigger')) return
    // Deliberately no x/y: a trigger has no dependencies, so the auto-layout puts
    // it in the first column, and connecting it shifts the rest along. Pinning it
    // by hand instead would leave a gap the moment the auto-placed nodes moved.
    const node: GraphNodeDefinition = {
      id: 'trigger',
      type: 'trigger',
      trigger_kind: 'manual',
      name: 'When I run it',
      instruction: '',
      output_kind: 'json',
      inputs: [],
    }
    setPlan({ ...plan, nodes: [node, ...plan.nodes] })
    setSelectedId(node.id)
  }

  function removeNode() {
    if (!definition || !plan || plan.nodes.length <= 1) return
    const nodes = plan.nodes.filter(node => node.id !== definition.id)
    setPlan({
      nodes,
      edges: plan.edges.filter(edge => edge.from !== definition.id && edge.to !== definition.id),
    })
    setSelectedId(nodes[0]?.id ?? null)
  }

  async function act(label: string, action: () => Promise<GraphJob>, message?: string) {
    if (busy) return
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await flushAutosave()
      const next = await action()
      if (!mounted.current) return
      if (wantedJobIdRef.current !== next.id) return
      if (!await primeAutosave(next, readDraftMeta(next.id), { requireWantedId: next.id })) return
      if (!applyFocusedJob(next)) return
      setPlan(next.graph)
      setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
      if (message) setNotice(message)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
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

  async function saveTemplate(meta: { name: string; description: string; category: string }) {
    if (!job || busy) return
    setBusy('save-template')
    setError('')
    try {
      await flushAutosave()
      const template = await saveGraphTemplate(token, job.id, meta)
      if (mounted.current) {
        setSavingTemplate(false)
        setJob(current => {
          if (current?.id !== job.id || wantedJobIdRef.current !== job.id) return current
          return { ...current, workflow_id: template.id }
        })
        setJobs(current => current.map(item => item.id === job.id ? { ...item, workflow_id: template.id } : item))
        setTemplates(current => [template, ...current.filter(item => item.id !== template.id)])
        setNotice(`Saved reusable workflow “${template.name}”.`)
      }
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  function promotePlan() {
    if (!job || job.workflow_id || busy) return
    void saveTemplate({
      name: normalizedPlanTitle(draftTitle),
      description: draftMeta.description?.trim() ?? '',
      category: draftMeta.category?.trim() || 'other',
    })
  }

  async function runCurrentPlan() {
    if (!job || busy) return
    setBusy('start')
    setError('')
    setNotice('')
    try {
      await flushAutosave()
      const next = await startGraphJob(token, job.id)
      if (!mounted.current) return
      if (wantedJobIdRef.current !== next.id) return
      if (!await primeAutosave(next, readDraftMeta(next.id), { requireWantedId: next.id })) return
      if (!applyFocusedJob(next)) return
      setPlan(next.graph)
      setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
      setNotice('Execution started.')
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function createFromTemplate(template: GraphTemplate, input?: Record<string, string>) {
    if (busy) return
    setBusy('use-template')
    setError('')
    try {
      const existingId = runTarget?.kind === 'template'
        && runTarget.template.id === template.id
        ? runTarget.createdJobId
        : undefined
      let jobId = existingId
      if (jobId == null) {
        const created = await createGraphJob(token, {
          title: template.name,
          graph: template.graph,
          workflow_id: template.id,
          // Template ownership wins — shell active project is only a fallback for
          // legacy rows that never stored a project (1 workflow = 1 project).
          project_slug: resolveOwnedProjectSlug(template, activeProject?.slug) ?? undefined,
          profile_id: profileId,
        })
        if (!mounted.current) return
        jobId = created.id
        setRunTarget(current => (
          current?.kind === 'template' && current.template.id === template.id
            ? { ...current, createdJobId: created.id }
            : current
        ))
        setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      }
      const next = await startGraphJob(token, jobId, input)
      if (!mounted.current) return
      if (!await primeAutosave(next, {
        name: template.name,
        description: template.description,
        category: template.category,
      }, { requireWantedId: next.id })) return
      setRunTarget(null)
      setOpeningJobId(null)
      setStage('editor')
      focusJob(next)
      setPlan(next.graph)
      setSelectedId(null)
      setJobs(current => [next, ...current.filter(item => item.id !== next.id)])
      setNotice(`Execution started from “${template.name}”.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
      throw cause
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function editTemplate(template: GraphTemplate) {
    if (busy) return
    setBusy('edit-template')
    setError('')
    try {
      const created = await createGraphJob(token, {
        title: template.name,
        graph: template.graph,
        workflow_id: template.id,
        project_slug: resolveOwnedProjectSlug(template, activeProject?.slug) ?? undefined,
        profile_id: profileId,
      })
      if (!mounted.current) return
      if (!await primeAutosave(created, {
        name: template.name,
        description: template.description,
        category: template.category,
      })) return
      setOpeningJobId(null)
      setStage('editor')
      focusJob(created)
      setPlan(created.graph)
      setSelectedId(null)
      setJobs(current => [created, ...current.filter(item => item.id !== created.id)])
      setNotice(`Editable draft created from “${template.name}”.`)
    } catch (cause) {
      if (mounted.current) setError(String(cause))
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function runDraft(item: GraphJob, input?: Record<string, string>) {
    if (busy) return
    setBusy('start')
    setError('')
    const restoreWantedIfCurrent = (seq: number, jobId: number) => {
      if (
        mounted.current
        && seq === jobLoadSeq.current
        && wantedJobIdRef.current === jobId
      ) {
        wantedJobIdRef.current = focusedJobIdRef.current
      }
    }
    wantedJobIdRef.current = item.id
    const seq = ++jobLoadSeq.current
    try {
      await flushAutosave()
      if (!mounted.current || seq !== jobLoadSeq.current || wantedJobIdRef.current !== item.id) {
        restoreWantedIfCurrent(seq, item.id)
        return
      }
      const next = await startGraphJob(token, item.id, input)
      if (!mounted.current || seq !== jobLoadSeq.current || wantedJobIdRef.current !== next.id) {
        restoreWantedIfCurrent(seq, item.id)
        return
      }
      const primed = await primeAutosave(next, readDraftMeta(next.id), {
        seq,
        requireWantedId: next.id,
      })
      if (!primed || !applyFocusedJob(next)) {
        restoreWantedIfCurrent(seq, next.id)
        return
      }
      setOpeningJobId(null)
      setStage('editor')
      setPlan(next.graph)
      setSelectedId(null)
      setJobs(current => current.map(row => row.id === next.id ? next : row))
      setRunTarget(null)
      setNotice('Execution started.')
    } catch (cause) {
      restoreWantedIfCurrent(seq, item.id)
      if (mounted.current) setError(String(cause))
      throw cause
    } finally {
      if (mounted.current) setBusy(null)
    }
  }

  async function prepareDraftTemplate(item: GraphJob) {
    if (!await primeAutosave(item)) return
    focusJob(item)
    setPlan(item.graph)
    setSavingTemplate(true)
  }

  const allDone = !!job?.node_states.length && job.node_states.every(state => state.status === 'done')
  // Mirror ChangesReview: final approve with an empty diff is "accept & close",
  // not "merge changes" (agent finished with no file edits).
  const [emptyReviewDiff, setEmptyReviewDiff] = React.useState(false)
  React.useEffect(() => {
    if (!job || job.status !== 'review' || !job.worktree || !allDone) {
      setEmptyReviewDiff(false)
      return
    }
    let cancelled = false
    getJobDiff(token, job.id)
      .then(body => { if (!cancelled) setEmptyReviewDiff(body.files.length === 0) })
      .catch(() => { if (!cancelled) setEmptyReviewDiff(false) })
    return () => { cancelled = true }
  }, [token, job?.id, job?.status, job?.worktree?.status, allDone])

  const doneCount = job?.node_states.filter(state => state.status === 'done').length ?? 0
  const hasLocalGraphEdit = !!job && job.status === 'queued' && !!plan && (
    JSON.stringify(plan) !== lastSavedGraph.current
    || normalizedPlanTitle(draftTitle) !== lastSavedTitle.current
  )
  const intakeEditBlocked = intakeEditState.dirty || !intakeEditState.valid
  const effectiveSaveState = intakeEditBlocked || saveState === 'error'
    ? 'error'
    : hasLocalGraphEdit || saveState === 'saving' || !!pendingSave.current || !!saveInFlight.current
      ? 'saving'
      : 'saved'
  const runBlocked = effectiveSaveState !== 'saved'
  React.useEffect(() => {
    setIntakeEditState({ dirty: false, valid: true })
  }, [job?.id, selectedId])
  const archivedTemplates = templates.filter(item => item.status === 'archived')
  const activeTemplates = templates.filter(item => item.status !== 'archived')

  if (stage === 'home') {
    const drafts = jobs.filter(item => item.status === 'queued')
    const runs = jobs.filter(item => item.status !== 'queued')
    const query = homeQueries[homeTab].trim().toLowerCase()
    const matches = (...values: Array<string | null | undefined>) =>
      !query || values.some(value => value?.toLowerCase().includes(query))
    const visibleDrafts = drafts.filter(item => matches(item.title))
    const visibleRuns = runs.filter(item => matches(item.title, projectRun(item).status))
    const searchedTemplates = (showArchived ? archivedTemplates : activeTemplates)
      .filter(item => matches(item.name, item.description, item.category, ...(scheduleCronByWorkflow.get(item.id) || [])))
    const searchPlaceholder = `Search ${homeTab}`
    const setTab = (tab: WorkflowHomeTab) => {
      setHomeTab(tab)
      if (tab !== 'workflows') setShowArchived(false)
    }
    const runTemplate = (template: GraphTemplate) => {
      const inputs = triggerInputs(template.graph)
      setRunTarget({
        kind: 'template',
        template: { ...template, inputs: inputs.length ? inputs : template.inputs },
      })
    }
    const category = (template: GraphTemplate) => template.category?.trim() || 'other'
    const scheduleSummary = (template: GraphTemplate) => {
      const rows = schedules.filter(schedule => schedule.workflow_id === template.id)
      if (rows.length === 0) return 'No schedules'
      const needsBinding = rows.filter(schedule => schedule.ready === false).length
      const on = rows.filter(schedule => schedule.enabled && schedule.ready !== false).length
      const off = rows.length - on - needsBinding
      const parts: string[] = []
      if (on) parts.push(`${on} schedule${on === 1 ? '' : 's'} on`)
      if (off) parts.push(`${off} schedule${off === 1 ? '' : 's'} off`)
      if (needsBinding) parts.push(`${needsBinding} need${needsBinding === 1 ? 's' : ''} binding${needsBinding === 1 ? '' : 's'}`)
      return parts.join(' · ')
    }
    const workflowRow = (template: GraphTemplate) => <div className="workflow-home-row workflow-home-workflow-row" role="row" key={template.id}>
      <div className="workflow-home-name" role="cell" data-label="Workflow">
        <strong>{template.name}</strong>
      </div>
      <div role="cell" data-label="Category"><span className="workflow-home-chip workflow-home-category">#{category(template)}</span></div>
      <div role="cell" data-label="Availability">
        <span className={`workflow-home-chip${template.status === 'active' ? ' workflow-home-available' : ' workflow-home-paused'}`}>
          {template.status === 'active' ? 'Available' : 'Paused'}
        </span>
      </div>
      <div role="cell" data-label="Automation"><span className="workflow-home-chip workflow-home-trigger">{scheduleSummary(template)}</span></div>
      <div className="workflow-home-actions" role="cell" data-label="Actions">
        <button className="ghost-button" disabled={!!busy} onClick={() => void editTemplate(template)}>Edit</button>
        <button className="primary-button" disabled={!!busy} onClick={() => runTemplate(template)}>Run</button>
        <button className="ghost-button" disabled={!!busy} onClick={() => setSchedulingTemplate(template)}>Schedules</button>
        <button
          className="row-action"
          title={template.status === 'active' ? 'Pause workflow availability' : 'Resume workflow availability'}
          aria-label={`${template.status === 'active' ? 'Pause' : 'Resume'} ${template.name}`}
          disabled={!!busy}
          onClick={() => void toggleTemplatePaused(template)}
        >{template.status === 'active' ? '⏸' : '▶'}</button>
        <button
          className="row-action"
          title="Archive workflow"
          aria-label={`Archive ${template.name}`}
          disabled={!!busy}
          onClick={() => void archiveTemplate(template)}
        ><IconArtifacts size={13} /></button>
      </div>
    </div>
    const workflowTable = (rows: GraphTemplate[]) => <div className="workflow-home-table" role="table" aria-label="Reusable workflows">
      <div className="workflow-home-row workflow-home-table-head workflow-home-workflow-row" role="row">
        <div role="columnheader">Workflow</div>
        <div role="columnheader">Category</div>
        <div role="columnheader">Availability</div>
        <div role="columnheader">Automation</div>
        <div className="workflow-home-actions-head" role="columnheader">Actions</div>
      </div>
      {rows.map(template => workflowRow(template))}
    </div>
    const emptySearch = query ? 'No matching items.' : null

    return <section className="graph-screen graph-home">
      <header className="graph-header workflow-home-toolbar">
        <h1 className="sr-only">Workflows</h1>
        <div className="workflow-home-tabs" role="tablist" aria-label="Workflow library">
          {([
            ['drafts', 'Drafts', drafts.length],
            ['workflows', 'Workflows', activeTemplates.length],
            ['runs', 'Runs', runs.length],
          ] as const).map(([tab, label, count]) => <button
            key={tab}
            className={homeTab === tab ? 'active' : ''}
            role="tab"
            aria-selected={homeTab === tab}
            onClick={() => setTab(tab)}
          >{label} <span>{count}</span></button>)}
        </div>
        <div className="workflow-home-toolbar-actions">
          <label className="workflow-home-search">
            <span className="sr-only">{searchPlaceholder}</span>
            <IconSearch size={14} />
            <input
              type="search"
              value={homeQueries[homeTab]}
              placeholder={searchPlaceholder}
              onChange={event => setHomeQueries(current => ({ ...current, [homeTab]: event.target.value }))}
            />
          </label>
          {homeTab === 'workflows' && (
          <button
            className={`ghost-button${showArchived ? ' active' : ''}`}
            aria-label={showArchived ? 'View active workflows' : 'View archived workflows'}
            aria-pressed={showArchived}
            onClick={() => setShowArchived(value => !value)}
          >
            <IconArtifacts size={14} />
            {showArchived ? 'Active workflows' : `Archived (${archivedTemplates.length})`}
          </button>
          )}
          {homeTab !== 'runs' && <button className="primary-button workflow-home-new" disabled={!!busy} onClick={() => void newPlan()}>
            <IconPlus size={14} /> New
          </button>}
        </div>
      </header>
      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="graph-notice">{notice}</div>}

      <div className="workflow-home-scroll">
        <div className="workflow-home-content">
          {homeTab === 'drafts' && (
            visibleDrafts.length === 0
              ? <p className="workflow-home-empty muted">{emptySearch || 'No draft plans yet. Create one to start building.'}</p>
              : <div className="workflow-home-table" role="table" aria-label="Draft plans">
                  <div className="workflow-home-row workflow-home-table-head workflow-home-draft-row" role="row">
                    <div role="columnheader">Draft plan</div>
                    <div role="columnheader">Status</div>
                    <div className="workflow-home-actions-head" role="columnheader">Actions</div>
                  </div>
                  {visibleDrafts.map(item => {
                    const isOpenDraft = job?.id === item.id
                    const draftSnapshot = isOpenDraft
                      ? { ...job, graph: plan ?? job.graph }
                      : item
                    const draftRunBlocked = isOpenDraft && runBlocked
                    return <div className="workflow-home-row workflow-home-draft-row" role="row" key={item.id}>
                    <div className="workflow-home-name" role="cell" data-label="Draft plan"><strong>{item.title || 'Untitled plan'}</strong></div>
                    <div role="cell" data-label="Status"><span className="workflow-home-chip workflow-home-draft-chip">Draft</span></div>
                    <div className="workflow-home-actions" role="cell" data-label="Actions">
                      <button className="ghost-button" disabled={!!busy} onClick={() => void openJob(item.id)}>Edit</button>
                      <button
                        className="primary-button"
                        disabled={!!busy || draftRunBlocked}
                        title={draftRunBlocked ? 'Wait for a valid saved workflow before running' : undefined}
                        onClick={() => setRunTarget({ kind: 'job', job: draftSnapshot })}
                      >Run</button>
                      <button className="ghost-button workflow-home-star" disabled={!!busy} onClick={() => void prepareDraftTemplate(item)}>★ Save as template</button>
                      <button className="row-action danger" title="Delete draft" aria-label={`Delete ${item.title}`} disabled={!!busy} onClick={() => void deletePlan(item)}><IconTrash size={13} /></button>
                    </div>
                  </div>
                  })}
                </div>
          )}

          {homeTab === 'workflows' && (
            showArchived
              ? <div className="workflow-home-group">
                  <div className="workflow-home-group-head">
                    <div><strong>Archived workflows</strong><small>Restore one or delete it permanently.</small></div>
                    <span>{searchedTemplates.length}</span>
                  </div>
                  {searchedTemplates.length === 0
                    ? <p className="workflow-home-empty muted">{emptySearch || 'No archived workflows.'}</p>
                    : <div className="workflow-home-table" role="table" aria-label="Archived workflows">
                        <div className="workflow-home-row workflow-home-table-head workflow-home-archive-row" role="row">
                          <div role="columnheader">Workflow</div>
                          <div role="columnheader">Category</div>
                          <div className="workflow-home-actions-head" role="columnheader">Actions</div>
                        </div>
                        {searchedTemplates.map(template => <div className="workflow-home-row workflow-home-archive-row" role="row" key={template.id}>
                          <div className="workflow-home-name" role="cell" data-label="Workflow"><strong>{template.name}</strong></div>
                          <div role="cell" data-label="Category"><span className="workflow-home-chip workflow-home-category">#{category(template)}</span></div>
                          <div className="workflow-home-actions" role="cell" data-label="Actions">
                            <button className="ghost-button" disabled={!!busy} aria-label={`Restore ${template.name}`} onClick={() => void restoreTemplate(template)}>Restore</button>
                            <button className="ghost-button danger" disabled={!!busy} aria-label={`Delete workflow ${template.name} permanently`} onClick={() => void deleteTemplate(template)}>Delete permanently</button>
                          </div>
                        </div>)}
                      </div>}
                </div>
              : <div className="workflow-home-group">
                  <div className="workflow-home-group-head">
                    <div><strong>Reusable workflows</strong><small>Run manually any time. Availability pauses all automation; each schedule has its own On or Off state.</small></div>
                    <span>{searchedTemplates.length}</span>
                  </div>
                  {searchedTemplates.length === 0
                    ? <p className="workflow-home-empty muted">{emptySearch || 'No reusable workflows.'}</p>
                    : workflowTable(searchedTemplates)}
                </div>
          )}

          {homeTab === 'runs' && (
            visibleRuns.length === 0
              ? <p className="workflow-home-empty muted">{emptySearch || 'No workflow runs yet.'}</p>
              : <div className="workflow-home-table" role="table" aria-label="Workflow runs">
                  <div className="workflow-home-row workflow-home-table-head workflow-home-run-row" role="row">
                    <div role="columnheader">Workflow</div>
                    <div role="columnheader">When</div>
                    <div role="columnheader">Status</div>
                    <div role="columnheader">Duration</div>
                    <div className="workflow-home-actions-head" role="columnheader">Actions</div>
                  </div>
                  {visibleRuns.map(item => {
                    const projection = projectRun(item)
                    return <div className="workflow-home-row workflow-home-run-row" role="row" key={item.id}>
                      <div className="workflow-home-name" role="cell" data-label="Workflow"><strong>{item.title}</strong></div>
                      <div className="workflow-home-secondary" role="cell" data-label="When">{formatRunAge(projection, item.created_at || item.updated_at)}</div>
                      <div role="cell" data-label="Status"><span className={`workflow-home-chip workflow-home-status st-${planStatusTone(item)}`}>{planStatusLabel(item)}</span></div>
                      <div className="workflow-home-secondary" role="cell" data-label="Duration">{formatRunDuration(projection)}</div>
                      <div className="workflow-home-actions" role="cell" data-label="Actions">
                        <button className="ghost-button" onClick={() => void openJob(item.id)}>View</button>
                      </div>
                    </div>
                  })}
                </div>
          )}
        </div>
      </div>

      {savingTemplate && job && <SaveTemplateModal
        title={job.title}
        busy={busy === 'save-template'}
        onCancel={() => setSavingTemplate(false)}
        onSave={meta => void saveTemplate(meta)}
      />}
      {runTarget && <RunModal
        title={runTarget.kind === 'template' ? runTarget.template.name : runTarget.job.title}
        inputs={runTarget.kind === 'template'
          ? runTarget.template.inputs
          : triggerInputs(runTarget.job.graph)}
        onCancel={() => setRunTarget(null)}
        onRun={async input => {
          if (runTarget.kind === 'template') await createFromTemplate(runTarget.template, input)
          else await runDraft(runTarget.job, input)
        }}
      />}
      {schedulingTemplate && <div className="modal-scrim" onClick={() => setSchedulingTemplate(null)}>
        <div
          className="modal-card schedule-modal-card"
          role="dialog"
          aria-modal="true"
          aria-label={`Schedule ${schedulingTemplate.name}`}
          onClick={event => event.stopPropagation()}
          onKeyDown={event => { if (event.key === 'Escape') setSchedulingTemplate(null) }}
        >
          <ScheduleManager
            token={token}
            workflows={[schedulingTemplate]}
            workflowId={schedulingTemplate.id}
            compact
            onClose={() => setSchedulingTemplate(null)}
            onChanged={() => void refreshList()}
            onOpenJob={async spawned => {
              if (spawned.engine !== 'graph') {
                throw new Error(`Schedule returned non-graph job ${spawned.id}.`)
              }
              if (
                spawned.project_slug
                && schedulingTemplate.project_slug
                && spawned.project_slug !== schedulingTemplate.project_slug
              ) {
                throw new Error('Schedule returned a job outside the workflow owner project.')
              }
              const selected = await openJob(spawned.id)
              if (!selected || selected.id !== spawned.id) {
                throw new Error(`Could not select spawned graph job ${spawned.id}.`)
              }
              if (selected.project_slug !== schedulingTemplate.project_slug) {
                throw new Error('Selected job does not belong to this workflow project.')
              }
              setSchedulingTemplate(null)
            }}
          />
        </div>
      </div>}
    </section>
  }

  return <section className="graph-screen">
    {/* One bar, not two: the Advanced tab already says where you are, so the
        eyebrow and the never-changing subtitle were spending 91px to repeat it. */}
    <header className="graph-header">
      {job?.status === 'queued' && <button
        className={`ghost-button graph-chat-toggle${chatOpen ? ' active' : ''}`}
        onClick={() => setChatOpen(open => {
          const next = !open
          if (job?.id != null) writeChatOpen(job.id, next)
          return next
        })}
        aria-pressed={chatOpen}
      >Chat</button>}
      {/* Project is locked to the open plan (1 workflow = 1 project). Shell already
          shows the active project - do not dump the display name again here. */}
      {resolveOwnedProjectSlug(job, activeProject?.slug) && (
        <span
          className="graph-project-lock"
          title="Project locked to this plan - switch project only when starting a new workflow"
          aria-label="Project locked to this plan"
        >
          <IconLock size={12} />
        </span>
      )}
      <h1 className="graph-title" aria-label={job ? draftTitle : undefined}>
        {job
          ? renamingTitle
            ? <input
                className="graph-title-input"
                aria-label="Workflow name"
                autoFocus
                maxLength={200}
                value={draftTitle}
                onFocus={event => event.currentTarget.select()}
                onChange={event => setDraftTitle(event.target.value)}
                onBlur={() => {
                  setDraftTitle(current => normalizedPlanTitle(current))
                  setRenamingTitle(false)
                }}
                onKeyDown={event => {
                  if (event.key === 'Enter') event.currentTarget.blur()
                  if (event.key === 'Escape') {
                    setDraftTitle(lastSavedTitle.current || job.title)
                    setRenamingTitle(false)
                  }
                }}
              />
            : <button
                className="graph-title-button"
                onClick={() => setRenamingTitle(true)}
                aria-label={`Rename workflow ${draftTitle}`}
                title="Rename workflow"
              >
                <span>{draftTitle}</span>
                <IconPencil size={13} />
              </button>
          : 'Workflows'}
      </h1>
      {job && <>
        <span className={`graph-status st-${planStatusTone(job)}`} title={job.status !== 'queued' ? 'Structure is frozen after start — use Duplicate to edit' : undefined}>{planStatusLabel(job)}</span>
        <span className="graph-node-count">{doneCount}/{job.node_states.length} nodes</span>
      </>}
      {job?.status === 'queued' && <span
        className={`graph-save-status is-${effectiveSaveState}`}
        role="status"
        aria-live="polite"
      >
        {effectiveSaveState === 'saving' ? 'Saving…' : effectiveSaveState === 'error' ? 'Not saved' : 'Saved ✓'}
      </span>}
      {job?.status === 'queued' && saveState === 'error' && !intakeEditBlocked && <button
        className="ghost-button graph-save-retry"
        onClick={() => void retryAutosave()}
        disabled={!!busy}
      >Retry save</button>}
      <div className="graph-header-actions">
        {job && plan && job.status !== 'queued' && <>
          <button className="ghost-button" onClick={promotePlan} disabled={!!busy || !!job.workflow_id}>
            {job.workflow_id ? '★ Saved as Workflow' : '★ Save as Workflow'}
          </button>
          <button className="ghost-button" onClick={() => void duplicatePlan()} disabled={!!busy}>
            {busy === 'duplicate' ? 'Copying…' : 'Duplicate to edit'}
          </button>
        </>}
        {job?.status === 'review' && allDone && <button className="primary-button" onClick={() => void act(
          'approve-job',
          () => approveGraphJob(token, job.id),
          // Same door as ChangesReview: final approve is the local merge for repo plans
          // (or a quiet close when the agent left no file changes).
          job.worktree
            ? (emptyReviewDiff
              ? `Closed with no file changes on ${job.worktree.base_branch}.`
              : `Changes merged into ${job.worktree.base_branch}.`)
            : 'Final result approved.',
        )} disabled={!!busy}>
          {/* A repo plan's final approve is also the local merge (slice 4) — say so,
              unless the diff is empty (accept & close, matching ChangesReview). */}
          {busy === 'approve-job'
            ? (job.worktree ? (emptyReviewDiff ? 'Closing…' : 'Merging…') : 'Approving…')
            : (job.worktree
              ? (emptyReviewDiff ? 'Accept & close' : 'Approve & merge changes')
              : 'Approve final result')}
        </button>}
      </div>
    </header>

    {error && <div className="error-bar">{error}</div>}
    {notice && <div className="graph-notice">{notice}</div>}
    {job?.status === 'queued' && <details className="graph-workflow-meta">
      <summary>Workflow metadata <span>optional</span></summary>
      <div className="graph-workflow-meta-fields">
        <label>Category<input
          value={draftMeta.category ?? ''}
          placeholder="e.g. content"
          onChange={event => setDraftMeta(current => ({ ...current, category: event.target.value }))}
        /></label>
        <label>Description<textarea
          rows={2}
          value={draftMeta.description ?? ''}
          placeholder="What this workflow does"
          onChange={event => setDraftMeta(current => ({ ...current, description: event.target.value }))}
        /></label>
      </div>
    </details>}
    {/* Durable merge/push outcome so a reopened Done plan still shows where the
        changes landed - the header Approve button disappears after the merge. */}
    {job?.worktree?.status === 'merged' && <p className="changes-note is-merged graph-merge-note">
      {job.worktree.merge_commit && job.worktree.base_commit && job.worktree.merge_commit === job.worktree.base_commit
        ? <>✓ Closed with no file changes on <code>{job.worktree.base_branch}</code>{job.worktree.merge_commit && <> · <code>{job.worktree.merge_commit.slice(0, 7)}</code></>}</>
        : <>✓ Changes merged into <code>{job.worktree.base_branch}</code>{job.worktree.merge_commit && <> · <code>{job.worktree.merge_commit.slice(0, 7)}</code></>}</>}
    </p>}
    {job?.worktree?.status === 'merged' && job.worktree.push_status === 'pushed' && <p className="changes-note is-pushed graph-merge-note">
      ↑ Pushed to <code>{job.worktree.push_remote}</code>
      {job.worktree.push_web_url && <> · <a href={job.worktree.push_web_url} target="_blank" rel="noreferrer">open repo</a></>}
    </p>}
    {job?.worktree?.status === 'merged' && job.worktree.push_status === 'failed' && <div className="changes-push-failed graph-merge-note" role="alert">
      <p><strong>Merged into your project, but pushing to the remote failed.</strong></p>
      {job.worktree.push_error && <pre className="changes-push-error">{job.worktree.push_error}</pre>}
      <p>
        Nothing was undone - the changes are safely in <code>{job.worktree.base_branch}</code>.
        Fix the cause with your own git, then retry from the task Changes panel.
        {job.worktree.push_web_url && <> <a href={job.worktree.push_web_url} target="_blank" rel="noreferrer">Open the repo</a>.</>}
      </p>
    </div>}
    {job && <SatpamCard token={token} jobId={job.id} interventions={job.satpam} jobStatus={job.status} onChanged={() => void loadJob(job.id, { background: true })} />}
    {busy === 'create' && <p className="graph-loading">Materializing architect draft…</p>}

    <div className="graph-workspace" style={{
      ['--graph-chat-width' as string]: `${chatWidth}px`,
      ['--graph-inspector-width' as string]: `${inspectorWidth}px`,
    }}>
      {/* Chat left, artifact right — the house idiom (Design Studio does the same),
          and the standing rule: the agent edits the plan on screen, never the DB. */}
      {chatOpen && job && plan && <aside className="graph-chat-panel">
        <AuthoringChat
          ref={chatRef}
          token={token}
          features={features}
          profiles={profiles}
          activeProfile={activeProfile}
          projectSlug={resolveOwnedProjectSlug(job, activeProject?.slug)}
          // A graph job already owns a chat session — the one it was created with — so
          // the conversation is pinned to the plan without inventing a second thread.
          ensureSession={async () => job.session_id}
          buildPrompt={text => buildGraphPrompt({
            name: job.title,
            description: '',
            category: '',
            graph: plan,
          }, text, codeAreas)}
          applyReply={raw => {
            const patch = parseGraphDraft(raw)
            if (!patch?.graph) return false
            applyGraphPatch(patch.graph)
            if (patch.inputs) {
              setPlan(current => current && ({
                ...current,
                nodes: current.nodes.map(node =>
                  node.type === 'trigger' ? { ...node, inputs: patch.inputs } : node),
              }))
            }
            setDraftMeta(current => ({
              name: patch.name ?? current.name,
              description: patch.description ?? current.description,
              category: patch.category ?? current.category,
            }))
            return true
          }}
          stripBlock={stripGraphBlock}
          buildTestPrompt={index => buildNodeTestPrompt(
            { name: job.title, description: '', category: '', graph: plan },
            plan.nodes[index]?.id ?? '',
            job.input as Record<string, unknown> | undefined,
          )}
          mentionItems={mentionItems}
          initialMessage={initialAuthorText ?? undefined}
          onInitialConsumed={() => setInitialAuthorText(null)}
          autoOpen
          idleHint="Describe the plan and the agent draws the graph; ask for changes and it redraws it. Branches run at once. This chat stays scoped to this plan."
          placeholder="Describe or change the plan…"
        />
      </aside>}
      {chatOpen && job && plan && <div className="graph-resize-handle" role="separator" aria-orientation="vertical" aria-label="Resize chat panel" onPointerDown={dragChat} />}
      <main className="graph-main">
        {!job || !plan
          ? <div className="graph-empty"><strong>Select a graph plan</strong><p className="muted">Architect drafts and graph executions appear here.</p></div>
          : <>
            <GraphCanvas
              job={job}
              plan={plan}
              profiles={profiles}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onDeselect={() => setSelectedId(null)}
              editable={job.status === 'queued'}
              onMoveNode={moveNode}
              onConnect={connect}
              onDisconnect={disconnect}
              onAddNode={addNode}
              onAddScript={addScriptNode}
              onAddTrigger={addTrigger}
              hasTrigger={plan.nodes.some(node => node.type === 'trigger')}
            />
          </>}
      </main>

      {/* Only when there is something to inspect. An empty 294px column that says
          "select a node" is furniture the canvas could be using. */}
      {job && plan && definition && <div className="graph-resize-handle" role="separator" aria-orientation="vertical" aria-label="Resize node detail" data-grow="left" onPointerDown={dragInspector} />}
      {job && plan && definition && <aside className="graph-inspector">
            <div className="graph-inspector-head">
              <div><p className="graph-eyebrow">Node</p><h2>{definition.name}</h2></div>
              <span className={`graph-status st-${selectedState?.status ?? 'pending'}`}>{statusLabel(selectedState?.status ?? 'pending')}</span>
              <button className="row-action" onClick={() => setSelectedId(null)} aria-label="Close node inspector">×</button>
            </div>
            {job.status === 'queued' ? <div className="graph-plan-form">
              <label>Name<input value={definition.name} onChange={event => updateSelected({ name: event.target.value })} /></label>
              {definition.type === 'trigger' ? <>
                <div className="graph-trigger-mode">
                  <p className="graph-eyebrow">Trigger node</p>
                  <div className="seg graph-trigger-seg" role="group" aria-label="Trigger mode">
                    <button
                      type="button"
                      className={(definition.trigger_kind ?? 'manual') === 'manual' ? 'active' : ''}
                      aria-pressed={(definition.trigger_kind ?? 'manual') === 'manual'}
                      onClick={() => updateSelected({ trigger_kind: 'manual' })}
                    >Manual</button>
                    <button
                      type="button"
                      className={definition.trigger_kind === 'scheduled' ? 'active' : ''}
                      aria-pressed={definition.trigger_kind === 'scheduled'}
                      onClick={() => updateSelected({
                        trigger_kind: 'scheduled',
                        schedule: definition.schedule ?? DEFAULT_GRAPH_SCHEDULE,
                      })}
                    >Schedule</button>
                  </div>
                </div>
                {(definition.trigger_kind ?? 'manual') === 'manual' ? <div className="graph-trigger-panel">
                  <p className="graph-eyebrow">Intake form</p>
                  <WorkflowInputsEditor
                    inputs={definition.inputs ?? []}
                    onChange={inputs => updateSelected({ inputs })}
                    onEditStateChange={setIntakeEditState}
                  />
                  <p className="muted graph-field-note">
                    These are the questions each manual run asks. Nodes reference them with <code>{'{{id}}'}</code>.
                  </p>
                </div> : <div className="graph-trigger-panel">
                  <p className="graph-eyebrow">Schedule settings</p>
                  <ScheduleSettingsEditor
                    value={definition.schedule ?? DEFAULT_GRAPH_SCHEDULE}
                    onChange={schedule => updateSelected({ schedule })}
                  />
                  <p className="muted graph-field-note">
                    Scheduled runs never ask for per-run input. Leave this schedule Off, then open Schedules to configure durable bindings before turning it On.
                  </p>
                </div>}
              </> : definition.type === 'script' ? <>
                <label>Script<input
                  value={definition.command ?? ''}
                  placeholder="A file in this project's scripts/ folder — e.g. fetch-data.sh"
                  onChange={event => updateSelected({ command: event.target.value })}
                /></label>
                <label>Arguments <span className="muted">(one per line; {'{{input}}'} fills from the plan input)</span><textarea
                  rows={3}
                  value={(definition.args ?? []).join('\n')}
                  onChange={event => updateSelected({ args: event.target.value.split('\n') })}
                /></label>
                <p className="muted graph-field-note">
                  This step runs the saved script — no AI involved, so it's fast, free, and
                  repeatable. It gets the earlier steps' results as JSON on its input; what it
                  prints becomes this step's output. The first time a script (or a changed
                  script) runs, the plan pauses for your one-time approval.
                </p>
                <label>Output contract<select value={definition.output_kind} onChange={event => updateSelected({ output_kind: event.target.value as GraphOutputKind })}>
                  {OUTPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
                </select></label>
                <label className="graph-check"><input type="checkbox" checked={!!definition.review_required} onChange={event => updateSelected({ review_required: event.target.checked })} />Require human review</label>
              </> : <>
                <label>Instruction<MentionTextarea rows={5} items={mentionItems} value={definition.instruction} onChange={value => updateSelected({ instruction: value })} ariaLabel="Node instruction" /></label>
                <label>Expected output<MentionTextarea
                  rows={2}
                  items={mentionItems}
                  value={definition.expected_output ?? ''}
                  placeholder="What a good result looks like — @ mentions a project file"
                  onChange={value => updateSelected({ expected_output: value })}
                  ariaLabel="Expected output"
                /></label>
                <label>Rules <span className="muted">(optional)</span><MentionTextarea
                  rows={3}
                  items={mentionItems}
                  value={definition.rules ?? ''}
                  placeholder="Constraints on how to do it — @ mentions a project file"
                  onChange={value => updateSelected({ rules: value })}
                  ariaLabel="Node rules"
                /></label>
                {definition.target_ambiguous && <p className="graph-target-question" role="alert">
                  This job needs an answer before the plan can start: {definition.target_question || 'which area should it work in?'}
                </p>}
                <label>Works in<select
                  value={definition.target_ambiguous ? '' : (definition.target ?? '')}
                  onChange={event => {
                    const value = event.target.value
                    // Picking a target IS the answer to an ambiguous job (T1) —
                    // the question clears with the choice, never silently.
                    // touches_repo mirrors the server's derivation for live
                    // display only; the server recomputes it and never trusts it.
                    updateSelected({
                      target: value || null,
                      target_ambiguous: false,
                      target_question: null,
                      touches_repo: !!value && value !== 'ops',
                    })
                  }}
                >
                  {definition.target_ambiguous
                    ? <option value="">Choose where this job works…</option>
                    : <option value="">Anywhere — the project folder</option>}
                  <option value="ops">Ops — notes, reports, files</option>
                  {codeAreas.map(area => <option key={area} value={area}>
                    {area === '.' ? 'Repo — the project root' : `Repo — ${area}`}
                  </option>)}
                </select></label>
                {definition.touches_repo && <p className="muted graph-field-note">
                  A repo job: it gets its own isolated copy of the code, and you review the change before it lands.
                </p>}
                <label>Agent<select
                  value={definition.profile_id ?? ''}
                  onChange={event => updateSelected({
                    profile_id: event.target.value ? Number(event.target.value) : null,
                  })}
                >
                  <option value="">Default — this run’s agent</option>
                  {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select></label>
                {(() => {
                  const effectiveProfile = profiles.find(profile => profile.id === (definition.profile_id ?? profileId))
                    ?? profiles.find(profile => profile.id === profileId)
                  const runnerId = effectiveProfile?.runner_id
                  const detected = runnerId ? skillsByRunner[runnerId] : undefined
                  const chosen = definition.skill_ids ?? []
                  // Hints from the chat may name skills this runner does not detect —
                  // keep them visible so they can be unchecked, not silently kept.
                  const unknown = chosen.filter(id => !(detected ?? []).some(skill => skill.id === id))
                  const toggle = (id: string, on: boolean) => updateSelected({
                    skill_ids: on ? [...chosen, id] : chosen.filter(item => item !== id),
                  })
                  return <details className="graph-skills" onToggle={event => { if ((event.target as HTMLDetailsElement).open) loadSkills(runnerId) }}>
                    <summary>Skills <span className="muted">({chosen.length ? `${chosen.length} suggested` : 'optional'})</span></summary>
                    <p className="muted graph-field-note">Suggested to the agent in its prompt. What is actually enabled comes from the agent's own setup.</p>
                    {detected == null
                      ? <p className="muted">{runnerId ? 'Loading detected skills…' : 'Pick an agent first.'}</p>
                      : detected.length === 0 && unknown.length === 0
                        ? <p className="muted">No skills detected for this agent.</p>
                        : <>
                          {detected.map(skill => <label className="graph-check" key={skill.id} title={skill.description || undefined}>
                            <input type="checkbox" checked={chosen.includes(skill.id)} onChange={event => toggle(skill.id, event.target.checked)} />{skill.name || skill.id}
                          </label>)}
                          {unknown.map(id => <label className="graph-check graph-skill-unknown" key={id} title="Not detected for this agent">
                            <input type="checkbox" checked onChange={() => toggle(id, false)} />{id} <span className="muted">(not detected)</span>
                          </label>)}
                        </>}
                  </details>
                })()}
                <label>Output contract<select value={definition.output_kind} onChange={event => updateSelected({ output_kind: event.target.value as GraphOutputKind })}>
                  {OUTPUT_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
                </select></label>
                <label className="graph-check"><input type="checkbox" checked={!!definition.review_required} onChange={event => updateSelected({ review_required: event.target.checked })} />Require human review</label>
              </>}
              {definition.type !== 'trigger' && <fieldset>
                <legend>Dependencies</legend>
                {/* The canvas gesture is drag-to-connect; this list is the same edit
                    for anyone not using a pointer. */}
                {plan.nodes.filter(node => node.id !== definition.id).map(node => <label className="graph-check" key={node.id}>
                  <input type="checkbox" checked={plan.edges.some(edge => edge.from === node.id && edge.to === definition.id)} onChange={() => toggleDependency(node.id)} />{node.name}
                </label>)}
              </fieldset>}
              <div className="graph-form-actions">
                {/* A dry run in the chat: the agent executes this node and its upstream
                    chain conversationally, so the instruction can be judged before
                    Approve & start. No job state is touched. */}
                {definition.type !== 'trigger' && (() => {
                  const testable = definition.type === 'script'
                    ? !!(definition.command ?? '').trim()
                    : !!definition.instruction.trim()
                  return <button
                    className="ghost-button"
                    disabled={!testable}
                    title={testable ? undefined : definition.type === 'script' ? 'Name a script first' : 'Write an instruction first'}
                    onClick={() => { setChatOpen(true); setPendingTest(definition.id) }}
                  >Test in chat</button>
                })()}
                <button className="ghost-button danger" onClick={removeNode} disabled={plan.nodes.length <= 1}>Remove node</button>
              </div>
            </div> : <div className="graph-run-detail">
              <p>{definition.type === 'trigger'
                ? 'Manual trigger — this plan starts when you press start.'
                : definition.type === 'script'
                  ? `⚡ Runs the saved script scripts/${definition.command}${definition.args?.length ? ` with: ${definition.args.join(' ')}` : ''} — no AI involved.`
                  : definition.instruction || 'No instruction.'}</p>
              {definition.expected_output && <div className="graph-node-detail">
                <p className="graph-eyebrow">Expected output</p><p>{definition.expected_output}</p>
              </div>}
              {definition.rules && <div className="graph-node-detail">
                <p className="graph-eyebrow">Rules</p><p>{definition.rules}</p>
              </div>}
              <dl>
                {definition.type !== 'trigger' && <div><dt>Works in</dt><dd>
                  {definition.target_ambiguous
                    ? 'Unanswered — where should it work?'
                    : definition.target == null ? 'The project folder'
                    : definition.target === 'ops' ? 'Ops'
                    : `Repo — ${definition.target === '.' ? 'the project root' : definition.target}`}
                </dd></div>}
                <div><dt>Output</dt><dd>{definition.output_kind}</dd></div>
                {definition.type === 'script' && <div><dt>Script</dt><dd>scripts/{definition.command}</dd></div>}
                {definition.type !== 'trigger' && definition.type !== 'script' && <div><dt>Agent</dt><dd>
                  {profiles.find(profile => profile.id === definition.profile_id)?.name ?? 'Run default'}
                </dd></div>}
                <div><dt>Attempt</dt><dd>{selectedState?.run_id ?? '—'}</dd></div>
              </dl>
              {selectedState?.inputs != null && <details><summary>Resolved inputs</summary><pre>{JSON.stringify(selectedState.inputs, null, 2)}</pre></details>}
              {/* Decision-hold (slice 12): the node parked itself with a genuine
                  open question. Answering re-runs it with the decision — usable
                  while the plan is RUNNING, because independent branches never
                  stopped and the answer should not wait for them. */}
              {selectedState?.status === 'review' && selectedState.question && <div className="satpam-decision">
                <p className="graph-eyebrow">The agent needs a decision</p>
                <p className="satpam-question">{selectedState.question}</p>
                <textarea rows={3} value={answerText} onChange={event => setAnswerText(event.target.value)} placeholder="Your decision…" />
                <button
                  className="primary-button"
                  disabled={!!busy || !answerText.trim()}
                  onClick={() => void act('answer', () => answerGraphNode(token, job.id, definition.id, answerText.trim()), 'Decision sent — the job is re-running with it.')}
                >{busy === 'answer' ? 'Sending…' : 'Answer & resume'}</button>
              </div>}
              {/* A script blocked on trust is not a malfunction — it is the one-time
                  approval surface (T6). The card shows the script's actual content +
                  sha256 (audit F4: approve bytes, not a filename); approving echoes
                  that hash so a swapped file is refused, then reruns the step. */}
              {definition.type === 'script' && selectedState?.status === 'failed' && selectedState.error?.startsWith('script_approval_required') ? <ScriptApprovalCard
                token={token}
                jobId={job.id}
                nodeId={definition.id}
                command={definition.command ?? ''}
                approving={busy === 'approve-script'}
                disabled={!!busy}
                onApprove={sha256 => void act('approve-script', () => approveGraphNodeScript(token, job.id, definition.id, sha256), 'Script approved — the step is running again.')}
              />
              : selectedState?.error && <p className="error-text">{selectedState.error}</p>}
              {selectedState?.output != null ? <pre className="graph-output">{outputText(selectedState)}</pre> : <p className="muted">No validated output yet.</p>}
              {['review', 'done'].includes(job.status) && selectedState && ['done', 'review', 'failed'].includes(selectedState.status) && <>
                <label>Correct output<textarea rows={8} value={outputEdit} onChange={event => setOutputEdit(event.target.value)} /></label>
                <div className="graph-form-actions">
                  <button className="ghost-button" onClick={() => void act('rerun', () => rerunGraphNode(token, job.id, definition.id))} disabled={!!busy}>Rerun node</button>
                  <button className="ghost-button" onClick={() => void saveOutput()} disabled={!!busy || !outputEdit.trim()}>Save correction</button>
                  {selectedState.status === 'review' && <button className="primary-button" onClick={() => void act('approve-node', () => approveGraphNode(token, job.id, definition.id))} disabled={!!busy}>Approve node</button>}
                </div>
              </>}
            </div>}
      </aside>}
    </div>

    {job?.status === 'queued' && <footer className="graph-editor-footer">
      <button
        className="ghost-button graph-promote-button"
        onClick={promotePlan}
        disabled={!!busy || !!job.workflow_id || runBlocked}
        title={job.workflow_id ? 'This plan is already a reusable workflow' : 'Create a reusable workflow from this plan'}
      >
        {busy === 'save-template' ? 'Saving…' : '★ Save as Workflow'}
      </button>
      <button
        className="primary-button"
        onClick={() => setRunTarget({ kind: 'job', job: { ...job, graph: plan ?? job.graph } })}
        disabled={!!busy || runBlocked}
        title={runBlocked ? 'Wait for a valid saved workflow before running' : undefined}
      >
        {busy === 'start' ? 'Starting…' : '▶ Run'}
      </button>
    </footer>}
    {runTarget && <RunModal
      title={runTarget.kind === 'template' ? runTarget.template.name : runTarget.job.title}
      inputs={runTarget.kind === 'template'
        ? runTarget.template.inputs
        : triggerInputs(runTarget.job.graph)}
      onCancel={() => setRunTarget(null)}
      onRun={async input => {
        if (runTarget.kind === 'template') await createFromTemplate(runTarget.template, input)
        else await runDraft(runTarget.job, input)
      }}
    />}
  </section>
}
