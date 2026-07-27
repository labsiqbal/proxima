import { Composer } from '../chat/Composer'
import { useMasterState } from '../../master/MasterStateProvider'
import { MasterTargetPicker } from './MasterTargetPicker'

export function MasterComposer({
  token,
  projectSlug,
}: {
  token: string
  projectSlug?: string
}) {
  const { activeRun, composer, desk, fleet, focus, target, actions } = useMasterState()
  const busy = composer.sending || ['queued', 'running'].includes(activeRun?.status || '')
  const targetContainer = fleet.containers.find(
    container => container.id === target.containerId,
  )
  const focusedContainer = fleet.containers.find(
    container => container.id === focus.containerId,
  )
  const attachmentSlug = targetContainer?.slug
    || focusedContainer?.slug
    || projectSlug
  return (
    <div className="master-composer-dock">
      <MasterTargetPicker />
      <Composer
        token={token}
        slug={attachmentSlug}
        disabled={busy}
        promptModes={false}
        generateKinds={[]}
        combinedActions={false}
        placeholder="Message Master about an outcome, decision, or Task..."
        textareaLabel="Message Master"
        submitLabel={busy ? 'Master is working' : 'Send to Master'}
        submittingLabel="Sending to Master..."
        draftValue={composer.draft}
        onDraftChange={actions.setDraft}
        selectionValue={composer.selection}
        onSelectionChange={actions.setSelection}
        focusRequest={composer.focusRequest}
        clearOnSubmitStart={false}
        onSubmit={content => actions.send(content)}
      />
      <p className="master-composer-hint">
        {desk?.unattended
          ? 'Master may continue already queued Tasks within your saved budgets.'
          : 'Master responds when you ask. Every delegated action remains a visible Task.'}
        {!attachmentSlug
          ? ' Choose a Container target when you need attachments or file mentions.'
          : ''}
      </p>
      {composer.error && (
        <p className="master-composer-error" role="alert">
          <strong>Message not sent.</strong> {composer.error}
        </p>
      )}
    </div>
  )
}
