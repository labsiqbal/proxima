# Provenance

Vendored copy of the `masterplan` skill. Do not edit the skill content here;
change it upstream and re-vendor.

- Source repository: https://github.com/labsiqbal/masterplan
- Vendored path in source: `skills/masterplan/`
- Source commit: `1d6bbec207c7d057e1d7f2cdae9591b7a8f811d5` (2026-07-22)
- License: MIT (see the `LICENSE` file in this folder, copied from the source repo)

## Refreshing this copy

```bash
git clone --depth 1 https://github.com/labsiqbal/masterplan /tmp/masterplan
rm -rf bundled-skills/masterplan
cp -r /tmp/masterplan/skills/masterplan bundled-skills/masterplan
cp /tmp/masterplan/LICENSE bundled-skills/masterplan/LICENSE
# then update the "Source commit" line above (git -C /tmp/masterplan rev-parse HEAD)
```

Only this file and `LICENSE` are Proxima additions; everything else is a faithful
copy of the source tree.
