---
name: speech-condense
description: Cut a rambling talk down to a tight version that still sounds like one continuous take. Use for condensing a podcast, interview, panel, livestream recording, lecture, or verbose piece to camera; for producing a short highlight version of a long conversation; for removing filler, repetition and dead air from unscripted speech; or when asked to hit a target length ("make this 3 minutes"). Covers ASR, sentence-level indexing, keep-list selection, pause-aware cut placement, jump-cut treatment, and evidence-based verification of every join.
---

# Speech Condense

Turn a long, unscripted talk into a short one that a viewer would not guess was edited. Two things must be true at the end, and they fail independently:

- **It still says something.** The condensed version has to stand on its own — an argument that survives, not a bag of sentences that were individually good.
- **It does not sound cut.** Every join has to land where a listener expects a breath, and look like something a camera operator would do.

The job is finished when you can point to the evidence for both. Not when the encoder exits zero, and not when the duration matches the target.

## What Makes This Hard

- **A cut is not local.** Removing a sentence changes what the sentence *after* it means — drop the question and the answer answers nothing. Each cut can be clean and the result still incoherent; `condense_plan` flags the detectable cases and `out/condense_script.md` catches the rest (Hard Rule 3).
- **The blade lands mid-syllable unless you make it not.** Boundaries inside a segment are derived, and 60 ms off clips a consonant — which is why boundaries snap onto detected silence and why word-level timestamps change what is safe to attempt.
- **Cut count matters more than cut length, and a jump cut in a static shot is visible.** Every join is a chance to look spliced; fewer, longer runs is the fix — a property of the keep-list, not the renderer. Which treatment fits depends on the footage (Phase 2).
- **Under-cutting is a failure too.** Coming in far under target usually means whole sections went missing, not that the edit is tight.

## Hard Rules

1. **Index before you select.** Run `condense_index` and read the unit table *and* its visual survey. Deciding what to keep from the raw transcript throws away the pause structure, the repetition detection, and the duration of everything you are choosing between.
1b. **Do not buy a dense visual pass for a local question.** The survey's constant thumbnail budget plus the transcript's own on-screen references (`V` flags) are the visual evidence this pipeline needs. `video_ingest` at 2 fps on a long recording is a large cost for an answer condensing usually does not use. `video_watch_segment(video_path=...)` and `video_read_frames` are the targeted tools: use tight batched windows or single-frame reads instead of sweeping the whole video.
2. **Check the pause-detection verdict before planning.** Every cut point is snapped to a detected silence. If `condense_index` reports `threshold_too_high` or `threshold_too_low`, fix `silence_db` and re-index first — otherwise the cuts are placed by arithmetic and no later step will tell you.
3. **Read `out/condense_script.md` straight through before rendering.** It is the condensed talk as continuous prose with every removal annotated. It is the only cheap way to catch an edit where every boundary is clean and the result no longer makes sense.
4. **Prefer runs over scatter.** Express the keep-list as ranges (`"u012-u031"`). Consecutive units produce no cut at all.
5. **Never edit the transcript to make the edit work.** You are choosing among things the speaker said. Reordering sentences, splicing half of one sentence onto half of another, or keeping a clause that reverses the speaker's meaning is misrepresentation, not condensing. If the honest cut cannot hit the target, say so in the brief.
6. **Read every join evidence row `condense_qc` returns.** Each row is the only proof that its cut is clean. The waveform row and the frame row answer different questions; read both.
7. **A blocking error is blocking.** A cut inside a word, or an output duration that disagrees with the plan, ships an audible defect. Fix it; do not park it.
8. **Verification is append-only.** Every checkpoint verdict goes into `out/condense_verify.md` as a new line. Never rewrite an old line.
9. **Do not wait for confirmation.** If the target length or the audience is unspecified, choose, and record the assumption in `out/condense_brief.md`.

## File Contract

Everything under `out/` relative to the project directory. Conversation text is not progress.

