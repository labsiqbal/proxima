import React from 'react'
import { getProjectLayout } from '../api/projects'

// Container-relative real locations of a project's wiki/artifacts/scripts/
// uploads, from the per-project layout map (prune C4/#138). Reserved names
// carry no routing meaning anymore - a surface that reads or writes one of
// these areas must resolve its real path through this hook instead of
// hardcoding today's default names.
export type ProjectAreaPaths = {
	wiki: string
	artifacts: string
	scripts: string
	uploads: string
}

// The pre-layout-map defaults, used only as the fail-open fallback when the
// layout endpoint is unavailable (an unavailable container cannot be browsed
// either way, so the fallback never silently targets the wrong real folder).
const DEFAULT_AREA_PATHS: ProjectAreaPaths = {
	wiki: 'wiki',
	artifacts: 'artifacts',
	scripts: 'scripts',
	uploads: 'uploads',
}

const cache = new Map<string, ProjectAreaPaths>()
const inflight = new Map<string, Promise<ProjectAreaPaths>>()

async function fetchAreaPaths(token: string, slug: string): Promise<ProjectAreaPaths> {
	try {
		const layout = await getProjectLayout(token, slug)
		const paths: ProjectAreaPaths = {
			wiki: layout.areas.wiki.path,
			artifacts: layout.areas.artifacts.path,
			scripts: layout.areas.scripts.path,
			uploads: layout.areas.uploads.path,
		}
		cache.set(slug, paths)
		return paths
	} catch {
		return { ...DEFAULT_AREA_PATHS }
	}
}

export function invalidateProjectAreaPaths(slug?: string): void {
	if (slug === undefined) cache.clear()
	else cache.delete(slug)
}

/** The project's mapped area locations; null until known for this slug. */
export function useProjectAreaPaths(
	token: string | null | undefined,
	slug: string | null | undefined,
): ProjectAreaPaths | null {
	const [paths, setPaths] = React.useState<ProjectAreaPaths | null>(
		() => (slug ? cache.get(slug) ?? null : null),
	)
	React.useEffect(() => {
		if (!token || !slug) {
			setPaths(null)
			return
		}
		const cached = cache.get(slug)
		if (cached) {
			setPaths(cached)
			return
		}
		setPaths(null)
		let alive = true
		let request = inflight.get(slug)
		if (!request) {
			request = fetchAreaPaths(token, slug).finally(() => inflight.delete(slug))
			inflight.set(slug, request)
		}
		void request.then(result => {
			if (alive) setPaths(result)
		})
		return () => {
			alive = false
		}
	}, [token, slug])
	return paths
}
