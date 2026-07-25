import { api } from './client'

export type CatalogCommand = {
	name: string
	description: string
	surface: string
	unavailableMessage: string | null
	/** When set, invoking this slash queues an agent turn that requires the skill. */
	skillId?: string | null
}
export type CommandCatalog = {
	groups: Array<{ label: string; commands: CatalogCommand[] }>
	profileId?: number | null
	runnerId?: string | null
}

export const getCommandCatalog = (
	token: string,
	opts?: { profileId?: number | null; runnerId?: string | null; rescan?: boolean },
) => {
	const q = new URLSearchParams()
	if (opts?.profileId != null) q.set('profile_id', String(opts.profileId))
	if (opts?.runnerId) q.set('runner_id', opts.runnerId)
	if (opts?.rescan) q.set('rescan', '1')
	const qs = q.toString()
	return api<CommandCatalog>(`/api/commands/catalog${qs ? `?${qs}` : ''}`, token)
}