| File | What | When |
|---|---|---|
| `out/media.json` | Source probe from `inspect_media`. | First. |
| `out/transcript.json` | ASR transcript with segment-level times (`words` is always empty with the local ASR). | Before indexing. |
| `out/speech_index.json` + `.md` | Unit table, disfluency spans, pause analysis, time budget, **and the coarse visual survey** (constant thumbnail budget, shot-cut count, motion baseline). **Satisfies the visual-inspection requirement on its own.** | Before selecting. |
| `out/video_ingest.json` | Dense 2fps sweep. Rarely needed and expensive — only when you must see content *between* the survey samples. | Almost never. |
| `out/condense_brief.md` | Target length and its justification, what counts as essential for this piece, the keep strategy, the checkpoint list. | Before `condense_plan`. |
| `out/condense_plan.json` | Clips, joins, continuity flags, duration accounting. | Before render. |
| `out/condense_script.md` | The condensed talk as prose with removals annotated. Written by `condense_plan`; **must be read**. | Before render. |
| `out/condensed_transcript.json` | Transcript remapped into output time. Written by `condense_plan`. Use it if you go on to subtitle the cut. | Automatic. |
| `out/condensed.mp4` | The condensed cut. | Before verification. |
| `out/condense_qc_report.json` | Deterministic QC over the current plan and output. | After render. |
| `out/condense_verify.md` | Append-only checkpoint verdict log. | Throughout verification. |
| `out/report.md` | Closeout: assumptions, outputs, parked items, residual risk. | Last. |

## Workflow

### Phase 1: Intake

`inspect_media(input_path)` — duration, resolution, orientation, audio streams.

`speech_transcribe(input_path)` — the local ASR runs the transcription; check what you got. It provides **segment-level timestamps only** (word-level timing is a Moss model limitation and never appears), so cut points cannot be proven to land between words: plan cuts on segment boundaries, rely on silence snapping, and treat every content cut as needing a listening checkpoint. If the local ASR is unavailable, continue only after recording the limitation.

**Do not guess a language.** The local model detects language itself; the `language` argument is recorded but does not affect recognition. Speaker diarization runs whenever `diarize=true` (the default). After speech_transcribe, inspect the transcript metadata (`provider`, `language`, `speaker_labels`, `segments[].speaker`) before doing anything else; if a multi-speaker piece came back without speaker labels, re-run `speech_transcribe` and make sure `diarize` was not disabled.

Then sanity-check, because everything downstream is built on this text:

- Do the segment times span the whole recording, or does the tail cut off?
- Is `n_speakers` what you expect? A solo piece split into three speakers means noisy audio; an interview collapsed into one means diarization failed and you lose the speaker-change signal.
- Are there hallucinated loops? ASR repetition and the speaker's own repetition look identical in text and mean opposite things — one is a bug to ignore, the other is your best cut candidate.

### Phase 1b: Check speaker attribution (multi-speaker only)

ASR speaker labels are the least reliable thing transcription returns: the local model gets the words right but its diarization can collapse on remote-mixed or noisy audio (a verified case tagged an interviewer and their guest as one speaker). Before trusting any `speaker` field, run `diarize_audit(transcript_path)`. It decides nothing — it hands you an evidence brief: `speaker_share` (a lopsided share on a dialogue is the classic collapse tell), a `roster` of clean sample windows per existing label, `name_cues` (self-intros, hand-offs — candidates, not answers), `suspect_flags` / `watch_windows`, and a soft `verdict` (`likely_collapsed` / `worth_checking` / `no_obvious_conflict` / `no_speaker_labels`). `no_obvious_conflict` is not a certificate; the windows are offered regardless — if speaker identity matters to your cut, look anyway.

The decision is **yours, and it is visual**: `video_watch_segment(video_path=..., fps=4, segments=[...])` on tight windows, batched into one call. When the question is *what does that caption say*, switch to `video_read_frames` at source resolution — a 4-up sheet cell is ~392px wide, marginal for a ~10px CJK name plate, exactly where a model stops reading and starts confabulating a plausible-looking name that then poisons every label downstream. If the plate is still illegible there, that is the answer: use a neutral label.

