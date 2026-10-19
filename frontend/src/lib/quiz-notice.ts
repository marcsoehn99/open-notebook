import { toast } from 'sonner'

import { quizzesApi } from '@/lib/api/quizzes'
import { quizRefetchIntervalMs } from '@/lib/types/quiz'

export type QuizOpenRequest = {
  notebookId: string
  quizId: string
}

export type QuizNoticeCopy = {
  running: string
  ready: string
  openLabel: string
  failed: string
}

type QuizJob = {
  quizId: string
  notebookId: string
}

type Snapshot = { readonly request: QuizOpenRequest | null }

type WatchInput = {
  notebookId: string
  quizId: string
  ready: string
  openLabel: string
  failed: string
  running?: string
}

type WatchDeps = {
  get: (quizId: string) => Promise<{ status?: string }>
  delay: (ms: number) => Promise<void>
}

type StoredEnvelope = {
  v: 1
  jobs: QuizJob[]
}

export const quizNoticeStorageKey = 'open-notebook-quiz-notice'

const emptySnapshot: Snapshot = { request: null }
const listeners = new Set<() => void>()
const generationByQuiz = new Map<string, number>()
const pollErrorBudget = 5
const toastEpochByKey = new Map<string, number>()

let snapshot: Snapshot = emptySnapshot
let navigator: ((notebookId: string) => void) | null = null
let session = 0

const defaultDeps: WatchDeps = {
  get: (quizId) => quizzesApi.get(quizId, null),
  delay: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}

let deps = defaultDeps

function noticeToastId(quizId: string) {
  return `quiz-notice:${quizId}`
}

function pendingToastId(notebookId: string) {
  return `quiz-notice:pending:${notebookId}`
}

function browserStorage(): Storage | null {
  try {
    if (typeof localStorage === 'undefined') return null
    return localStorage
  } catch {
    return null
  }
}

function isJob(value: unknown): value is QuizJob {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.quizId === 'string' &&
    record.quizId.length > 0 &&
    typeof record.notebookId === 'string' &&
    record.notebookId.length > 0
  )
}

function parseStoredJobs(raw: string | null): QuizJob[] {
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return []
    const envelope = parsed as Record<string, unknown>
    if (envelope.v !== 1 || !Array.isArray(envelope.jobs)) return []
    return envelope.jobs.filter(isJob)
  } catch {
    return []
  }
}

function readJobs(): QuizJob[] {
  const storage = browserStorage()
  if (!storage) return []
  try {
    return parseStoredJobs(storage.getItem(quizNoticeStorageKey))
  } catch {
    return []
  }
}

function writeJobs(jobs: QuizJob[]) {
  const storage = browserStorage()
  if (!storage) return
  try {
    const envelope: StoredEnvelope = { v: 1, jobs }
    storage.setItem(quizNoticeStorageKey, JSON.stringify(envelope))
  } catch {
    return
  }
}

function putJob(job: QuizJob) {
  const jobs = readJobs().filter((existing) => existing.quizId !== job.quizId)
  jobs.push(job)
  writeJobs(jobs)
}

function forgetJob(job: QuizJob) {
  const id = noticeToastId(job.quizId)
  toastEpochByKey.set(id, (toastEpochByKey.get(id) ?? 0) + 1)
  generationByQuiz.set(job.quizId, (generationByQuiz.get(job.quizId) ?? 0) + 1)
  writeJobs(readJobs().filter((existing) => existing.quizId !== job.quizId))
  toast.dismiss(id)
}

function reportFailure(job: QuizJob, failed: string) {
  forgetJob(job)
  toast.error(failed, { id: noticeToastId(job.quizId), duration: Infinity })
}

function armDismiss(job: QuizJob, toastKey: string) {
  // Sonner dismisses the previous toast when the same id is replaced.
  // The epoch is stored before that call so the old callback cannot drop the job.
  const epoch = (toastEpochByKey.get(toastKey) ?? 0) + 1
  toastEpochByKey.set(toastKey, epoch)
  return () => {
    if (toastEpochByKey.get(toastKey) !== epoch) return
    forgetJob(job)
  }
}

function showRunningNotice(job: QuizJob, running: string) {
  const id = noticeToastId(job.quizId)
  const onDismiss = armDismiss(job, id)
  toast.info(running, {
    id,
    duration: Infinity,
    closeButton: true,
    onDismiss,
  })
}

