
## Review quiz-neu (4398619..9c2a7ef), 19.10.
- Q-S1 (niedrig): setQueryData direkt in QuizDialog statt Hook in lib/hooks (frontend/AGENTS.md:17).
- Q-S2 (niedrig): noticePin.current wird im Render gelesen/geschrieben (QuizDialog.tsx:239-253).
- QuizDialog.tsx 1001 Zeilen (möglicher Large Class); Formular-Startwerte doppelt (Init/Reset), vier Checkbox-Blöcke doppelt.
- Q-P1 (niedrig): Listenlabel zeigt rohe Quiz-ID bei gleichen Einstellungen; Fix: laufende Nummer. Wartet auf Marcs Entscheidung.
- Q-P3: Spec sagt "leeres Formular", gebaut sind Startwerte (5 Fragen, mittel, alle Quellen). Marc bestätigt den Wortlaut.
- Test-Umgebung: vitest unter Node 26 braucht --localstorage-file, sonst bricht quiz-notice.test.ts ab ("reading 'getItem'"). Setup sollte das selbst abfangen.
