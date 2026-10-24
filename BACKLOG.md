
## Review quiz-neu (4398619..9c2a7ef), 19.10.
- Q-S1 (niedrig): setQueryData direkt in QuizDialog statt Hook in lib/hooks (frontend/AGENTS.md:17).
- Q-S2 (niedrig): noticePin.current wird im Render gelesen/geschrieben (QuizDialog.tsx:239-253).
- QuizDialog.tsx 1001 Zeilen (möglicher Large Class); Formular-Startwerte doppelt (Init/Reset), vier Checkbox-Blöcke doppelt.
- [erledigt 040dc0c] Q-P1 (niedrig): Listenlabel zeigte rohe Quiz-ID bei gleichen Einstellungen. Jetzt „Quiz N · …“, ältestes = Quiz 1.
- [erledigt 340b462] Q-P3: Spec sagte "leeres Formular", gebaut sind Startwerte (5 Fragen, mittel, alle Quellen). Kriterium 1 nennt jetzt die Startwerte (von Marc bestätigt).
- Test-Umgebung: vitest unter Node 26 braucht --localstorage-file, sonst bricht quiz-notice.test.ts ab ("reading 'getItem'"). Setup sollte das selbst abfangen.
- Sicherheit (19.10.): Laut quiz-liste-Lauf startet `cursor-agent --force` im Modus "Run Everything"; prüfen, ob die deny-Liste in ~/.cursor/cli-config.json dann noch greift (harmloser Test mit gesperrtem Befehl), bevor der nächste --force-Bau startet.

## sec-deps-2 (24.10.)
- Branch sec-deps-2: filelock 4.0.9 -> 4.2.4 (>=4.2.1), behebt GHSA-v4x8-3wr5-3wq8, GHSA-9vhv-jxww-xfm5, GHSA-567q-5qvw-pf92; virtualenv 21.14.9 -> 21.14.13, behebt GHSA-cx6f-vjhg-g3cc, GHSA-4f8h-vr72-p32c. Hinweis: GHSA-394h-qwmc-48pf und GHSA-6w3r-xr62-4xcp waren schon in 21.14.9 behoben.
- Untergrenze filelock>=4.2.1 in [tool.uv] constraint-dependencies.
- Offene Punkte:
  - CHANGELOG-Eintrag unter ### Security fehlt (AGENTS.md:35).
  - Commit-Message d9d1e1a ist kein Conventional Commit und nennt 4.2.1 statt 4.2.4; Vorschlag PR-Titel `chore(deps): bump filelock to 4.2.4 and virtualenv to 21.14.13`.
  - Eigenes Ticket: langgraph-checkpoint-sqlite 3.1.1 GHSA-6hmw-9432-67qq (Fix 3.1.2) und fastmcp 3.2.0 GHSA-7xgj-8392-5q4r (Fix 3.4.10 / 4.1.2).
- Status: wartet auf Review/Marc.