function showReadyNotice(job: QuizJob, ready: string, openLabel: string) {
  const id = noticeToastId(job.quizId)
  const onDismiss = armDismiss(job, id)
  toast.info(ready, {
    id,
    duration: Infinity,
    closeButton: true,
    action: {
      label: openLabel,
      onClick: () => requestQuizOpen(job.notebookId, job.quizId),
    },
    onDismiss,
  })
}

function emit(next: Snapshot) {
  snapshot = next
  for (const listener of listeners) listener()
}

function stillCurrent(quizId: string, generation: number, ticket: number) {
  return ticket === session && generationByQuiz.get(quizId) === generation
}

async function poll(input: WatchInput, generation: number, ticket: number, errors = 0) {
  if (!stillCurrent(input.quizId, generation, ticket)) return

  let status: string | undefined
  let threw = false
  try {
    status = (await deps.get(input.quizId)).status
  } catch {
    threw = true
  }

  if (!stillCurrent(input.quizId, generation, ticket)) return

  const job = { quizId: input.quizId, notebookId: input.notebookId }

  if (threw) {
    const nextErrors = errors + 1
    if (nextErrors >= pollErrorBudget) {
      reportFailure(job, input.failed)
      return
    }
    const wait = quizRefetchIntervalMs(undefined)
    await deps.delay(wait === false ? 2000 : wait)
    return poll(input, generation, ticket, nextErrors)
  }

  if (status === 'completed') {
    showReadyNotice(job, input.ready, input.openLabel)
    return
  }

  if (status === 'failed') {
    reportFailure(job, input.failed)
    return
  }

  const wait = quizRefetchIntervalMs(status)
  await deps.delay(wait === false ? 2000 : wait)
  return poll(input, generation, ticket, 0)
}

async function adopt(job: QuizJob, copy: QuizNoticeCopy, generation: number, ticket: number) {
  let status: string | undefined
  let threw = false
  try {
    status = (await deps.get(job.quizId)).status
  } catch {
    threw = true
  }

  if (!stillCurrent(job.quizId, generation, ticket)) return

  const input: WatchInput = {
    notebookId: job.notebookId,
    quizId: job.quizId,
    ready: copy.ready,
    openLabel: copy.openLabel,
    failed: copy.failed,
  }

  if (!threw && status === 'completed') {
    showReadyNotice(job, copy.ready, copy.openLabel)
    return
  }

  if (!threw && status === 'failed') {
    reportFailure(job, copy.failed)
    return
  }

  showRunningNotice(job, copy.running)
  if (!threw) return poll(input, generation, ticket, 0)

  const wait = quizRefetchIntervalMs(undefined)
  await deps.delay(wait === false ? 2000 : wait)
  return poll(input, generation, ticket, 1)
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
  generationByQuiz.clear()
  toastEpochByKey.clear()
  navigator = null
  snapshot = emptySnapshot
  const storage = browserStorage()
  if (!storage) return
  try {
    storage.removeItem(quizNoticeStorageKey)
  } catch {
    return
  }
}

export function registerQuizNavigator(next: ((notebookId: string) => void) | null) {
  navigator = next
}

export function requestQuizOpen(notebookId: string, quizId: string) {
  forgetJob({ notebookId, quizId })
  emit({ request: { notebookId, quizId } })
  navigator?.(notebookId)
}

export function clearQuizOpenRequest() {
  if (!snapshot.request) return
  emit(emptySnapshot)
}

export function announceQuizRunning(notebookId: string, message: string) {
  toast.info(message, { id: pendingToastId(notebookId), duration: Infinity })
}

export function dismissQuizNotice(notebookId: string) {
  toast.dismiss(pendingToastId(notebookId))
}

export function watchQuizJob(input: WatchInput) {
  const job = { quizId: input.quizId, notebookId: input.notebookId }
  putJob(job)
  toast.dismiss(pendingToastId(input.notebookId))
  const generation = (generationByQuiz.get(input.quizId) ?? 0) + 1
  generationByQuiz.set(input.quizId, generation)
  if (input.running) showRunningNotice(job, input.running)
  void poll(input, generation, session)
}

export function resumeQuizNotices(copy: QuizNoticeCopy) {
  for (const job of readJobs()) {
    if (generationByQuiz.has(job.quizId)) continue
    const generation = (generationByQuiz.get(job.quizId) ?? 0) + 1
    generationByQuiz.set(job.quizId, generation)
    void adopt(job, copy, generation, session)
  }
}
