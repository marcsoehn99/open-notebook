import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { enUS } from '@/lib/locales/en-US'
import {
  announceQuizRunning,
  getQuizOpenSnapshot,
  installQuizNoticeDeps,
  registerQuizNavigator,
  resetQuizNotice,
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
      id: 'quiz-notice:notebook:1',
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
        id: 'quiz-notice:notebook:1',
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

  it('ignores a finished poll after a newer quiz replaced it', async () => {
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
    expect(readyCalls).toHaveLength(1)
    const options = readyCalls[0][1]
    if (!options?.action || typeof options.action !== 'object' || !('onClick' in options.action)) {
      throw new Error('ready notice has no action')
    }
    options.action.onClick({} as never)
    expect(getQuizOpenSnapshot().request?.quizId).toBe('quiz:2')
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
      id: 'quiz-notice:notebook:1',
      duration: Infinity,
    })
    expect(delay).not.toHaveBeenCalled()
  })
})
