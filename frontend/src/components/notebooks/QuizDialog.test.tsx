import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { QuizDialog } from '@/components/notebooks/QuizDialog'
import { quizzesApi } from '@/lib/api/quizzes'
import { requestQuizOpen, resetQuizNotice } from '@/lib/quiz-notice'
import { NoteResponse, NotebookResponse, SourceListResponse } from '@/lib/types/api'

vi.mock('sonner', () => ({
  toast: {
    info: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  },
}))

vi.mock('@/lib/api/quizzes', () => ({
  quizzesApi: {
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

describe('QuizDialog start', () => {
  beforeEach(() => {
    resetQuizNotice()
    vi.mocked(quizzesApi.create).mockReset()
    vi.mocked(quizzesApi.create).mockImplementation(() => new Promise(() => {}))
    vi.mocked(toast.info).mockClear()
  })

  it('shows the background sentence before the create request returns', async () => {
    render(
      <QuizDialog
        notebook={notebook}
        sources={[source]}
        notes={[] as NoteResponse[]}
        open
        onOpenChange={vi.fn()}
      />
    )

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

    render(
      <QuizDialog
        notebook={notebook}
        sources={[source]}
        notes={[] as NoteResponse[]}
        open={false}
        onOpenChange={onOpenChange}
      />
    )

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(true))
  })
})
