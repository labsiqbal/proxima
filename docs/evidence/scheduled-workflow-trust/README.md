# Scheduled workflow trust evidence

Disposable real-browser pass for scheduled workflow input trust. The production web
bundle runs against an isolated owner database and a fake runner. Default CI
(`npm run test:e2e:schedules`) is assertion-only. Capture mode regenerates the
stable before/after PNGs:

```bash
apps/api/.venv/bin/python scripts/verify_scheduled_workflow_browser.py \
  --screenshots docs/evidence/scheduled-workflow-trust
```

| Flow | Before | After |
| --- | --- | --- |
| Missing durable binding | [home needs binding](before-missing-binding.png) | [schedule Off with Needs binding](after-missing-binding-refusal.png) |
| Run now exact job | [ready schedule On](before-run-now.png) | [opened owning-project job](after-run-now-exact-job.png) |

Capture mode validates each file as a nonempty PNG with a stable filename. Live data
and the updater are never touched.
