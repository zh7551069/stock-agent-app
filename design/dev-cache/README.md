# Figma design/dev-cache

Single-screen frame cache for Cloud Agent / fullstack. Read **this cache only** for colors, type, spacing, and screenshots.

Layout: `design/dev-cache/<fileKey>/<nodeId>/meta.json` + `screenshot.png`

Index: `design/dev-cache/catalog.json` (`products[].fileKey` + `products[].dir`).

- `nodeId` folder uses `-` instead of `:` (e.g. `109-2` = `109:2`)
- `cache_rev` in `meta.json`; bump on redesign
- If `meta.json` exists for the frame, do **not** call `get_design_context` or any other online Figma Dev Mode read
- Implementation artifacts must record the cache path (`design/dev-cache/<fileKey>/<nodeId>/`) and that frame's `cache_rev`
- Only 设计师figma may refresh via online Dev Mode

Current product: 蓄势池 / 连板股先手, fileKey `2aHDiixK3G20NfZDIjRxzx`, 21 frames, `cache_rev` 1.
