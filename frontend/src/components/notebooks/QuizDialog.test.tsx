import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { QuizDialog } from '@/components/notebooks/QuizDialog'
import { quizzesApi } from '@/lib/api/quizzes'
import { requestQuizOpen, resetQuizNotice } from '@/lib/quiz-notice'
import { NoteResponse, NotebookResponse, SourceListResponse } from '@/lib/types/api'
import { QuizPlay, QuizSummary } from '@/lib/types/quiz'

vi.mock('sonner', () => ({
  toast: {
    info: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  },
}))

vi.mock('@/lib/api/quizzes', () => ({
  quizzesApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    start: vi.fn(),
    retry: vi.fn(),
    commit: vi.fn(),
    explain: vi.fn(),
  },
}))

vi.mock('@/components/podcasts/ContentSelectionPanel', () => ({
  ContentSelectionPanel: () => null,
}))

vi.mock('@/components/sources/SourceDialog', () => ({
  SourceDialog: () => null,
}))

const notebook: NotebookResponse = {
  id: 'notebook:n1',
  name: 'My notebook',
  description: '',
  archived: false,
  created: '2026-01-01T00:00:00Z',
  updated: '2026-01-01T00:00:00Z',
  source_count: 1,
  note_count: 0,
}

const source: SourceListResponse = {
  id: 'source:1',
  title: 'Kapitel',
  asset: null,
  embedded: false,
  embedded_chunks: 0,
  insights_count: 0,
  created: '2026-01-01T00:00:00Z',
  updated: '2026-01-01T00:00:00Z',
}

function summary(id: string, topic: string): QuizSummary {
  return {
    id,
    status: 'completed',
    num_questions: 1,
    difficulty: 'medium',
    topic_prompt: topic,
    language: 'de',
    source_ids: ['source:1'],
  }
}

function play(id: string, topic: string, stem: string, finished: boolean): QuizPlay {
  return {
    ...summary(id, topic),
    status: 'completed',
    cards: [
      {
        prompt: { type: 'short', question_id: `${id}-q`, position: 1, stem },
        result: null,
      },
    ],
    progress: finished
      ? { kind: 'finished', score: { correct: 1, total: 1 } }
      : { kind: 'in_progress', answered: 0, total: 1 },
  }
}

let queryClient: QueryClient

function dialog(open: boolean, onOpenChange = vi.fn()) {
  return (
    <QueryClientProvider client={queryClient}>
      <QuizDialog
        notebook={notebook}
        sources={[source]}
        notes={[] as NoteResponse[]}
        open={open}
        onOpenChange={onOpenChange}
      />
    </QueryClientProvider>
  )
}

