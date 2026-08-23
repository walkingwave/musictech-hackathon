# Session Lifecycle

## Definitions

A **session** is persisted server-side project data under `sessions/<id>/`. It
contains the source vocal when present, analysis metadata, arrangement state,
guides, generated stems, and MIDI artifacts. A browser project is a local UI
snapshot that references one server session.

## User actions

| Action | Server session files | Browser/localStorage state | Intended use |
| --- | --- | --- | --- |
| **Load project** | Retained | Replaced with the selected project's restored state | Resume a prior project |
| **Close project** | Retained | Active project/timeline/audio references are cleared | Start or open another project later |
| **Delete project** | Permanently removed after confirmation | Cleared only when it refers to the deleted session | Remove an unwanted project |

Close is deliberately non-destructive. Delete must require a confirmation that
identifies the project and must be rejected while that session is generating.

## Scope and constraints

- The backend owns persisted session files; the browser must not infer or delete
  filesystem paths.
- Session IDs are opaque identifiers, not user-controlled paths.
- Loading replaces the current project rather than merging tracks.
- Server-created stems can be restored by their saved audio URLs. Exact browser
  MIDI-editor clip restoration is a separate persistence concern until note/clip
  data is stored server-side.
- This local application has no authentication. A networked deployment needs
  authorization before exposing session listing/loading/deletion endpoints.
