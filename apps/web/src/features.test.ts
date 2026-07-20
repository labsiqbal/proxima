import assert from 'node:assert/strict'
import { test } from 'vitest'
import {
  DEFAULT_FEATURES,
  isFeatureCommandEnabled,
  isFeatureSessionEnabled,
  isFeatureViewEnabled,
  parseAppFeatures,
  resolveAppFeatures,
  studioBridgeAvailability,
} from './features'

test('feature config is strict and defaults off', async () => {
  assert.deepEqual(parseAppFeatures({ features: { design_studio: true, workflow_graph: true } }), { designStudio: true, workflowGraph: true })
  assert.deepEqual(parseAppFeatures({ features: { design_studio: 'yes' } }), DEFAULT_FEATURES)
  assert.deepEqual(parseAppFeatures(null), DEFAULT_FEATURES)
  assert.deepEqual(await resolveAppFeatures(async () => { throw new Error('offline') }), DEFAULT_FEATURES)
})

test('disabled sessions and views fail closed', () => {
  assert.equal(isFeatureViewEnabled('design', DEFAULT_FEATURES), false)
  assert.equal(isFeatureViewEnabled('graph', DEFAULT_FEATURES), false)
  assert.equal(isFeatureViewEnabled('chat', DEFAULT_FEATURES), true)
  assert.equal(isFeatureSessionEnabled({ title: 'Design: launch card', mode: 'design' }, DEFAULT_FEATURES), false)
  assert.equal(isFeatureSessionEnabled({ title: 'Ordinary chat' }, DEFAULT_FEATURES), true)
})

test('disabled studio commands and bridges cannot dispatch actions', () => {
  assert.deepEqual(studioBridgeAvailability('image', DEFAULT_FEATURES), { design: false })
  assert.deepEqual(studioBridgeAvailability('video-file', DEFAULT_FEATURES), { design: false })
  assert.equal(isFeatureCommandEnabled({ name: '/image', surface: 'chat' }, DEFAULT_FEATURES), true)
  assert.equal(isFeatureCommandEnabled({ name: '/design-studio', surface: 'chat' }, DEFAULT_FEATURES), false)
})
