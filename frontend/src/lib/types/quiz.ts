export type Difficulty = 'easy' | 'medium' | 'hard'
export type QuestionType = 'mc' | 'multi' | 'short' | 'cloze'

export interface QuizSummary {
  id: string
  status: string
  error_message?: string | null
  num_questions: number
  difficulty: Difficulty
  topic_prompt?: string | null
  language: string
  source_ids: string[]
}

export interface QuizOption {
  id: string
  text: string
}

interface PromptBase {
  question_id: string
  position: number
  stem: string
}

export interface McPrompt extends PromptBase {
  type: 'mc'
  options: QuizOption[]
}

export interface MultiPrompt extends PromptBase {
  type: 'multi'
  options: QuizOption[]
}

export interface ShortPrompt extends PromptBase {
  type: 'short'
}

export interface ClozePrompt extends PromptBase {
  type: 'cloze'
}

export type QuizPrompt = McPrompt | MultiPrompt | ShortPrompt | ClozePrompt

export type Reveal =
  | { type: 'mc'; correct_option_id: string }
  | { type: 'multi'; correct_option_ids: string[] }
  | { type: 'short'; accepted: string[] }
  | { type: 'cloze'; accepted: string[] }

export interface CardResult {
  correct: boolean
  reveal: Reveal
}

export interface PlayCard {
  prompt: QuizPrompt
  result: CardResult | null
}

export interface InProgress {
  kind: 'in_progress'
  answered: number
  total: number
}

export interface Finished {
  kind: 'finished'
  score: { correct: number; total: number }
}

export type QuizProgress = InProgress | Finished

export interface QuizReady extends QuizSummary {
  status: 'completed'
  cards: PlayCard[]
  progress: QuizProgress
}

export type QuizPlay = QuizSummary | QuizReady

export function isQuizReady(play: QuizPlay): play is QuizReady {
  return play.status === 'completed' && 'cards' in play
}

const QUIZ_POLL_MS = 2000

const QUIZ_ERROR_KEYS: Record<string, string> = {
  invalid_output: 'quiz.errorInvalidOutput',
  empty_source: 'quiz.errorEmptySource',
  timeout: 'quiz.errorTimeout',
  not_configured: 'quiz.errorNotConfigured',
  too_large: 'quiz.errorTooLarge',
}

export function quizRefetchIntervalMs(status: string | undefined): number | false {
  if (status === 'completed' || status === 'failed') {
    return false
  }
  return QUIZ_POLL_MS
}

export function quizIsFailed(status: string | undefined): boolean {
  return status === 'failed'
}

export function quizErrorDetail(
  translate: (key: string) => string,
  code: string | null | undefined
): string | null {
  if (!code || code === 'failed') {
    return null
  }
  const key = QUIZ_ERROR_KEYS[code]
  if (!key) {
    return null
  }
  return translate(key)
}

export type Submission =
  | { type: 'mc'; option_id: string }
  | { type: 'multi'; option_ids: string[] }
  | { type: 'short'; text: string }
  | { type: 'cloze'; text: string }

export interface CreateQuizRequest {
  source_ids: string[]
  num_questions: number
  difficulty: Difficulty
  topic_prompt: string | null
  question_types: QuestionType[]
  language: string
}

export interface Verdict {
  attempt_id: string
  question_id: string
  result: CardResult
  progress: QuizProgress
}

export interface Explanation {
  explanation: string
  quote: string
  source_id: string
}