What separates a speaker from a listener — a listener's mouth moves too (laughs, an `嗯`, reactions), so one open-mouth frame is not evidence of anything:

- **Sustained change beats any single frame.** A speaker's mouth changes shape on nearly every sampled frame for as long as the transcript says they are talking; a listener's opens once and returns to rest. Judge the pattern across the whole window, never one cell.
- **fps=4 is the floor for lip motion**; at 2 fps a talking mouth and a yawning one look identical. Keep windows short (3–6 s) and spend the frames on rate, not duration.
- **Match the motion against the ASR text** — free and decisive, because `video_watch_segment` attaches each window's transcript. A 5-second question with one second of mouth movement means someone off-frame is speaking; the syllable count should roughly fit the duration; if nobody's mouth moves over transcribed speech, do not force a choice (off-camera speaker, B-roll insert, or offset window).
- **Watch across a turn boundary, not inside a turn** — the mouth that stops and the mouth that starts in one window beat any mid-answer look.
- **When lips are ambiguous, other cues outrank them**: a platform active-speaker highlight settles it outright; then mic lean, gestures beating with emphasis, gaze at turn starts, and the source's own burned-in captions (dash/colour changes at turn changes).
- **Inconclusive is a real verdict.** Do not average the evidence into a confident label; keep the neutral one and note it — a wrong attribution silently corrupts every downstream `speaker_change` check.

**Keep one person to one label.** Build the cast first from the `roster` sample windows (label → where they sit / what they look like / any name caption), then reuse that label on sight; mint a new one only when the speaker matches nobody on the list. A webcam grid makes identity trivial (same box, same person); a single camera means tracking by face, clothing, seat, or an on-screen name. For `name_cues`, the picture is the arbiter: `self_intro` names the person **speaking in that window**; `handoff` names who **speaks next**, not the announcer. Adopt a name only when you can tie it to a specific speaker you see — a merely spoken name may belong to someone off the call. When no name survives the check, use a stable neutral label (`speaker A` / `主持人` / `嘉宾`): an honest A/B beats a confidently wrong name.

Collect `{start, end, speaker}` for each stretch and call `diarize_relabel(assignments=[...])`; it rewrites the labels and **never touches the text**. Re-run `diarize_audit` to confirm. You do not have to re-attribute every second — cover the stretches you will keep; if you skip the repair, say so in the brief and treat `speaker_change` silence as uninformative rather than as a pass.

### Phase 2: See The Frame — cheaply, once

**`condense_index` (Phase 3) does the whole visual pass for you.** It samples a *constant-budget* handful of thumbnails — 3 base frames plus one per detected shot change, capped at 32, regardless of duration — and returns them as one sheet, together with the shot-cut count and a measured frame-to-frame **motion baseline**. So Phase 2 is not a separate call; it is a thing you read in Phase 3's output.

This is a deliberate budget decision, not a shortcut. A 2 fps `video_ingest` of a long recording costs orders of magnitude more and answers a question condensing does not ask: **what to keep is decided from the text.** The picture only has to settle two things.

1. **How visible will your cuts be?** The motion baseline says it, and it is the same baseline QC measures each rendered cut against:
   - `locked_off` — near-static shot. Every cut will jump against that stillness, so keep long consecutive runs (fewer cuts). There is no zoom/effect fix — fewer cuts is the fix.
   - `moderate_motion` — noticeable but not glaring; decide per cut from the QC jump ratio.
   - `busy` — your cuts hide among the footage's own motion; straight cuts are fine.

   The shot-cut count matters alongside it: footage that already cuts between framings hides your cuts among its own.

2. **Is there burned-in text or a graphic?** Read the sheet for this — it is not measured. A source that carries burned-in captions, a lower third or a logo will have them **jump mid-sentence at every cut you make**, and that is a defect a viewer notices even when the picture itself matches.

**Do not add a dense pass on top.** `video_ingest` is for the rare case where you genuinely must see content *between* the survey samples, and `subtitle_scout` is the wrong shape here entirely — half its output is a caption-band strip that condensing has no use for.

