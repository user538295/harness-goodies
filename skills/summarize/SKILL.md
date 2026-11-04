---
name: "summarize"
description: "Explain the findings and key lessons of supplied content (pasted text, URL or attached file) within a requested reading time, or by default in at most 24 lines of at most 255 characters each."
---

# Summarize

Read the supplied content and explain its main findings so the user understands them and learns from them. Answer in the user's language unless requested otherwise.

## Reading the source
- Read the actual content using tools suited to its format, including relevant figures and tables. Do not summarize from a title or search snippet.
- If access fails, try another way to read the same source. If only part is readable, state what is missing and summarize the accessible part. If nothing is readable, ask for an accessible copy instead of guessing.

## Content
- Select the findings that matter most to understanding the central message; do not give every section equal space or sacrifice explanation to cover more points.
- Explain the findings, not just list them: what do they mean, why or how does the source support them, and what can the reader learn from them? Explain unfamiliar concepts in plain language and use a short example when it helps. Make clear when an example is your own illustration.
- Base claims on the source. Distinguish findings from the author's opinions and your own inferences. Preserve uncertainty and limitations that affect the meaning. Do not invent missing facts.

## Structure
- First line: the central finding or message in one sentence.
- Then the key findings, most important first, each with its meaning and support.
- Last line(s): the takeaway, what the reader should remember or apply.
- No introduction or closing offer.

## Length
- If the user specifies a reading time, use it as an approximate upper limit instead of the line and character limits. Estimate roughly 200 words per minute (up to about 600 words for 3 minutes), using fewer words for difficult concepts. Do not pad the answer to fill the time.
- Otherwise, use at most 24 newline-separated lines, each at most 255 characters including spaces, bullets, and citations. These limits cover the entire answer, even with multiple sources. Use fewer lines when enough.

## Before sending
Check that the answer explains the findings and their lessons rather than merely listing claims. Check accuracy against the source. Use a tool when available to check the word count against the requested reading time, or the line and character counts when no time was specified. Shorten as needed without losing essential meaning, then check again.