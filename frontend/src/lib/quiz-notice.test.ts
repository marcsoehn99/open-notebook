import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { enUS } from '@/lib/locales/en-US'
import {
  announceQuizRunning,
  dismissQuizNotice,
  getQuizOpenSnapshot,
  installQuizNoticeDeps,
  quizNoticeStorageKey,
  registerQuizNavigator,
  resetQuizNotice,
  resumeQuizNotices,
  watchQuizJob,
} from '@/lib/quiz-notice'

vi.mock('sonner', () => ({
  toast: {
    info: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  },
}))

vi.mock('@/lib/api/quizzes', () => ({
  quizzesApi: {
    get: vi.fn(),
    create: vi.fn(),
  },
}))

const notice = {
  notebookId: 'notebook:1',
  quizId: 'quiz:1',
  ready: 'Das Quiz ist fertig.',
  openLabel: 'Quiz öffnen',
  failed: 'Das Quiz konnte nicht erstellt werden',
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

describe('quiz notice', () => {
  beforeEach(() => {
    resetQuizNotice()
    installQuizNoticeDeps(null)
    vi.mocked(toast.info).mockClear()
    vi.mocked(toast.error).mockClear()
    vi.mocked(toast.dismiss).mockClear()
  })

  it('keeps the background sentence in the catalog', () => {
    expect(enUS.quiz.noticeBackground).toBe('Quiz wird erstellt – läuft im Hintergrund')
  })

  it('shows the background sentence before any status request', () => {
    announceQuizRunning(notice.notebookId, enUS.quiz.noticeBackground)
    expect(toast.info).toHaveBeenCalledWith(enUS.quiz.noticeBackground, {
      id: 'quiz-notice:pending:notebook:1',
      duration: Infinity,
    })
  })

  it('replaces the notice with a clickable hint when the quiz is ready', async () => {
    const open = vi.fn()
    registerQuizNavigator(open)
    installQuizNoticeDeps({
      get: vi.fn().mockResolvedValue({ status: 'completed' }),
      delay: vi.fn().mockResolvedValue(undefined),
    })

    watchQuizJob(notice)
    await flush()

    expect(toast.info).toHaveBeenCalledWith(
      notice.ready,
      expect.objectContaining({
        id: 'quiz-notice:quiz:1',
        duration: Infinity,
        action: expect.objectContaining({ label: notice.openLabel }),
      })
    )

    const options = vi.mocked(toast.info).mock.calls.at(-1)?.[1]
    if (!options?.action || typeof options.action !== 'object' || !('onClick' in options.action)) {
      throw new Error('ready notice has no action')
    }
    options.action.onClick({} as never)

    expect(open).toHaveBeenCalledWith('notebook:1')
    expect(getQuizOpenSnapshot().request).toEqual({
      notebookId: 'notebook:1',
      quizId: 'quiz:1',
    })
  })

  it('keeps the first quiz notice when a second quiz starts', async () => {
    let finishFirst: (value: { status: string }) => void = () => {}
    const get = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<{ status: string }>((resolve) => {
            finishFirst = resolve
          })
      )
      .mockResolvedValue({ status: 'completed' })
    installQuizNoticeDeps({
      get,
      delay: vi.fn().mockResolvedValue(undefined),
    })

    watchQuizJob(notice)
    watchQuizJob({ ...notice, quizId: 'quiz:2' })
    await flush()
    finishFirst({ status: 'completed' })
    await flush()

    const readyCalls = vi
      .mocked(toast.info)
      .mock.calls.filter((call) => call[0] === notice.ready)
    expect(readyCalls.map((call) => call[1]?.id)).toEqual([
      'quiz-notice:quiz:2',
      'quiz-notice:quiz:1',
    ])
    const first = readyCalls.find((call) => call[1]?.id === 'quiz-notice:quiz:1')
    if (!first?.[1]?.action || typeof first[1].action !== 'object' || !('onClick' in first[1].action)) {
      throw new Error('ready notice has no action')
    }
    first[1].action.onClick({} as never)
    expect(getQuizOpenSnapshot().request?.quizId).toBe('quiz:1')
    expect(storedQuizIds()).toEqual(['quiz:2'])
  })

  it('keeps waiting while the quiz is still running', async () => {
    const delay = vi.fn().mockResolvedValue(undefined)
    installQuizNoticeDeps({
      get: vi
        .fn()
        .mockResolvedValueOnce({ status: 'running' })
        .mockResolvedValueOnce({ status: 'completed' }),
      delay,
    })

    watchQuizJob(notice)
    await flush()

    expect(delay).toHaveBeenCalled()
    expect(toast.info).toHaveBeenCalledWith(
      notice.ready,
      expect.objectContaining({ action: expect.objectContaining({ label: notice.openLabel }) })
    )
  })

  it('stops polling when generation fails', async () => {
    const delay = vi.fn().mockResolvedValue(undefined)
    installQuizNoticeDeps({
      get: vi.fn().mockResolvedValue({ status: 'failed' }),
      delay,
    })

    watchQuizJob(notice)
    await flush()

    expect(toast.error).toHaveBeenCalledWith(notice.failed, {
      id: 'quiz-notice:quiz:1',
      duration: Infinity,
    })
    expect(delay).not.toHaveBeenCalled()
    expect(storedQuizIds()).not.toContain('quiz:1')
  })

  it('removes the stored job when the ready notice is opened or closed', async () => {
    installQuizNoticeDeps({
      get: vi.fn().mockResolvedValue({ status: 'completed' }),
      delay: vi.fn().mockResolvedValue(undefined),
    })

    watchQuizJob(notice)
    await flush()
    expect(storedQuizIds()).toEqual(['quiz:1'])

    const opened = lastInfoOptions()
    if (!opened.action || typeof opened.action !== 'object' || !('onClick' in opened.action)) {
      throw new Error('ready notice has no action')
    }
    opened.action.onClick({} as never)
    expect(storedQuizIds()).not.toContain('quiz:1')

    watchQuizJob(notice)
    await flush()
    const closed = lastInfoOptions()
    if (!closed.onDismiss) throw new Error('ready notice cannot be closed')
    closed.onDismiss({} as never)
    expect(storedQuizIds()).not.toContain('quiz:1')
  })

  it('resumes every stored quiz in one notebook', async () => {
    localStorage.setItem(
      quizNoticeStorageKey,
      JSON.stringify({
        v: 1,
        jobs: [
          { quizId: 'quiz:1', notebookId: 'notebook:1' },
          { quizId: 'quiz:2', notebookId: 'notebook:1' },
        ],
      })
    )
    const get = vi.fn().mockResolvedValue({ status: 'completed' })
    installQuizNoticeDeps({ get, delay: vi.fn().mockResolvedValue(undefined) })

    resumeQuizNotices(noticeCopy())
    await flush()

    expect(get.mock.calls.map((call) => call[0])).toEqual(['quiz:1', 'quiz:2'])
    expect(storedQuizIds()).toEqual(['quiz:1', 'quiz:2'])
  })

  it('stops polling after five status errors and drops the quiz', async () => {
    const get = vi.fn().mockRejectedValue(new Error('offline'))
    const delay = vi.fn().mockResolvedValue(undefined)
    installQuizNoticeDeps({ get, delay })

    watchQuizJob(notice)
    await flush()

    expect(get).toHaveBeenCalledTimes(5)
    expect(delay).toHaveBeenCalledTimes(4)
    expect(toast.error).toHaveBeenCalledWith(notice.failed, {
      id: 'quiz-notice:quiz:1',
      duration: Infinity,
    })
    expect(storedQuizIds()).not.toContain('quiz:1')
  })

  it('resets the error count after a successful status check', async () => {
    const get = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ status: 'completed' })
    installQuizNoticeDeps({
      get,
      delay: vi.fn().mockResolvedValue(undefined),
    })

    watchQuizJob(notice)
    await flush()

    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.info).toHaveBeenCalledWith(
      notice.ready,
      expect.objectContaining({ id: 'quiz-notice:quiz:1' })
    )
    expect(storedQuizIds()).toEqual(['quiz:1'])
  })

  it('does not drop another quiz when a create is dismissed', async () => {
    installQuizNoticeDeps({
      get: vi.fn().mockResolvedValue({ status: 'completed' }),
      delay: vi.fn().mockResolvedValue(undefined),
    })
    watchQuizJob(notice)
    await flush()

    announceQuizRunning(notice.notebookId, enUS.quiz.noticeBackground)
    dismissQuizNotice(notice.notebookId)

    expect(toast.dismiss).toHaveBeenCalledWith('quiz-notice:pending:notebook:1')
    expect(storedQuizIds()).toEqual(['quiz:1'])
  })

  it('ignores corrupt notice storage', () => {
    localStorage.setItem(quizNoticeStorageKey, '{')
    expect(() => resumeQuizNotices(noticeCopy())).not.toThrow()
    expect(toast.info).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('resumes a finished quiz when the module loads again', async () => {
    localStorage.setItem(
      quizNoticeStorageKey,
      JSON.stringify({ v: 1, jobs: [{ quizId: 'quiz:1', notebookId: 'notebook:1' }] })
    )
    vi.resetModules()
    const freshToast = await import('sonner')
    const fresh = await import('@/lib/quiz-notice')
    const get = vi.fn().mockResolvedValue({ status: 'completed' })
    fresh.installQuizNoticeDeps({
      get,
      delay: vi.fn().mockResolvedValue(undefined),
    })
    vi.mocked(freshToast.toast.info).mockClear()

    fresh.resumeQuizNotices(noticeCopy())
    await flush()
    fresh.resumeQuizNotices(noticeCopy())
    await flush()

    expect(get).toHaveBeenCalledTimes(1)
    expect(freshToast.toast.info).toHaveBeenCalledWith(
      notice.ready,
      expect.objectContaining({
        id: 'quiz-notice:quiz:1',
        duration: Infinity,
        action: expect.objectContaining({ label: notice.openLabel }),
      })
    )
    expect(storedQuizIds()).toEqual(['quiz:1'])
    fresh.resetQuizNotice()
  })

  it('resumes a running quiz when the module loads again', async () => {
    localStorage.setItem(
      quizNoticeStorageKey,
      JSON.stringify({ v: 1, jobs: [{ quizId: 'quiz:9', notebookId: 'notebook:9' }] })
    )
    vi.resetModules()
    const freshToast = await import('sonner')
    const fresh = await import('@/lib/quiz-notice')
    let release: () => void = () => {}
    const delay = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        })
    )
    fresh.installQuizNoticeDeps({
      get: vi.fn().mockResolvedValue({ status: 'running' }),
      delay,
    })
    vi.mocked(freshToast.toast.info).mockClear()

    fresh.resumeQuizNotices(noticeCopy())
    await flush()

    expect(freshToast.toast.info).toHaveBeenCalledWith(
      enUS.quiz.noticeBackground,
      expect.objectContaining({
        id: 'quiz-notice:quiz:9',
        duration: Infinity,
      })
    )
    expect(storedQuizIds()).toContain('quiz:9')
    expect(delay).toHaveBeenCalled()
    fresh.resetQuizNotice()
    release()
    await flush()
  })
})

function noticeCopy() {
  return {
    running: enUS.quiz.noticeBackground,
    ready: notice.ready,
    openLabel: notice.openLabel,
    failed: notice.failed,
  }
}

function storedQuizIds() {
  const raw = localStorage.getItem(quizNoticeStorageKey)
  if (!raw) return []
  const parsed = JSON.parse(raw) as { jobs?: { quizId?: string }[] }
  return (parsed.jobs ?? []).map((job) => job.quizId)
}

function lastInfoOptions() {
  const options = vi.mocked(toast.info).mock.calls.at(-1)?.[1]
  if (!options) throw new Error('notice was not shown')
  return options
}