describe('QuizDialog', () => {
  beforeEach(() => {
    resetQuizNotice()
    sessionStorage.clear()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    vi.mocked(quizzesApi.list).mockReset()
    vi.mocked(quizzesApi.list).mockResolvedValue([])
    vi.mocked(quizzesApi.create).mockReset()
    vi.mocked(quizzesApi.get).mockReset()
    vi.mocked(quizzesApi.start).mockReset()
    vi.mocked(quizzesApi.start).mockResolvedValue({ attempt_id: 'attempt:1' })
    vi.mocked(toast.info).mockClear()
    vi.mocked(toast.error).mockClear()
  })

  describe('start', () => {
    beforeEach(() => {
      vi.mocked(quizzesApi.create).mockImplementation(() => new Promise(() => {}))
    })

    it('shows the background sentence before the create request returns', async () => {
      render(dialog(true))

      const start = await screen.findByRole('button', { name: 'quiz.start' })
      await waitFor(() => expect(start).toBeEnabled())
      fireEvent.click(start)

      expect(screen.getByRole('status')).toHaveTextContent('quiz.noticeBackground')
      expect(toast.info).toHaveBeenCalledWith('quiz.noticeBackground', {
        id: 'quiz-notice:pending:notebook:n1',
        duration: Infinity,
      })
      expect(quizzesApi.create).toHaveBeenCalledTimes(1)
    })

    it('opens the quiz when the notice is clicked', async () => {
      const onOpenChange = vi.fn()
      requestQuizOpen(notebook.id, 'quiz:ready')

      render(dialog(false, onOpenChange))

      await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(true))
    })
  })

  it('opens the form from a quiz in progress and from the score', async () => {
    vi.mocked(quizzesApi.list).mockResolvedValue([summary('quiz:live', 'Live')])
    vi.mocked(quizzesApi.get).mockResolvedValue(play('quiz:live', 'Live', 'stem-live', false))
    const view = render(dialog(true))

    expect(await screen.findByText('stem-live')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'quiz.newQuiz' }))

    expect(await screen.findByRole('button', { name: 'quiz.start' })).toBeEnabled()
    expect(screen.queryByText('stem-live')).not.toBeInTheDocument()
    expect(screen.getByLabelText('quiz.numQuestions')).toBeInTheDocument()

    view.unmount()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    vi.mocked(quizzesApi.list).mockResolvedValue([summary('quiz:done', 'Done')])
    vi.mocked(quizzesApi.get).mockResolvedValue(play('quiz:done', 'Done', 'stem-done', true))
    render(dialog(true))

    expect(await screen.findByRole('heading', { name: 'quiz.score' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'quiz.newQuiz' }))

    expect(await screen.findByRole('button', { name: 'quiz.start' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'quiz.score' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('quiz.numQuestions')).toBeInTheDocument()
  })

  it('keeps the first quiz after a second quiz is created', async () => {
    const stored: QuizSummary[] = []
    const plays = new Map<string, QuizPlay>()
    vi.mocked(quizzesApi.list).mockImplementation(async () => stored.map((item) => ({ ...item })))
    vi.mocked(quizzesApi.get).mockImplementation(async (id: string) => {
      const found = plays.get(id)
      if (!found) {
        throw new Error(`missing ${id}`)
      }
      return found
    })
    vi.mocked(quizzesApi.create).mockImplementation(async (_notebookId, body) => {
      const id = `quiz:${stored.length + 1}`
      const topic = body.topic_prompt ?? id
      const created = summary(id, topic)
      stored.unshift(created)
      plays.set(id, play(id, topic, `stem-${topic}`, false))
      return created
    })

    render(dialog(true))
    const start = await screen.findByRole('button', { name: 'quiz.start' })
    await waitFor(() => expect(start).toBeEnabled())
    fireEvent.change(screen.getByLabelText('quiz.topic'), { target: { value: 'Alpha' } })
    fireEvent.click(start)

    expect(await screen.findByText('stem-Alpha')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'quiz.newQuiz' }))

    const again = await screen.findByRole('button', { name: 'quiz.start' })
    await waitFor(() => expect(again).toBeEnabled())
    fireEvent.change(screen.getByLabelText('quiz.topic'), { target: { value: 'Beta' } })
    fireEvent.click(again)

    expect(await screen.findByText('stem-Beta')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Alpha' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Beta' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Alpha' }))
    expect(await screen.findByText('stem-Alpha')).toBeInTheDocument()
    expect(screen.queryByText('stem-Beta')).not.toBeInTheDocument()
    expect(quizzesApi.create).toHaveBeenCalledTimes(2)
    expect(vi.mocked(quizzesApi.create).mock.calls.map((call) => call[1].topic_prompt)).toEqual([
      'Alpha',
      'Beta',
    ])
  })

  it('shows the newest quiz and New quiz again after the window is closed', async () => {
    vi.mocked(quizzesApi.list).mockResolvedValue([
      summary('quiz:new', 'Newer'),
      summary('quiz:old', 'Older'),
    ])
    vi.mocked(quizzesApi.get).mockImplementation(async (id: string) => {
      const newer = id === 'quiz:new'
      return play(id, newer ? 'Newer' : 'Older', newer ? 'stem-newer' : 'stem-older', false)
    })
    const view = render(dialog(true))

    expect(await screen.findByText('stem-newer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'quiz.newQuiz' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByText('stem-older')).toBeInTheDocument()

    view.rerender(dialog(false))
    view.rerender(dialog(true))

    expect(await screen.findByText('stem-newer')).toBeInTheDocument()
    expect(screen.queryByText('stem-older')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'quiz.newQuiz' })).toBeInTheDocument()
  })

  it('opens a listed quiz when that row is clicked', async () => {
    vi.mocked(quizzesApi.list).mockResolvedValue([
      summary('quiz:new', 'Newer'),
      summary('quiz:old', 'Older'),
    ])
    vi.mocked(quizzesApi.get).mockImplementation(async (id: string) => {
      const newer = id === 'quiz:new'
      return play(id, newer ? 'Newer' : 'Older', newer ? 'stem-newer' : 'stem-older', false)
    })
    render(dialog(true))

    expect(await screen.findByText('stem-newer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Older' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Older' }))

    expect(await screen.findByText('stem-older')).toBeInTheDocument()
    expect(screen.queryByText('stem-newer')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Newer' }))
    expect(await screen.findByText('stem-newer')).toBeInTheDocument()
  })
})
