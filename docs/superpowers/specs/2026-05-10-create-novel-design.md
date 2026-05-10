# Create Novel — Design Spec

**Date:** 2026-05-10  
**Status:** Approved

## Summary

Allow users to create a new novel from the UI. A "New Novel" toggle button on the `/novels` page expands an inline form. On success the user is navigated to the novel's Process Chapter page.

## Backend

### New endpoint: `POST /api/novels`

- **File:** `backend/api/routes/novels.py`
- **Request body:** JSON with `title` (string, required), `author` (string | null, optional), `language` (string | null, optional)
- **Response:** `201 Created` with the new novel serialized as `NovelSummary`
- **Error cases:** 422 if `title` is missing or blank

### New query: `create_novel()`

- **File:** `backend/api/queries.py`
- Generates a UUID v4 and uses `NOW()` for `created_at`
- Inserts into the existing `novels` table
- Returns the inserted row as a `dict` (same shape as `_list_novels_real`)
- No migration needed — table schema already supports all fields

### New request schema: `NovelCreate`

- **File:** `backend/api/schemas.py`
- Fields: `title: str`, `author: str | None = None`, `language: str | None = None`

## Frontend

### Changes to `Novels.tsx`

- Add `showForm: boolean` state (default `false`)
- Add a `createNovel(title, author, language)` fetch helper defined locally (POST to `/api/novels`, same pattern as `postChapter` in `Process.tsx`)
- Add `useMutation` calling `createNovel`; on success:
  1. Invalidate `["novels"]` query via `queryClient.invalidateQueries`
  2. Navigate to `/novels/:id/process` using `useNavigate`
- Render a "New Novel" button that sets `showForm = true`
- When `showForm` is true, render a form with:
  - `title` — required text input
  - `author` — optional text input
  - `language` — optional text input
  - Submit button (disabled while pending)
  - Cancel button → sets `showForm = false`
  - Inline error display if mutation fails

## Data Flow

```
User clicks "New Novel"
  → showForm = true → form renders

User fills title (+ optional fields) → submits
  → POST /api/novels
  → backend inserts row, returns NovelSummary
  → invalidate ["novels"] cache
  → navigate to /novels/:id/process
```

## Out of Scope

- Editing or deleting novels
- Novel cover images or additional metadata
- Duplicate title detection
