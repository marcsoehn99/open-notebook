'use client'

import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { quizzesApi } from '@/lib/api/quizzes'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useQuiz } from '@/lib/hooks/use-quizzes'
import { useTranslation } from '@/lib/hooks/use-translation'
import { NoteResponse, NotebookResponse, SourceListResponse } from '@/lib/types/api'
import {
  CardResult,
  CreateQuizRequest,
  Difficulty,
  Explanation,
  QuestionType,
  QuizPrompt,
  Submission,
  Verdict,
  isQuizReady,
  quizErrorDetail,
  quizIsFailed,
} from '@/lib/types/quiz'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ContentSelectionPanel } from '@/components/podcasts/ContentSelectionPanel'
import {
  NotebookSelection,
  NotebookSummary,
  SourceMode,
} from '@/components/podcasts/generate-podcast-selection'
import { SourceDialog } from '@/components/sources/SourceDialog'

interface QuizDialogProps {
  notebook: NotebookResponse
  sources: SourceListResponse[]
  notes: NoteResponse[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

const QUESTION_TYPES: QuestionType[] = ['mc', 'multi', 'short', 'cloze']

function attemptKey(quizId: string) {
  return `quiz-attempt:${quizId}`
}

const attemptStarts = new Map<string, Promise<string>>()

function httpStatus(error: unknown): number | undefined {
  if (axios.isAxiosError(error)) {
    return error.response?.status
  }
  return undefined
}

function ensureAttempt(quizId: string): Promise<string> {
  const existing = sessionStorage.getItem(attemptKey(quizId))
  if (existing) {
    return Promise.resolve(existing)
  }
  const inflight = attemptStarts.get(quizId)
  if (inflight) {
    return inflight
  }
  const pending = quizzesApi
    .start(quizId)
    .then((started) => {
      sessionStorage.setItem(attemptKey(quizId), started.attempt_id)
      return started.attempt_id
    })
    .catch((error: unknown) => {
      attemptStarts.delete(quizId)
      throw error
    })
  attemptStarts.set(quizId, pending)
  return pending
}

export function QuizDialog({
  notebook,
  sources,
  notes,
  open,
  onOpenChange,
}: QuizDialogProps) {
  const { t, language } = useTranslation()
  const [quizId, setQuizId] = useState<string | null>(null)
  const [numQuestions, setNumQuestions] = useState(5)
  const [difficulty, setDifficulty] = useState<Difficulty>('medium')
  const [topic, setTopic] = useState('')
  const [quizLanguage, setQuizLanguage] = useState(language)
  const [types, setTypes] = useState<Record<QuestionType, boolean>>({
    mc: true,
    multi: true,
    short: true,
    cloze: true,
  })
  const [selections, setSelections] = useState<Record<string, NotebookSelection>>({})
  const [expanded, setExpanded] = useState<string[]>([])
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }
    setSelections((prev) => {
      const previous = prev[notebook.id]
      const sourceModes: Record<string, SourceMode> = {}
      sources.forEach((source) => {
        sourceModes[source.id] = previous?.sources[source.id] ?? 'full'
      })
      const noteModes: Record<string, SourceMode> = {}
      notes.forEach((note) => {
        noteModes[note.id] = 'off'
      })
      return {
        ...prev,
        [notebook.id]: { sources: sourceModes, notes: noteModes },
      }
    })
    setExpanded((current) => (current.includes(notebook.id) ? current : [notebook.id]))
  }, [open, notebook.id, sources, notes])

  const summary = useMemo<NotebookSummary>(() => {
    const selection = selections[notebook.id]
    const selectedSources = Object.values(selection?.sources ?? {}).filter(
      (mode) => mode !== 'off'
    ).length
    return { notebookId: notebook.id, sources: selectedSources, notes: 0 }
  }, [notebook.id, selections])

  const selectedSourceIds = Object.entries(selections[notebook.id]?.sources ?? {})
    .filter(([, mode]) => mode !== 'off')
    .map(([sourceId]) => sourceId)

  const selectedTypes = QUESTION_TYPES.filter((questionType) => types[questionType])

  async function startQuiz() {
    if (selectedSourceIds.length === 0 || selectedTypes.length === 0) {
      return
    }
    const body: CreateQuizRequest = {
      source_ids: selectedSourceIds,
      num_questions: numQuestions,
      difficulty,
      topic_prompt: topic.trim() ? topic.trim() : null,
      question_types: selectedTypes,
      language: quizLanguage.trim(),
    }
    setCreating(true)
    try {
      const created = await quizzesApi.create(notebook.id, body)
      setQuizId(created.id)
    } catch {
      toast.error(t('quiz.createFailed'))
    } finally {
      setCreating(false)
    }
  }

  function toggleNotebook(notebookId: string, checked: boolean | 'indeterminate') {
    const turnOn = checked === true
    const sourceModes: Record<string, SourceMode> = {}
    sources.forEach((source) => {
      sourceModes[source.id] = turnOn ? 'full' : 'off'
    })
    const noteModes: Record<string, SourceMode> = {}
    notes.forEach((note) => {
      noteModes[note.id] = 'off'
    })
    setSelections((prev) => ({
      ...prev,
      [notebookId]: { sources: sourceModes, notes: noteModes },
    }))
  }

  function changeSource(notebookId: string, sourceId: string, mode: SourceMode) {
    setSelections((prev) => {
      const current = prev[notebookId] ?? { sources: {}, notes: {} }
      return {
        ...prev,
        [notebookId]: {
          ...current,
          sources: { ...current.sources, [sourceId]: mode },
        },
      }
    })
  }

  function keepNotesOff(notebookId: string, noteId: string) {
    setSelections((prev) => {
      const current = prev[notebookId] ?? { sources: {}, notes: {} }
      return {
        ...prev,
        [notebookId]: {
          ...current,
          notes: { ...current.notes, [noteId]: 'off' },
        },
      }
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('quiz.title')}</DialogTitle>
          <DialogDescription>{t('quiz.description')}</DialogDescription>
        </DialogHeader>
        {quizId ? (
          <QuizPlayer quizId={quizId} onRestart={() => setQuizId(null)} />
        ) : (
          <div className="flex flex-col gap-4">
            {sources.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('quiz.noSources')}</p>
            ) : (
              <ContentSelectionPanel
                notebooks={[notebook]}
                isLoading={false}
                selectedNotebookSummaries={[summary]}
                tokenCount={0}
                charCount={0}
                expandedNotebooks={expanded}
                setExpandedNotebooks={setExpanded}
                selections={selections}
                sourcesByNotebook={{ [notebook.id]: sources }}
                notesByNotebook={{ [notebook.id]: notes }}
                fetchingNotebookIds={new Set()}
                onNotebookToggle={toggleNotebook}
                onSourceModeChange={changeSource}
                onNoteToggle={keepNotesOff}
              />
            )}
            {selectedSourceIds.length === 0 && (
              <p className="text-sm text-destructive">{t('quiz.selectSources')}</p>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label htmlFor="quiz-count">{t('quiz.numQuestions')}</Label>
                <Input
                  id="quiz-count"
                  type="number"
                  min={1}
                  max={10}
                  value={numQuestions}
                  onChange={(event) => setNumQuestions(Number(event.target.value))}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>{t('quiz.difficulty')}</Label>
                <Select
                  value={difficulty}
                  onValueChange={(value) => setDifficulty(value as Difficulty)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="easy">{t('quiz.easy')}</SelectItem>
                    <SelectItem value="medium">{t('quiz.medium')}</SelectItem>
                    <SelectItem value="hard">{t('quiz.hard')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="quiz-topic">{t('quiz.topic')}</Label>
                <Input
                  id="quiz-topic"
                  value={topic}
                  placeholder={t('quiz.topicPlaceholder')}
                  onChange={(event) => setTopic(event.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="quiz-language">{t('quiz.language')}</Label>
                <Input
                  id="quiz-language"
                  value={quizLanguage}
                  onChange={(event) => setQuizLanguage(event.target.value)}
                />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label>{t('quiz.types')}</Label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={types.mc}
                  onCheckedChange={(checked) =>
                    setTypes((prev) => ({ ...prev, mc: checked === true }))
                  }
                />
                {t('quiz.typeMc')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={types.multi}
                  onCheckedChange={(checked) =>
                    setTypes((prev) => ({ ...prev, multi: checked === true }))
                  }
                />
                {t('quiz.typeMulti')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={types.short}
                  onCheckedChange={(checked) =>
                    setTypes((prev) => ({ ...prev, short: checked === true }))
                  }
                />
                {t('quiz.typeShort')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={types.cloze}
                  onCheckedChange={(checked) =>
                    setTypes((prev) => ({ ...prev, cloze: checked === true }))
                  }
                />
                {t('quiz.typeCloze')}
              </label>
              {selectedTypes.length === 0 && (
                <p className="text-sm text-destructive">{t('quiz.selectTypes')}</p>
              )}
            </div>
            <Button
              onClick={startQuiz}
              disabled={
                creating ||
                sources.length === 0 ||
                selectedSourceIds.length === 0 ||
                selectedTypes.length === 0 ||
                !quizLanguage.trim() ||
                !Number.isInteger(numQuestions) ||
                numQuestions < 1 ||
                numQuestions > 10
              }
            >
              {creating ? t('quiz.starting') : t('quiz.start')}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function QuizPlayer({
  quizId,
  onRestart,
}: {
  quizId: string
  onRestart: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [attemptId, setAttemptId] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const [optionId, setOptionId] = useState('')
  const [optionIds, setOptionIds] = useState<string[]>([])
  const [textAnswer, setTextAnswer] = useState('')
  const [verdict, setVerdict] = useState<Verdict | null>(null)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [showScore, setShowScore] = useState(false)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const playQuery = useQuiz(quizId, attemptId)
  const play = playQuery.data
  const ready = play && isQuizReady(play) ? play : null

  useEffect(() => {
    if (!ready) {
      return
    }
    let cancelled = false
    ensureAttempt(quizId)
      .then((id) => {
        if (!cancelled) {
          setAttemptId(id)
        }
      })
      .catch(() => {
        if (!cancelled) {
          toast.error(t('quiz.createFailed'))
        }
      })
    return () => {
      cancelled = true
    }
  }, [quizId, ready, t])

  const card = ready?.cards[index]
  const prompt = card?.prompt
  const shown: CardResult | null =
    verdict && prompt && verdict.question_id === prompt.question_id
      ? verdict.result
      : card?.result ?? null

  useEffect(() => {
    setOptionId('')
    setOptionIds([])
    setTextAnswer('')
    setExplanation(null)
  }, [prompt?.question_id])

  async function submit() {
    if (!prompt || !attemptId) {
      return
    }
    const submission = buildSubmission(prompt, optionId, optionIds, textAnswer)
    if (!submission) {
      return
    }
    setSubmitting(true)
    try {
      const result = await quizzesApi.commit(quizId, {
        attempt_id: attemptId,
        question_id: prompt.question_id,
        submission,
      })
      setVerdict(result)
      await queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.quiz(quizId, attemptId),
      })
    } catch (error) {
      if (httpStatus(error) === 409) {
        toast.error(t('quiz.alreadyAnswered'))
        await queryClient.invalidateQueries({ queryKey: ['quizzes', quizId] })
      } else {
        toast.error(t('quiz.submitFailed'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function explain(questionId: string) {
    if (!attemptId) {
      return
    }
    try {
      const result = await quizzesApi.explain(quizId, questionId, attemptId)
      setExplanation(result)
    } catch {
      toast.error(t('quiz.explainFailed'))
    }
  }

  async function repeat() {
    attemptStarts.delete(quizId)
    sessionStorage.removeItem(attemptKey(quizId))
    try {
      const id = await ensureAttempt(quizId)
      setAttemptId(id)
      setIndex(0)
      setVerdict(null)
      setExplanation(null)
      setShowScore(false)
    } catch {
      toast.error(t('quiz.createFailed'))
    }
  }

  async function retry() {
    setRetrying(true)
    try {
      await quizzesApi.retry(quizId)
      attemptStarts.delete(quizId)
      sessionStorage.removeItem(attemptKey(quizId))
      setAttemptId(null)
      setVerdict(null)
      setExplanation(null)
      setShowScore(false)
      await queryClient.invalidateQueries({ queryKey: ['quizzes', quizId] })
    } catch {
      toast.error(t('quiz.retryFailed'))
    } finally {
      setRetrying(false)
    }
  }

  if (!play) {
    return <p className="text-sm text-muted-foreground">{t('quiz.waiting')}</p>
  }

  if (quizIsFailed(play.status)) {
    const detail = quizErrorDetail(t, play.error_message)
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-destructive">{t('quiz.failed')}</p>
        {detail && <p className="text-sm text-muted-foreground">{detail}</p>}
        <Button onClick={retry} disabled={retrying}>
          {t('quiz.retry')}
        </Button>
        <Button variant="outline" onClick={onRestart}>
          {t('quiz.start')}
        </Button>
      </div>
    )
  }

  if (!ready) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{t('quiz.generating')}</span>
        <span>{t('quiz.waiting')}</span>
      </div>
    )
  }

  const finishedScore =
    verdict?.progress.kind === 'finished'
      ? verdict.progress.score
      : ready.progress.kind === 'finished'
        ? ready.progress.score
        : null
  const onAnsweredCard = Boolean(
    verdict && prompt && verdict.question_id === prompt.question_id
  )
  const score = showScore || !onAnsweredCard ? finishedScore : null

  if (score) {
    const explainId =
      verdict?.question_id ?? ready.cards[ready.cards.length - 1]?.prompt.question_id
    return (
      <div className="flex flex-col gap-4">
        <h3 className="text-lg font-semibold">{t('quiz.score')}</h3>
        <Progress value={score.total ? (score.correct / score.total) * 100 : 0} />
        <p>{t('quiz.scoreLine', { correct: score.correct, total: score.total })}</p>
        {verdict && (
          <p className={verdict.result.correct ? 'text-success' : 'text-destructive'}>
            {verdict.result.correct ? t('quiz.correct') : t('quiz.incorrect')}
          </p>
        )}
        {explainId && (
          <Button variant="outline" onClick={() => explain(explainId)}>
            {t('quiz.explain')}
          </Button>
        )}
        {explanation && (
          <ExplanationBlock
            explanation={explanation}
            onOpenSource={() => {
              setSourceId(explanation.source_id)
              setSourceOpen(true)
            }}
          />
        )}
        <Button onClick={repeat}>{t('quiz.again')}</Button>
        <SourceDialog open={sourceOpen} onOpenChange={setSourceOpen} sourceId={sourceId} />
      </div>
    )
  }

  if (!prompt || !card) {
    return <p className="text-sm text-muted-foreground">{t('quiz.waiting')}</p>
  }

  const total = ready.cards.length
  const canAnswer = Boolean(buildSubmission(prompt, optionId, optionIds, textAnswer))

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">
          {t('quiz.progress', { current: index + 1, total })}
        </p>
        <Progress value={total ? ((index + 1) / total) * 100 : 0} />
      </div>
      <Card>
        <CardContent className="flex flex-col gap-4 pt-6">
          <p className="text-base">{prompt.stem}</p>
          <QuestionFields
            prompt={prompt}
            optionId={optionId}
            optionIds={optionIds}
            textAnswer={textAnswer}
            locked={Boolean(shown)}
            shown={shown}
            onOptionId={setOptionId}
            onOptionIds={setOptionIds}
            onText={setTextAnswer}
          />
          {shown && (
            <div className="flex flex-col gap-2">
              <p className={shown.correct ? 'text-success' : 'text-destructive'}>
                {shown.correct ? t('quiz.correct') : t('quiz.incorrect')}
              </p>
              {(shown.reveal.type === 'short' || shown.reveal.type === 'cloze') && (
                <p className="text-sm text-muted-foreground">
                  {t('quiz.accepted')}: {shown.reveal.accepted.join(', ')}
                </p>
              )}
              <Button variant="outline" onClick={() => explain(prompt.question_id)}>
                {t('quiz.explain')}
              </Button>
            </div>
          )}
          {explanation && (
            <ExplanationBlock
              explanation={explanation}
              onOpenSource={() => {
                setSourceId(explanation.source_id)
                setSourceOpen(true)
              }}
            />
          )}
        </CardContent>
      </Card>
      <div className="flex gap-2">
        {!shown && (
          <Button onClick={submit} disabled={!canAnswer || submitting}>
            {t('quiz.answer')}
          </Button>
        )}
        {shown && index < total - 1 && (
          <Button onClick={() => setIndex((value) => value + 1)}>{t('quiz.next')}</Button>
        )}
        {shown && index === total - 1 && (
          <Button onClick={() => setShowScore(true)}>{t('quiz.score')}</Button>
        )}
      </div>
      <SourceDialog open={sourceOpen} onOpenChange={setSourceOpen} sourceId={sourceId} />
    </div>
  )
}

function buildSubmission(
  prompt: QuizPrompt,
  optionId: string,
  optionIds: string[],
  textAnswer: string
): Submission | null {
  if (prompt.type === 'mc') {
    return optionId ? { type: 'mc', option_id: optionId } : null
  }
  if (prompt.type === 'multi') {
    return optionIds.length > 0 ? { type: 'multi', option_ids: optionIds } : null
  }
  const text = textAnswer.trim()
  if (!text) {
    return null
  }
  if (prompt.type === 'short') {
    return { type: 'short', text }
  }
  return { type: 'cloze', text }
}

function QuestionFields({
  prompt,
  optionId,
  optionIds,
  textAnswer,
  locked,
  shown,
  onOptionId,
  onOptionIds,
  onText,
}: {
  prompt: QuizPrompt
  optionId: string
  optionIds: string[]
  textAnswer: string
  locked: boolean
  shown: CardResult | null
  onOptionId: (value: string) => void
  onOptionIds: (value: string[]) => void
  onText: (value: string) => void
}) {
  const { t } = useTranslation()

  if (prompt.type === 'mc') {
    return (
      <RadioGroup value={optionId} onValueChange={onOptionId} disabled={locked}>
        {prompt.options.map((option) => (
          <div key={option.id} className="flex items-center gap-2">
            <RadioGroupItem value={option.id} id={option.id} />
            <Label htmlFor={option.id} className={optionMark(option.id, shown, option.id === optionId)}>
              {option.text}
            </Label>
          </div>
        ))}
      </RadioGroup>
    )
  }

  if (prompt.type === 'multi') {
    return (
      <div className="flex flex-col gap-2">
        {prompt.options.map((option) => {
          const checked = optionIds.includes(option.id)
          return (
            <label key={option.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={checked}
                disabled={locked}
                onCheckedChange={(value) => {
                  if (value === true) {
                    onOptionIds([...optionIds, option.id])
                    return
                  }
                  onOptionIds(optionIds.filter((id) => id !== option.id))
                }}
              />
              <span className={optionMark(option.id, shown, optionIds.includes(option.id))}>
                {option.text}
              </span>
            </label>
          )
        })}
      </div>
    )
  }

  if (prompt.type === 'short') {
    return (
      <div className="flex flex-col gap-2">
        <Label htmlFor="quiz-short">{t('quiz.yourAnswer')}</Label>
        <Textarea
          id="quiz-short"
          value={textAnswer}
          disabled={locked}
          placeholder={t('quiz.shortPlaceholder')}
          onChange={(event) => onText(event.target.value)}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="quiz-cloze">{t('quiz.yourAnswer')}</Label>
      <Input
        id="quiz-cloze"
        value={textAnswer}
        disabled={locked}
        placeholder={t('quiz.clozePlaceholder')}
        onChange={(event) => onText(event.target.value)}
      />
    </div>
  )
}

function optionMark(optionId: string, shown: CardResult | null, picked: boolean) {
  if (!shown) {
    return ''
  }
  const reveal = shown.reveal
  const correct =
    (reveal.type === 'mc' && reveal.correct_option_id === optionId) ||
    (reveal.type === 'multi' && reveal.correct_option_ids.includes(optionId))
  if (correct) {
    return 'text-success'
  }
  if (picked) {
    return 'text-destructive'
  }
  return ''
}

function ExplanationBlock({
  explanation,
  onOpenSource,
}: {
  explanation: Explanation
  onOpenSource: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2 rounded-md bg-muted p-3">
      <p className="text-sm">{explanation.explanation}</p>
      <p className="text-xs text-muted-foreground">{t('quiz.quote')}</p>
      <p className="text-sm">{explanation.quote}</p>
      <Button variant="link" className="h-auto justify-start px-0" onClick={onOpenSource}>
        {t('quiz.openSource')}
      </Button>
    </div>
  )
}

