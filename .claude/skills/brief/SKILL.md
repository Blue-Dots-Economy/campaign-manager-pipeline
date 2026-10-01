---
name: brief
description: Answer as a precise summary - lead with the answer, keep it short, no preamble. Use when the user asks to summarise, says "concisely", "briefly", "short version", "TLDR", "I didn't understand", "keep it simple", or is re-asking because a previous answer was too long.
---

# Answer briefly

Lead with the answer. Cut everything that is not load-bearing.

## Shape

1. **The answer, first line.** Not context, not what you did, not "I looked into X".
2. **The numbers that justify it**, as a short table or 3-5 bullets.
3. **What the user must decide or do**, if anything. One line.

Target 150 words. Hard stop at 250 unless the user asked for detail.

## Cut these

- Preamble: "Good question", "Let me explain", "So basically"
- Narration of your own process: "I checked X, then queried Y, then found Z".
  Give the finding, not the journey.
- Restating the question back
- Caveats the user already knows
- Offers of further work, unless genuinely blocking. One closing line at most.
- Repeating a number you already gave in the same answer

## Keep these

- Exact figures. "1,326 rows" not "about a thousand".
- Column, table, file and command names, verbatim.
- A correction, if something you said earlier was wrong. Say it plainly in one
  sentence - never bury it and never pad it with apology.
- The one thing that blocks progress.

## Format

Tables beat prose for anything with more than two numbers. Code blocks for
commands. Bold only the single most important figure, if any. No section
headings under ~200 words - they cost more than they organise.

## When the user says "I didn't understand"

They are not asking for the same thing restated shorter. They are telling you
the framing was wrong. Change the framing:

- Drop every term specific to this system on the first pass
- Use a concrete example with real values instead of describing a rule
- Say what it means for them, not how it works
- Three or four short lines, then stop

A second "I didn't understand" means stop explaining and ask which part.

## Never

- Do not summarise by dropping bad news. A shorter answer must still carry the
  failure, the caveat, and the number that is smaller than hoped.
- Do not turn a precise figure into a vague one to save words.