**Where the text says to look.** Anything worth seeing leaves a trace in the transcript, and `condense_index` finds those traces: units containing an on-screen reference ("like this", "as you can see", "看这里", "这个动作") are flagged **V** and listed as `visual_check_timestamps`. Those are the only places a targeted look pays for itself, because the words alone do not carry them — dropping such a unit discards whatever was being shown, and keeping it means the frame has to still make sense after your cut. Batch them into **one** low-fps `video_watch_segment(video_path=...)` call. Do not sweep the whole recording looking for highlights the text never mentions.

### Phase 3: Index

`condense_index(video_path, transcript_path)`. Read the returned unit table — the whole thing, from `out/speech_index.md` if it was truncated. A keep-list built from a truncated view silently drops the tail of the video. This is also where the visual survey of Phase 2 arrives, as a thumbnail sheet plus the motion baseline; read it here.

Four things in the output decide what happens next:

1. **The pause-detection verdict.** `plausible` means boundaries will find real breaths. `threshold_too_high` means quiet speech is being counted as silence and cuts will snap *into* words; `threshold_too_low` or `no_pauses_found` means the breaths are invisible and boundary times will be derived. Both are fixed by re-indexing with `silence_db="auto"` or a hand-picked value — do that now, not after the first bad render.
2. **The lossless floor.** The duration reachable by tightening pauses and removing hesitation sounds *without dropping a sentence*. If the target is above the floor, this job needs almost no editorial judgement. If it is far below, most of the reduction has to come from content, and the brief has to say which content.
3. **The picture** — the motion baseline, the shot-cut count, and the thumbnail sheet (Phase 2 above), plus `visual_check_timestamps` if any unit points at something on screen.
4. **The flags and the trim candidates.** `D` (restates an earlier unit) is where unscripted speakers hide the most removable time, and it is the hardest thing to notice reading linearly. `F`/`s` mark disfluency. `!` marks a run-on with no sentence-final punctuation — cutting at its end will land mid-thought. `~` marks interpolated timing.

### Phase 4: Brief

Write `out/condense_brief.md`. It records the decisions and the claims you will later have to prove.

1. **The target, and why.** If the caller gave one, restate it and say whether it sits above or below the lossless floor. If they did not, choose one from the material and justify it — the honest question is how much of this recording is genuinely redundant, and the answer differs by an order of magnitude between a scripted read (little fat; 80–90% is often all you can take without losing content) and an unstructured conversation (40–60% is routine, and the result is usually better than the original). Do not pick a round number because it sounds decisive.

2. **What counts as essential for *this* piece.** This is the editorial core and it is yours. Weigh:
   - **the claim and its support** — the sentence that states the point, plus the one concrete thing that makes it land (a number, a name, a worked example). One good example beats three;
   - **structure a listener needs** — the question a kept answer answers; the setup a kept punchline needs; the "first" that a kept "second" depends on;
   - **what is genuinely repeated** — a speaker circling back to a point already made is the cheapest content to cut, and `D` flags find most of it;
   - **preamble and meta-talk** — "let me give some background first", "as I was saying", "does that make sense" — almost always removable, and usually at the start of a unit rather than as a whole unit;
   - **tangents that never return** — a digression that connects to nothing later is a clean lift.

   Whatever you drop, the speaker's position must survive intact. Cutting the qualification off a hedged claim makes them say something they did not say.

3. **The keep strategy, in ranges.** Aim for the fewest, longest runs that carry the content. Note explicitly where you are accepting a cut and why the discontinuity is tolerable there — a speaker change, a topic shift, and a full stop before a new paragraph are the places a listener already expects one.

