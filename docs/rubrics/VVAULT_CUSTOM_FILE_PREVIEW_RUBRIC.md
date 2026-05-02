# VVAULT Custom File Preview Rubric

This is a product and engineering rubric for how VVAULT should preview custom file types in the Vault UI.

It is not a storage migration plan and it is not a format rewrite plan.

The motivating example is `.capsule`, but the rubric is intentionally generic so future custom file types can follow the same rules.

## Short Summary

VVAULT preview behavior must be derived from what a file **actually is and what can actually be recovered**, not from stale metadata alone.

Preview decisions must separate three different truths:

1. **Storage truth**: what is actually stored in `vault_files` or Supabase object storage
2. **Classification truth**: what the backend can infer from extension, MIME/content type, inline content, and recoverability
3. **Preview truth**: what the UI should honestly render for the user

For custom types like `.capsule`, the system should prefer a derived preview contract such as `preview_kind` over trusting a historical `file_type` field as the sole authority.

## Confirmed by Current Repo

The current Vault UI path is:

- `GET /api/vault/files` in `vvault/server/vvault_web_server.py`
- `_transform_files_for_display(...)` in `vvault/server/vvault_web_server.py`
- `selectFile(...)` in `src/components/VaultBrowser.js`
- preview rendering in `src/components/VaultBrowser.js`

The current `.capsule` example matters because the repo already treats capsules as structured text in live runtime paths:

- `vvault/server/memup_sync.py` reads capsule content with `json.loads(...)`
- `vvault/server/memup_sync.py` writes capsule content with `json.dumps(...)`
- `vvault/memory/vvault_core.py` stores local capsules as UTF-8 JSON
- `vvault/server/vvault_web_server.py` uses capsule JSON in SimDrive injection

The historical mismatch this rubric must guard against is also present in repo history:

- `scripts/migrate_to_supabase.py` treated `.capsule` as a binary extension in an older migration path

So VVAULT must assume that some custom files are **logically text/JSON** even when their stored row metadata may still look binary or storage-backed.

## 1. Purpose

The goal of file preview in VVAULT is to let the user inspect stored artifacts as truthfully and directly as possible without mutating the underlying storage contract.

Pass criteria:

- preview behavior reflects actual recoverable content
- preview failures are truthful and specific
- storage metadata is not rewritten just to make UI preview work
- custom file support is generic and reusable across constructs, users, and future anatomy types

Fail criteria:

- the UI says “binary” when preview merely failed
- preview behavior depends on one hard-coded construct path or one callsign
- support for a custom file type requires storage-path churn or row rewrites

## 2. File Preview Decision Model

Preview behavior should be derived in this order:

1. **Path and extension**
2. **Explicit MIME/content type**
3. **Inline content presence**
4. **Recoverability from storage-backed rows**
5. **Successful parsing or decoding**
6. **Final truthful fallback**

### Preview modes

#### `json`

Use when:

- extension or policy marks the file as structured text
- recovered content is readable text
- JSON parsing succeeds

Render behavior:

- pretty-print the JSON
- preserve raw text fidelity if pretty-print is not possible

#### `text`

Use when:

- recovered content is readable text
- JSON parsing fails or is not relevant
- the file is logically a text document, markdown file, transcript, config, or readable custom artifact

Render behavior:

- show raw text
- preserve line breaks

#### `markdown` or text-document

Use when:

- extension and policy say the content is markdown or markdown-like
- the viewer supports markdown rendering safely

If markdown rendering is not available or is risky, raw text is acceptable.

#### `media`

Use when:

- the file is an image, audio, or video format
- the backend can provide a safe preview payload or retrievable object

Render behavior:

- render the media directly when safe
- otherwise provide a download/open action

#### `binary`

Use only when:

- the file is truly non-textual
- the content is not previewable in the current UI
- or recovery produced undecodable bytes

Render behavior:

- say it is binary
- provide the best available fallback action

#### `unavailable`

Use when:

- the file might be previewable in theory
- but current recovery failed because inline content is missing, object retrieval failed, or the object is gone

Render behavior:

- say preview is unavailable
- do not claim the file is binary unless that is actually known

## 3. Custom File-Type Rules

### Rule 1: Custom extensions must be policy-driven

New extensions such as `.capsule` must be handled by a generic preview policy, not by construct-specific code.

Pass:

- behavior keys off extension and recoverability
- the same rule works for any `instances/{callsign}/.../{name}.capsule`

Fail:

- preview support is hard-coded to `nova-001`
- preview support assumes one directory branch only

### Rule 2: Logical structure beats stale historical row typing

If a custom file type is logically JSON or text, it should be previewed as such when readable content can be recovered, even if older rows still carry a legacy `file_type` such as `binary`.

Pass:

- `.capsule` can preview as JSON or text without mutating the row

Fail:

- stale `file_type` prevents recovery

### Rule 3: Recovery must be generic

If the backend already has a generic text recovery helper for storage-backed rows, custom file preview must reuse it instead of inventing one-off loaders.

