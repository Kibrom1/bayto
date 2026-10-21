import { ApiError, parseError } from './errors'

export interface PullRequestRecord { url: string; head_sha: string; draft: boolean; title: string }
export interface PullRequestState {
  branch: string
  base_branch: string
  ready: boolean
  opened: PullRequestRecord | null
  draft: { title: string; body: string }
}

export async function getPullRequest(sessionId: string): Promise<PullRequestState> {
  const res = await fetch(`/sessions/${sessionId}/pull-request`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}

export async function openPullRequest(
  sessionId: string,
  body: { title: string; body: string; draft: boolean; open_unfinished: boolean },
): Promise<PullRequestRecord> {
  const res = await fetch(`/sessions/${sessionId}/pull-request`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return res.json()
}
