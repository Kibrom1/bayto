// Mirrors orchestrator/src/orchestrator/api/tasks.py (POST /tasks, GET /tasks).

import { ApiError, parseError } from './errors'

export interface Task {
  id: string
  title: string
  brief: string | null
  output_type: string
  // Derived from the last session's status; null if the task has no sessions yet.
  status: string | null
  last_session_id: string | null
  output_artifact_id: string | null
  // Build mode: the local repository the team may change. Null for discussion-only tasks.
  workspace?: Workspace | null
  // Opt-in meaning-based search of long attachments; sends document text to the embedding provider.
  semantic_retrieval?: boolean
  // Opt-in: a local model answers a seat's turn when Claude has run out of usage.
  local_fallback?: { enabled: boolean; model: string | null } | null
}

export interface Workspace {
  repo_path: string
  base_branch: string
  test_command?: string | null
  // More commands the developer and tester may run, beyond the test command.
  allowed_commands?: string[]
}

export interface WorkspaceReport {
  ok: boolean
  problems: { code: string; detail: string }[]
  repo_root: string | null
  current_branch: string | null
  dirty: boolean
  dirty_files: string[]
  suggested_test_command?: string | null
  permissions?: { role: string; can_run: string[]; can_run_tests: boolean; note: string }[]
}

export interface TaskCreateRequest {
  title: string
  brief: string
  output_type: string
  success_criteria?: string | null
  workspace?: Workspace | null
  semantic_retrieval?: boolean
  local_fallback?: { enabled: boolean; model?: string | null }
}

export interface RetrievalSetting {
  semantic_retrieval: boolean
  ready: boolean // an embedding provider is set up on the orchestrator
  provider: string | null
  model: string | null
  needs: string | null // what to set when not ready
}

/** The shared wording of the switch: the cost of turning it on is stated where it is chosen. */
export const SEMANTIC_LABEL = 'Search long documents by meaning'

export async function getRetrieval(taskId: string): Promise<RetrievalSetting> {
  const res = await fetch(`/tasks/${taskId}/retrieval`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export async function setRetrieval(taskId: string, semantic: boolean): Promise<RetrievalSetting> {
  const res = await fetch(`/tasks/${taskId}/retrieval`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ semantic_retrieval: semantic }),
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export async function listTasks(): Promise<Task[]> {
  const res = await fetch('/tasks')
  if (!res.ok) {
    throw new ApiError(res.status, `GET /tasks failed: ${res.status} ${await parseError(res)}`)
  }
  return res.json()
}

export async function createTask(req: TaskCreateRequest): Promise<Task> {
  const res = await fetch('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST /tasks failed: ${res.status} ${await parseError(res)}`)
  }
  const data = await res.json()
  return data.task
}

/** Ask the orchestrator whether a repo path can be used for Build mode (POST /workspaces/preflight). */
export async function preflightWorkspace(ws: Workspace): Promise<WorkspaceReport> {
  const res = await fetch('/workspaces/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ws),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `Could not check the repository: ${await parseError(res)}`)
  }
  return res.json()
}

/** Give an existing task a repository (PUT /tasks/{id}/workspace), for example to continue a discussion as a Build. */
export async function setTaskWorkspace(taskId: string, ws: Workspace): Promise<void> {
  const res = await fetch(`/tasks/${taskId}/workspace`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ws),
  })
  if (!res.ok) {
    throw new ApiError(res.status, `${await parseError(res)}`)
  }
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`/tasks/${taskId}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new ApiError(res.status, `DELETE /tasks/${taskId} failed: ${res.status} ${await parseError(res)}`)
  }
}

export interface LocalFallbackSetting {
  enabled: boolean
  model: string | null // as stored; null = use the server default
  effective_model: string | null
  default_model: string | null // BAYTO_LOCAL_FALLBACK_MODEL on the server
  ready: boolean // the model could run now
  needs: string | null
  build: boolean // Build tasks never fall back
}

/** The honest wording shown wherever the option is chosen. */
export const LOCAL_FALLBACK_LABEL = 'If Claude runs out of usage, let a local model answer instead'
export const LOCAL_FALLBACK_NOTE =
  "A local model is usually much weaker than Claude, so those answers will differ in quality and are marked 'Local model'. " +
  'It sees the same discussion but cannot use tools. Build seats never fall back: they pause for you. Bayto tries Claude again after the limit resets.'

export async function getLocalFallback(taskId: string): Promise<LocalFallbackSetting> {
  const res = await fetch(`/tasks/${taskId}/local-fallback`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export async function setLocalFallback(taskId: string, enabled: boolean, model: string | null): Promise<LocalFallbackSetting> {
  const res = await fetch(`/tasks/${taskId}/local-fallback`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, model }),
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}