4. **The checkpoint list — what you will verify, and with what.** List the claims that, if false, would make you reject the deliverable. Cover the whole cut, not a sample. Tag each with the evidence that settles it:

   - **S** — settled by *reading* `out/condense_script.md`: coherence, no orphaned connective, no dangling reference, the argument survives, the speaker is not misrepresented.
   - **A** — settled by the *audio* evidence: the dB triple and waveform `condense_qc` returns at each join, escalating to a listen if inconclusive. Clipped words, clicks, abrupt level jumps.
   - **V** — settled by the *frames* `condense_qc` returns either side of each join: how visible the jump is, whether burned-in graphics jump at the cut.

   When a still or a number is inconclusive, escalate to `video_watch_segment(video_path="out/condensed.mp4", ...)` on the rendered output at high fps over that join. Anything about motion across the cut needs a window, not a frame.

### Phase 5: Select and Plan

`condense_plan(keep=[...], target_duration=...)`.

Express the keep-list as ranges. Use `drop=[...]` to punch a few units out of a long range rather than enumerating around them. Use an explicit `{"start":…, "end":…}` only when you genuinely need a boundary that is not a unit edge — for example to start a clip *after* a connective, which is the cleanest fix for an orphaned "所以".

Options worth a deliberate choice:

- **`tighten_pauses`** (on by default) is free compression — dead air removed without touching content. Leave it on unless the pauses are doing rhetorical work.
- **`max_gap`** is the longest pause kept inside a run. Lower is tighter and more energetic; too low and the delivery sounds hurried and unnatural.
- **`drop_fillers`** is `"hard"` (hesitation sounds and stutters) or `"aggressive"` (also standalone soft fillers). It requires word-level timestamps, which the local ASR never provides (Moss model limitation) — `condense_plan` **always refuses it** with this backend. Remove fillers by dropping whole units instead.
- **`lead_out` > `lead_in`** by default, and that asymmetry is deliberate: a beat of silence after the last word is what makes a cut read as "the thought finished".
- **`min_clip`** guards against sub-second clips, which read as a stutter in the edit rather than as a sentence.

Then read what the tool returns, before rendering:

- **Errors are blocking.** A `mid_word_cut` will be audible.
- **The snap ratio.** How many boundaries landed in a detected silence. A low ratio means the rest were placed by arithmetic — those joins need A checkpoints.
- **The flagged joins.** Each flag names what it thinks is broken and how to fix it. `orphan_connective_in`, `broken_reference_in`, `answer_without_question` and `mid_thought_out` are all keep-list problems, so fix them in the keep-list and re-plan; they will not improve at render time.
- **The target verdict**, and `next_moves` if you missed: which kept units carry the least content per second, and which dropped units carry the most.

### Phase 6: Read The Script

Read `out/condense_script.md` from top to bottom as if you were the viewer. This is a distinct step, not a formality, and it is the one that catches what nothing else can.

Ask, at every marked cut: does the next sentence follow from the last one? Does anything reference material that is gone? Does the speaker still mean what they meant? Does the whole thing arrive somewhere?

If the answer is no, the fix is the keep-list. Go back to Phase 5.

### Phase 7: Render

`condense_render(plan_path)`. Choose the join treatment from what Phase 2 showed you:

- **`join="hard"`** (default) — straight cuts. Right for essentially all talking-head/podcast material. If cuts feel jumpy, the fix is fewer, longer runs, not an effect.
- **`join="dissolve"`** — a short cross-dissolve with a matching audio cross-fade. Softer, and appropriate for slow or formal material. On a talking head it often reads as a mistake rather than a style, and it cannot be used above 40 clips or when a clip is shorter than twice the dissolve. Do not reach for it as a default fix for jumpiness; fewer cuts is the better fix.

A successful encode proves the filter graph ran and nothing else.

### Phase 8: Verify

`condense_qc(plan_path, video_path="out/condensed.mp4")`.

Errors are blockers. Warnings need a conscious verdict — accepted with a reason, or fixed.

Then settle every checkpoint:

