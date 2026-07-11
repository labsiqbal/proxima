import { api } from './client'

export type CatalogCommand = { name: string; description: string; surface: string; unavailableMessage: string | null }
export type CommandCatalog = { groups: Array<{ label: string; commands: CatalogCommand[] }> }

export const getCommandCatalog = (token: string) => api<CommandCatalog>('/api/commands/catalog', token)
