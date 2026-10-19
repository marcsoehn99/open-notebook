
## Review quiz-neu (4398619..9c2a7ef), 19.10.
- Q-S1 (niedrig): setQueryData direkt in QuizDialog statt Hook in lib/hooks (frontend/AGENTS.md:17).
- Q-S2 (niedrig): noticePin.current wird im Render gelesen/geschrieben (QuizDialog.tsx:239-253).
- QuizDialog.tsx 1001 Zeilen (möglicher Large Class); Formular-Startwerte doppelt (Init/Reset), vier Checkbox-Blöcke doppelt.
- [erledigt 040dc0c] Q-P1 (niedrig): Listenlabel zeigte rohe Quiz-ID bei gleichen Einstellungen. Jetzt „Quiz N · …“, ältestes = Quiz 1.
- [erledigt 340b462] Q-P3: Spec sagte "leeres Formular", gebaut sind Startwerte (5 Fragen, mittel, alle Quellen). Kriterium 1 nennt jetzt die Startwerte (von Marc bestätigt).
- Test-Umgebung: vitest unter Node 26 braucht --localstorage-file, sonst bricht quiz-notice.test.ts ab ("reading 'getItem'"). Setup sollte das selbst abfangen.
