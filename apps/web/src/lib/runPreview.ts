// The way back from the app viewport to the controls that run it (#147).
//
// Since #147 the two halves of Run & Preview sit in different places: the Run
// controls (command, folder, port, Run/Stop, the owner-power consent) are the
// dock's Preview tool, and the viewport renders in the Artifacts main window.
// The dock reaches the main window through the shell seam (`onOpenAppViewport`
// → `App` → `ArtifactsScreen`), which it must, because opening a destination is
// App state. The other direction needs no state: it only asks the dock to show
// a tool it already owns, so it is a window event - the same shape "Reveal in
// Files" uses (`lib/revealFile`). Without it, a viewport whose app has stopped
// would name no next step, and a refusal that names no next step is a dead end
// this product does not ship (prune B5, #133).

export const OPEN_RUN_PREVIEW_EVENT = 'proxima:open-run-preview'

/** Ask the dock to open its Run & Preview controls. */
export function openRunPreview() {
  window.dispatchEvent(new Event(OPEN_RUN_PREVIEW_EVENT))
}
