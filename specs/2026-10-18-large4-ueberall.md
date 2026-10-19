# Large 4 überall, Qualität gemessen

## Ziel
Danach läuft im Portal überall Large 4, wo bisher Mistral Small oder Medium im Einsatz war. Wenn etwas länger dauert, sieht Marc, was gerade passiert, oder bekommt Bescheid, sobald es fertig ist. Ob die Qualität stimmt, zeigt eine Messung in Langfuse.

## Nutzerablauf
1. Marc stellt eine Chatfrage oder startet eine Transformation. Die Antwort kommt von Large 4.
2. Marc startet ein Quiz. Er sieht einen Fortschritt, der wirklich etwas aussagt, oder arbeitet weiter und bekommt eine Meldung, sobald das Quiz fertig ist.
3. Marc öffnet Langfuse und sieht für Chat und Quiz die Werte aus einem festen Eval-Set, Medium vorher und Large 4 nachher nebeneinander.

## Akzeptanzkriterien
1. Jeder Aufruf, der bisher an Mistral Small oder Medium ging (Chat, Transformationen, Tools), geht an `mistral-large-4`. In Langfuse taucht danach kein Small- oder Medium-Aufruf mehr auf.
2. Embeddings, OCR, Sprache-zu-Text und Text-zu-Sprache bleiben unverändert.
3. Beim Quiz zeigt die Oberfläche spätestens nach 2 Sekunden einen Fortschritt oder die Meldung "läuft im Hintergrund". Ist das Quiz fertig, erscheint ein Hinweis, den man anklicken kann.
4. Keine Aufgabe endet wegen des 300-Sekunden-Timeouts mit "failed". Das wird mit einer großen Quelle getestet.
5. In Langfuse gibt es für Chat und für Quiz je ein Eval-Set. Die Medium-Vergleichswerte werden aufgenommen, BEVOR der Server auf Large 4 umgestellt wird.
6. 100 % der Quiz-Zitate stehen wörtlich in der Quelle.
7. Large 4 erreicht in beiden Eval-Sets mindestens den Medium-Vergleichswert.
8. Das Nachdenken wird nur dort gekürzt, wo die Kriterien 6 und 7 danach weiterhin erfüllt sind. Standard ist: nicht kürzen.
9. Ist bei einem Quiz nur eine Frage fehlerhaft, wird nur diese neu erzeugt und nicht das ganze Quiz.
10. Die Antworten von Medium und Large 4 werden in beiden Eval-Sets von `cursor-agent` (Grok-Modell aus dem Abo) nach denselben Kriterien bewertet. Weder Medium noch Large 4 bewertet sich selbst. Die Werte stehen als Scores in Langfuse. Der Judge sieht bei keiner Antwort, ob sie von Medium oder von Large 4 stammt. Innerhalb jedes Paars wird die Reihenfolge zufällig gemischt. Jede Bewertung läuft in einer neuen Session, nicht in der Bau-Session. Zu jedem Score in Langfuse wird gespeichert, welches Judge-Modell ihn vergeben hat.
11. Gibt die Quelle für die bestellte Fragenzahl nicht genug her, darf das Quiz kürzer ausfallen.

## Bewertungsschema
Skala 1 bis 5: 1 = falsch oder erfunden, 3 = brauchbar mit Mängeln, 5 = korrekt, belegt und klar.

Chat, pro Antwort:
- Belegt: Jede Aussage ist durch die Quelle gedeckt. Steht eine erfundene Tatsache drin, gibt es hier eine 1.
- Richtig: Die Antwort ist inhaltlich korrekt.
- Vollständig: Sie beantwortet die Frage ganz.
- Verständlich: Sie ist klar und auf den Punkt.

Quiz, pro Frage:
- Quellenbezug: Die Frage lässt sich nur mit dem Inhalt der Quelle beantworten.
- Richtige Antwort: Genau eine Antwort ist eindeutig korrekt.
- Falsche Antworten: Sie klingen plausibel, sind aber eindeutig falsch.
- Erklärung: Sie stimmt und verweist auf die richtige Stelle.
- Zusätzlich prüft das Skript, dass 100 % der Zitate wörtlich in der Quelle stehen.

Bestanden: Der Large-4-Durchschnitt ist mindestens so hoch wie der von Medium, und kein einzelnes Kriterium ist um mehr als 0,3 Punkte schlechter.
- Der Judge bewertet jedes Paar zweimal. In Langfuse stehen pro Kriterium der Durchschnitt und die Schwankung zwischen den beiden Bewertungen.
- Liegt der Unterschied zwischen Medium und Large 4 innerhalb dieser Schwankung, gilt er als Gleichstand und nicht als Verschlechterung. Die Schwelle von 0,3 Punkten gilt nur für Unterschiede außerhalb der Schwankung.

Gegenprobe: Marc bewertet 3 Paare selbst. Weicht der Judge im Schnitt um mehr als 1 Punkt von Marc ab, wird das Schema nachgeschärft, bevor die Ergebnisse gelten.

## Nicht-Ziele
- Kein Modellwechsel bei Embeddings, OCR, Sprache-zu-Text und Text-zu-Sprache.
- Keine Tempoziele auf Kosten der Qualität. Lange Wartezeiten sind in Ordnung, solange sie sichtbar sind.

## Offene Fragen
- Wie groß werden die Eval-Sets, und was kostet ein Mistral-Lauf? Den Betrag nennt Dev-Bot Marc vorher.
- Laut Dev-Bot läuft die Quiz-Erzeugung im Backend schon als Hintergrundjob. Fortschrittsanzeige und Fertig-Meldung im Frontend fehlen noch.
- Dabei gehen Notebook-Inhalte an Cursor/Grok. Falls dort personenbezogene Daten drin sind, muss Compliance-Bot das prüfen. Vorschlag: für die Eval-Sets nur Demo-Inhalte verwenden, zum Beispiel das DEMO Quiz Lab.
