import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MediaGenerationPanels } from './SettingsScreen'

const getImageGenSettings = vi.fn()
const saveImageGenSettings = vi.fn()
const testImageGenSettings = vi.fn()
const getVideoGenSettings = vi.fn()
const saveVideoGenSettings = vi.fn()
const testVideoGenSettings = vi.fn()

vi.mock('../api/settings', () => ({
  getImageGenSettings: (...a: unknown[]) => getImageGenSettings(...a),
  saveImageGenSettings: (...a: unknown[]) => saveImageGenSettings(...a),
  testImageGenSettings: (...a: unknown[]) => testImageGenSettings(...a),
  getVideoGenSettings: (...a: unknown[]) => getVideoGenSettings(...a),
  saveVideoGenSettings: (...a: unknown[]) => saveVideoGenSettings(...a),
  testVideoGenSettings: (...a: unknown[]) => testVideoGenSettings(...a),
}))

const imageProviders = [
  { id: 'codex', displayName: 'Codex / ChatGPT auth', requiresKey: false, kind: 'codex' as const },
  {
    id: 'openai-compatible',
    displayName: 'OpenAI-compatible endpoint',
    requiresKey: true,
    kind: 'http' as const,
    defaultBaseUrl: 'https://api.openai.com/v1',
  },
]

const videoProviders = [
  {
    id: 'openai-compatible',
    displayName: 'OpenAI-compatible endpoint',
    requiresKey: true,
    kind: 'http' as const,
    defaultBaseUrl: 'https://api.openai.com/v1',
    note: 'Paste the API root with no path after it.',
  },
]

describe('Media generation settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getImageGenSettings.mockResolvedValue({
      provider: 'openai-compatible',
      model: 'xai/grok-imagine-image',
      baseUrl: 'https://api.linc.id/v1',
      hasApiKey: true,
      providers: imageProviders,
      defaultProvider: 'codex',
    })
    getVideoGenSettings.mockResolvedValue({
      provider: 'openai-compatible',
      model: 'xai/grok-imagine-video',
      baseUrl: 'https://api.linc.id/v1',
      hasApiKey: true,
      providers: videoProviders,
      defaultProvider: 'openai-compatible',
    })
    saveVideoGenSettings.mockResolvedValue({ ok: true, provider: 'openai-compatible', hasApiKey: true })
    testVideoGenSettings.mockResolvedValue({ ok: true, detail: 'Endpoint reachable - 18 models listed.' })
  })

  it('renders the Video generation card directly below Image generation', async () => {
    render(<MediaGenerationPanels token="t" />)

    const headings = await screen.findAllByRole('heading', { level: 3 })
    const titles = headings.map(h => h.textContent)
    expect(titles).toEqual(['Image generation', 'Video generation'])
  })

  it('shows an example base URL so the endpoint root is unambiguous', async () => {
    render(<MediaGenerationPanels token="t" />)

    const card = (await screen.findByRole('heading', { level: 3, name: 'Video generation' })).closest('.panel')!
    const endpoint = within(card as HTMLElement).getByPlaceholderText(/https:\/\/api\.openai\.com\/v1/)
    expect(endpoint).toHaveValue('https://api.linc.id/v1')
    expect(card).toHaveTextContent(/no path after it/i)
  })

  it('saves the video provider without touching the image settings', async () => {
    const user = userEvent.setup()
    render(<MediaGenerationPanels token="t" />)

    const card = (await screen.findByRole('heading', { level: 3, name: 'Video generation' })).closest('.panel')!
    await user.click(within(card as HTMLElement).getByRole('button', { name: /Save provider/ }))

    await waitFor(() => expect(saveVideoGenSettings).toHaveBeenCalled())
    expect(saveVideoGenSettings).toHaveBeenCalledWith('t', {
      provider: 'openai-compatible',
      baseUrl: 'https://api.linc.id/v1',
      model: 'xai/grok-imagine-video',
      apiKey: null,
    })
    expect(saveImageGenSettings).not.toHaveBeenCalled()
  })

  it('reports the video test-connection result', async () => {
    const user = userEvent.setup()
    render(<MediaGenerationPanels token="t" />)

    const card = (await screen.findByRole('heading', { level: 3, name: 'Video generation' })).closest('.panel')!
    await user.click(within(card as HTMLElement).getByRole('button', { name: /Test connection/ }))

    expect(await within(card as HTMLElement).findByText(/^Ready .* Endpoint reachable/)).toBeVisible()
    expect(testImageGenSettings).not.toHaveBeenCalled()
  })

  it('surfaces a failed video test as an actionable message', async () => {
    testVideoGenSettings.mockResolvedValue({ ok: false, detail: 'Key rejected (401).' })
    const user = userEvent.setup()
    render(<MediaGenerationPanels token="t" />)

    const card = (await screen.findByRole('heading', { level: 3, name: 'Video generation' })).closest('.panel')!
    await user.click(within(card as HTMLElement).getByRole('button', { name: /Test connection/ }))

    expect(await within(card as HTMLElement).findByText(/^Not ready .* Key rejected \(401\)\./)).toBeVisible()
  })
})
