import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEFAULT_FEATURES,
  isDisabledFeatureHash,
  isFeatureCommandEnabled,
  isFeatureSessionEnabled,
  isFeatureViewEnabled,
  parseAppFeatures,
  resolveAppFeatures,
  studioBridgeAvailability,
} from '../src/features.ts'

test('feature config is strict and defaults off', async () => {
  assert.deepEqual(parseAppFeatures({ features: { video: true, design_studio: true } }), { video: true, designStudio: true })
  assert.deepEqual(parseAppFeatures({ features: { video: 1, design_studio: 'yes' } }), DEFAULT_FEATURES)
  assert.deepEqual(parseAppFeatures(null), DEFAULT_FEATURES)
  assert.deepEqual(await resolveAppFeatures(async () => { throw new Error('offline') }), DEFAULT_FEATURES)
})

test('disabled sessions, views, and video hashes fail closed', () => {
  assert.equal(isFeatureViewEnabled('video', DEFAULT_FEATURES), false)
  assert.equal(isFeatureViewEnabled('design', DEFAULT_FEATURES), false)
  assert.equal(isFeatureViewEnabled('chat', DEFAULT_FEATURES), true)
  assert.equal(isFeatureSessionEnabled({ title: 'Design: launch card', mode: 'design' }, DEFAULT_FEATURES), false)
  assert.equal(isFeatureSessionEnabled({ title: 'Video: launch reel' }, DEFAULT_FEATURES), false)
  assert.equal(isFeatureSessionEnabled({ title: 'Ordinary chat' }, DEFAULT_FEATURES), true)
  assert.equal(isDisabledFeatureHash('#project/proxima-video__demo__one', DEFAULT_FEATURES), true)
  assert.equal(isDisabledFeatureHash('#project/proxima-video__demo__one', { ...DEFAULT_FEATURES, video: true }), false)
})

test('disabled studio commands and bridges cannot dispatch actions', () => {
  assert.deepEqual(studioBridgeAvailability('image', DEFAULT_FEATURES), { design: false, video: false })
  assert.deepEqual(studioBridgeAvailability('video-file', DEFAULT_FEATURES), { design: false, video: false })
  assert.equal(isFeatureCommandEnabled({ name: '/image', surface: 'chat' }, DEFAULT_FEATURES), true)
  assert.equal(isFeatureCommandEnabled({ name: '/video', surface: 'chat' }, DEFAULT_FEATURES), false)
  assert.equal(isFeatureCommandEnabled({ name: '/design-studio', surface: 'chat' }, DEFAULT_FEATURES), false)
})