For the current repo, `.capsule` should reuse the same file text recovery path already available in the backend.

## 4. Backend Responsibilities

The backend owns preview classification and recovery hints.

### Required responsibilities

- return the raw row data without mutating stored truth
- derive preview hints from extension, MIME/content type, inline content, and recoverability
- attempt text recovery for custom file types that are expected to be logically text/JSON
- expose a derived preview contract such as:
  - `preview_kind`
  - `content_type` or MIME
  - optionally `preview_error` when recovery fails

### Required policy

- do not rewrite historical `vault_files.file_type` values just for preview
- do not move storage paths
- do not create repo-local scaffolding to compensate for preview problems

### `.capsule` policy example

For `.capsule`:

- if inline `content` exists, use it
- otherwise attempt text recovery from storage-backed content
- if recovered text parses as JSON, return `preview_kind: json`
- if recovered text is readable but not valid JSON, return `preview_kind: text`
- if recovery fails, return `preview_kind: binary` or `preview_kind: unavailable` depending on what is actually known

## 5. Frontend Responsibilities

The frontend owns the final rendering decision, but it should prefer backend-derived hints over stale row metadata.

### Required responsibilities

- request detail payload for previewable files
- use derived preview hints first
- parse and pretty-print JSON safely
- fall back to raw text when content is readable but malformed as JSON
- show truthful unavailable or binary states when recovery fails

### Required safety behavior

- JSON parse must be wrapped in safe error handling
- preview UI must not crash on malformed content
- missing content must not be silently treated as valid binary

### Frontend pass/fail

Pass:

- content presence and derived preview hints drive rendering
- stale `file_type` alone does not decide the whole preview outcome

Fail:

- the UI gates preview solely on `file_type === 'binary'`
- the UI discards recoverable text because the list row was historically misclassified

## 6. Truthful Fallback Rules

The UI must tell the truth about why a preview is not shown.

### These states must be differentiated

#### Truly binary

Meaning:

- the file is non-text and not previewable in the current UI

Acceptable copy:

- `Binary file`
- `Preview not available for this binary type`

#### Text recoverable

Meaning:

- content can be recovered and shown, even if the stored row looks binary

Acceptable behavior:

- show JSON or raw text

#### Text malformed

Meaning:

- content is readable text but does not parse as structured JSON

Acceptable behavior:

- show raw text
- optionally note that structured parsing failed

#### Storage missing or unrecoverable

Meaning:

- the file might be text or structured data, but the actual content could not be retrieved

Acceptable copy:

- `Preview unavailable`
- `Content could not be recovered from storage`

Not acceptable:

- `Binary file` unless that is actually proven

## 7. Compatibility and Non-Breaking Constraints

This rubric assumes non-breaking preview support.

Required constraints:

- do not rewrite historical rows in this pass
- do not change `storage_path`
- do not rename `vault_files` columns
- do not introduce repo-local user data scaffolding as a preview workaround
- do not redefine a custom file format merely because preview is weak

Pass:

- preview improves while storage truth stays intact

Fail:

- preview support depends on migration first
- preview support changes canonical file names or paths

## 8. Testing Rubric

Every new previewable custom file type should have tests in three layers when practical.

### Backend tests

Must cover:

- inline JSON content -> `preview_kind: json`
- storage-recovered JSON -> `preview_kind: json`
- storage-recovered readable text -> `preview_kind: text`
- malformed JSON text -> still recoverable as text
- true binary -> binary or unavailable result
- missing storage object -> unavailable or equivalent truthful failure hint

### Frontend tests

Must cover:

- derived preview hint drives rendering
- JSON is pretty-printed
- malformed JSON falls back to raw text
- binary state only appears when recovery really fails or the file is truly binary

### Integration tests

When environment health allows, add a route-to-UI test for one real custom type such as `.capsule`.

If the environment blocks full integration, static or mocked contract tests are still required.

## 9. Acceptance Criteria

A new custom file type passes this rubric only if all of the following are true:

- clicking the file in the Vault UI shows readable content when the underlying artifact is text or structured data
- JSON-backed custom files render as formatted JSON
- readable non-JSON content renders as raw text
- the UI does not label a file as binary just because preview recovery failed
- historical `vault_files` rows remain valid without migration
- storage paths remain unchanged
- the implementation is generic for the file type, not tied to one construct
- tests cover inline content, storage recovery, malformed content, binary truth, and missing content

## Final Checklist for Engineers Adding a New Previewable Custom File Type

- define whether the custom extension is logically `json`, `text`, `media`, or `binary`
- confirm whether old rows may be mislabeled historically
- add or reuse backend recovery logic without mutating stored rows
- return a derived preview hint such as `preview_kind`
- make the frontend prefer derived preview behavior over stale `file_type`
- pretty-print JSON safely
- fall back to raw text when parsing fails but content is readable
- use truthful unavailable copy when recovery fails
- do not change storage paths or file contracts just to improve preview
- add backend tests
- add frontend tests
- add an integration test when environment health allows
