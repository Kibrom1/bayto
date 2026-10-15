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
}

export interface TaskCreateRequest {
  title: string
  brief: string
  output_type: string
  success_criteria?: string | null
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
