import { toast } from 'sonner'

import { quizzesApi } from '@/lib/api/quizzes'
import { quizRefetchIntervalMs } from '@/lib/types/quiz'

export type QuizOpenRequest = {
  notebookId: string
  quizId: string
}

type Snapshot = { readonly request: QuizOpenRequest | null }

type WatchInput = {
  notebookId: string
  quizId: string
  ready: string
  openLabel: string
  failed: string
}

type WatchDeps = {
  get: (quizId: string) => Promise<{ status?: string }>
  delay: (ms: number) => Promise<void>
}

const emptySnapshot: Snapshot = { request: null }
const listeners = new Set<() => void>()
const generationByNotebook = new Map<string, number>()

let snapshot: Snapshot = emptySnapshot
let navigator: ((notebookId: string) => void) | null = null
let session = 0

const defaultDeps: WatchDeps = {
  get: (quizId) => quizzesApi.get(quizId, null),
  delay: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}

let deps = defaultDeps

function toastId(notebookId: string) {
  return `quiz-notice:${notebookId}`
}

function emit(next: Snapshot) {
  snapshot = next
  for (const listener of listeners) listener()
}

function stillCurrent(notebookId: string, generation: number, ticket: number) {
  return ticket === session && generationByNotebook.get(notebookId) === generation
}

async function poll(input: WatchInput, generation: number, ticket: number) {
  if (!stillCurrent(input.notebookId, generation, ticket)) return

  let status: string | undefined
  try {
    status = (await deps.get(input.quizId)).status
  } catch {
    status = undefined
  }

  if (!stillCurrent(input.notebookId, generation, ticket)) return

  if (status === 'completed') {
    toast.info(input.ready, {
      id: toastId(input.notebookId),
      duration: Infinity,
      action: {
        label: input.openLabel,
        onClick: () => requestQuizOpen(input.notebookId, input.quizId),
      },
    })
    return
  }

  if (status === 'failed') {
    toast.error(input.failed, { id: toastId(input.notebookId), duration: Infinity })
    return
  }

  const wait = quizRefetchIntervalMs(status)
  await deps.delay(wait === false ? 2000 : wait)
  return poll(input, generation, ticket)
}

export function subscribeQuizOpen(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getQuizOpenSnapshot() {
  return snapshot
}

export function getQuizOpenServerSnapshot() {
  return emptySnapshot
}

export function installQuizNoticeDeps(next: WatchDeps | null) {
  deps = next ?? defaultDeps
}

export function resetQuizNotice() {
  session += 1
  generationByNotebook.clear()
  navigator = null
  snapshot = emptySnapshot
}

export function registerQuizNavigator(next: ((notebookId: string) => void) | null) {
  navigator = next
}

export function requestQuizOpen(notebookId: string, quizId: string) {
  emit({ request: { notebookId, quizId } })
  navigator?.(notebookId)
}

export function clearQuizOpenRequest() {
  if (!snapshot.request) return
  emit(emptySnapshot)
}

export function announceQuizRunning(notebookId: string, message: string) {
  toast.info(message, { id: toastId(notebookId), duration: Infinity })
}

export function dismissQuizNotice(notebookId: string) {
  generationByNotebook.set(notebookId, (generationByNotebook.get(notebookId) ?? 0) + 1)
  toast.dismiss(toastId(notebookId))
}

export function watchQuizJob(input: WatchInput) {
  const generation = (generationByNotebook.get(input.notebookId) ?? 0) + 1
  generationByNotebook.set(input.notebookId, generation)
  void poll(input, generation, session)
}