- **S checkpoints** — from the script reading in Phase 6, re-read after any keep-list change.
- **A checkpoints** — read the waveform row and the dB pair for each sampled join. QC measures the level in the 80 ms immediately either side of the cut against the file's own mean, and reports `in_pause` (quiet both sides — the cut sits in a breath), `one_side_quiet` (fine), or `in_speech` (full speech energy both sides, so there is no breath there). On a **content cut**, `in_speech` is either a clean word-to-word splice or a clipped syllable, and only listening separates them — spend a window on it. On a **filler excision** it is expected by design and is not flagged. Raise `max_evidence_joins` when there are more flagged cuts than samples.
- **V checkpoints** — read every frame row: two frames before the cut, two after. Judge whether a viewer would notice the jump — pose, gaze, hand position, framing, and any burned-in graphic. A small jump in a two-hour podcast is nothing; the same jump three times in twenty seconds is a broken edit.
- **Anything inconclusive** — `video_watch_segment(video_path="out/condensed.mp4", fps=..., segments=[...])` at high fps over the join, batching windows into one call. Spend tight windows on the first and last cut, joins the plan reported as not snapped to silence, every content cut measured `in_speech`, and any cut where the frames looked wrong. Do not run repeated dense sweeps of the output.

Append one line per checkpoint to `out/condense_verify.md`: the id, its S/A/V tag, the evidence used, and the verdict.

**When there are many cuts, triage — and say that you did.** Verification work scales with cut count, and on a long recording it can exhaust your budget before the closeout files are written (measured: a 37-minute source condensed to 12 minutes produced 66 cuts and ran out of turns mid-verification, leaving no `condense_verify.md` and no `report.md` — an unverifiable deliverable). Above roughly twenty cuts, do this instead of a uniform sweep:

1. **Verify exhaustively** every cut the tools flagged — plan errors first, then continuity warnings, `in_speech` content cuts, and `severe`/`scene_change` jumps. These are where defects actually live, and `condense_qc` already spends its evidence budget on them.
2. **Sample the rest** — the first cut, the last cut, and a spread of clean ones. A `subtle` jump with an `in_pause` verdict has two independent measurements saying it is fine.
3. **Record the sampling in `out/condense_verify.md` as its own line**: how many cuts, how many verified individually, how many covered by sampling, and which measurements you relied on for the remainder. A silent partial verification reads as a complete one, which is worse than an honest sample.
4. **Write the closeout files before you run out of room.** `condense_verify.md` and `report.md` are the deliverable's only record of what was checked; a beautiful cut with no verification log cannot be shipped or trusted.

If the cut count itself is what makes verification unaffordable, that is a signal about the edit, not about the budget: consolidate the keep-list into fewer, longer runs. Fewer cuts is better viewing *and* less to prove.

Note that ingesting `out/condensed.mp4` makes it the active video; pass the original path explicitly to go back to the source.

### Phase 9: Repair Loop

Fix at the level that caused the failure. Most condensing defects are keep-list defects wearing a render costume.

| Symptom | Fix at |
|---|---|
| The cut does not make sense as speech | the keep-list — restore the antecedent, question, or setup, then re-plan |
| A join opens on "所以"/"但是" with nothing behind it | keep the prior unit, or start that clip past the connective with an explicit `{start,end}` |
| Cut lands mid-word (`mid_word_cut`) | raise `snap_window`, or move the boundary to a unit edge |
| Join sounds abrupt; content cut measured `in_speech` | `silence_db="auto"` and re-index, raise `snap_window`, or pick a different boundary |
| Sounds hurried, no room to breathe | raise `max_gap`, raise `lead_out` |
| Choppy — too many cuts per minute | consolidate the keep-list into longer runs |
| Visible jump cuts | fewer cuts (consolidate the keep-list into longer runs). `join="dissolve"` only for slow/formal material |
| Over target | `next_moves.kept_units_worth_re_examining`, then re-plan |
| Under target | `next_moves.dropped_units_with_the_most_content`, then re-plan |
| Output duration disagrees with the plan | re-render; do not ship it. Something was dropped or repeated in the join |

Re-run QC and re-verify the affected checkpoints after every change. Append the new verdicts; do not edit the old lines.

**Stop-loss.** Three repair rounds on one checkpoint without resolution, and you may park it: append a `parked` verdict stating what you tried and what remains wrong. A checkpoint you never verified may never be parked. A blocking error may never be parked.

### Phase 10: Report

Write `out/report.md`: the task, the target and whether it was met, what was dropped and on what principle, the join treatment and why, QC status, every parked checkpoint with its reason, and residual risks.

## Also Subtitling?

Condense first, then subtitle the **rendered condensed cut** — subtitling the source and then cutting puts every cue at the wrong time. `condense_plan` writes `out/condensed_transcript.json`, the transcript remapped into output time with its derived unit timings preserved (segment-level only with the local ASR), so the subtitle pass does not need a second ASR run. Check its `partial=true` segments first: a unit split by a cut appears twice.

**First check whether the source already has burned-in captions.** The visual survey's thumbnail sheet shows this, and it is the thing to look for before agreeing to subtitle at all. If the source carries its own burned-in text, the condensed cut carries it too — so adding a second caption track **double-prints**: two sets of text at nearly the same height, overlapping into something illegible. Verified on a real interview: `broadcast` margins landed the new cues directly on top of the source's existing captions, and the QC evidence frames showed the two texts garbling each other.

So when the source is already captioned, the honest answers are, in order: **don't subtitle it** (the deliverable already has captions); or, if a second track is genuinely needed (a translation, a different language), place it clear of the existing band — raise `margin_v` well above the source's captions, or put it at the top with `alignment` — and prove it on the QC evidence frames, not from the plan.

Also carry over what the survey told you about cut-time behaviour: burned-in source captions **change at your cuts**, so a caption can appear to jump mid-sentence in the condensed cut even when the picture matches. That is a defect of the condense step, not the subtitle step, and it is visible in the join frame rows.

## Tool Policy

- `inspect_media` — required for the source.
- `speech_transcribe` — required. Note: the local ASR provides **segment-level** timestamps only (word-level is a Moss model limitation), so word-safe cuts are not provable — plan cuts on segment boundaries and verify joins by listening/rewatch instead.
- `condense_index` — required, and it *is* the visual pass. Its unit table, pause-detection verdict, thumbnail sheet and motion baseline are all a required read.
- `subtitle_scout` — not for this pipeline. It is subtitle-shaped: half its output is a caption-band strip condensing has no use for.
- `video_ingest` — rarely. Only when you must see content between the survey samples. Keep it to one ingest per relevant file and avoid repeated dense sweeps.
- `condense_plan` — produces the cut points. Its flagged joins and snap ratio are a required read, and `out/condense_script.md` is a required read.
- `condense_render` — builds the file. Proves nothing about quality.
- `condense_qc` — required before accepting; its frame and waveform evidence is a required read.
- `video_watch_segment` — the only way to settle a join that frames and numbers left inconclusive, and the tool for Phase 1b speaker attribution. Pass `video_path` and batch tight windows into one call.
- `validate_timeline` — optional cross-check on `out/condense_timeline.json`; it independently confirms every clip sits inside the source. It is not a substitute for QC.
- `render_preview` / `qc_preview` / `timeline_diff` — the general editing line. Not needed here; `condense_render` does the cutting.
- `subtitle_*` — only if the task also asks for captions, and only on the condensed output.

## Completion Bar

Done only when all of these hold:

- `out/media.json`, `out/transcript.json`, `out/speech_index.json` (with its visual survey performed), `out/condense_brief.md`, `out/condense_plan.json`, `out/condense_script.md`, `out/condensed.mp4`, `out/condense_qc_report.json`, `out/condense_verify.md` and `out/report.md` all exist;
- `out/condense_qc_report.json` covers the current plan and the current render, and has no unresolved errors;
- every checkpoint in `out/condense_brief.md` has a final verdict in `out/condense_verify.md`, and every parked one names what was tried;
- `out/condense_script.md` has been read end to end and reads as coherent speech;
- every join evidence row from the final QC run has been read;
- `out/report.md` is non-empty and records assumptions, outputs, parked items and residual risks.
