"""Catch and retry Chandra generations that fall into a repetition loop.

On hard pages the model sometimes starts repeating the same snippet forever.
With greedy decoding that runs all the way to chandra's MAX_OUTPUT_TOKENS cap
(12,384 tokens, ~20+ minutes on an RX 7600 XT) and the page's output is junk.
Chandra's vLLM backend detects repeats and retries with mild sampling, but its
Hugging Face backend (the one we use) does neither.

This module replaces chandra's HF generate function with one that:
  1. checks the tail of the output every CHECK_EVERY tokens and stops as soon
     as it's looping (so a loop costs ~1-3k tokens, not 12k). Two checks:
     - exact: the last LOOP_MIN_CHARS characters repeat one short snippet;
     - near-repeat: under half of the 4-word sequences in the last 300 words
       are distinct. Catches loops whose copies vary slightly, which is what
       sampled retries produce. Real pages measured 0.80+; a real loop 0.04.
  2. retries a looping or cap-hitting page with sampling, temperature 0.2 then
     0.4 and top_p 0.95 (the same schedule as chandra's vLLM backend);
  3. if every attempt loops, keeps the last one (an exact repeated tail is cut
     down to one copy; a near-repeat one was stopped after a few hundred words)
     and marks the result as an error so it's reported.

Pages that don't loop are generated exactly as before (greedy, same tokens).
"""
import re

import torch
from transformers import StoppingCriteria, StoppingCriteriaList

import chandra.model as chandra_model
from chandra.model.hf import process_batch_element, resolve_device
from chandra.model.schema import GenerationResult
from chandra.settings import settings

MAX_RETRIES = 2          # extra attempts after the first (greedy) one
CHECK_EVERY = 64         # tokens between loop checks
TAIL_TOKENS = 8192       # recent tokens decoded per check. Handwriting with digits/symbols can
                         # average < 2 chars/token, and a check needs LOOP_MIN_CHARS + period chars.
LOOP_MIN_CHARS = 3000    # periodic tail at least this long counts as a loop
LOOP_MAX_PERIOD = 1000   # longest repeated snippet looked for (>= 3 copies in the tail + 1)
FUZZY_WINDOW_WORDS = 300 # near-repeat check: look at the last this many words...
FUZZY_NGRAM = 4          # ...split into overlapping 4-word sequences...
FUZZY_MAX_UNIQUENESS = 0.5  # ...and call it a loop if under half of them are distinct

_TAG_RE = re.compile(r"<[^>]+>")

# Stats about the most recent page, for the OCR cache / progress output.
# "looped" is True if any attempt looped, even when a retry then succeeded.
last_stats = {"attempts": 0, "looped": False}


def loop_period(text: str):
    """Length of the snippet the end of `text` is stuck repeating, or None."""
    n = len(text)
    if n < LOOP_MIN_CHARS + 1:
        return None
    tail_start = n - LOOP_MIN_CHARS
    for period in range(1, LOOP_MAX_PERIOD + 1):
        if tail_start - period < 0:
            break
        # text[tail_start:] is periodic with this period iff it equals itself shifted by `period`.
        if text[tail_start:] == text[tail_start - period:n - period]:
            return period
    return None


def tail_ngram_uniqueness(text: str):
    """Share of distinct FUZZY_NGRAM-word sequences among the last FUZZY_WINDOW_WORDS words.

    A loop with small variations (sampling at temperature > 0, incrementing
    numbers) isn't an exact repeat, but its word sequences recur over and over, so
    this ratio drops far below 1. HTML tags are dropped first, so a large blank
    table (all tags, no words) can't look like a loop. None if too few words yet.
    """
    words = _TAG_RE.sub(" ", text).split()
    if len(words) < FUZZY_WINDOW_WORDS:
        return None
    tail = words[-FUZZY_WINDOW_WORDS:]
    grams = [tuple(tail[i:i + FUZZY_NGRAM]) for i in range(len(tail) - FUZZY_NGRAM + 1)]
    return len(set(grams)) / len(grams)


def is_looping(text: str) -> bool:
    if loop_period(text) is not None:
        return True
    ratio = tail_ngram_uniqueness(text)
    return ratio is not None and ratio < FUZZY_MAX_UNIQUENESS


def trim_loop(text: str) -> str:
    """Cut a looping tail down to one copy of the repeated snippet."""
    period = loop_period(text)
    if period is None:
        return text
    start = len(text) - LOOP_MIN_CHARS
    while start - period >= 0 and text[start - 1] == text[start - 1 + period]:
        start -= 1
    return text[:start + period]


class _LoopStop(StoppingCriteria):
    def __init__(self, tokenizer, prompt_len: int):
        self.tokenizer = tokenizer
        self.prompt_len = prompt_len
        self.triggered = False
        self.checks = 0          # diagnostics: numbers only, never text
        self.max_tail_chars = 0

    def __call__(self, input_ids, scores, **kwargs):
        done = torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)
        generated = input_ids.shape[1] - self.prompt_len
        if generated < CHECK_EVERY or generated % CHECK_EVERY:
            return done
        self.checks += 1
        start = max(self.prompt_len, input_ids.shape[1] - TAIL_TOKENS)
        for i in range(input_ids.shape[0]):
            text = self.tokenizer.decode(input_ids[i, start:].tolist(), skip_special_tokens=True)
            self.max_tail_chars = max(self.max_tail_chars, len(text))
            if is_looping(text):
                done[i] = True
                self.triggered = True
        return done


def _eos_ids(model):
    # Same stop tokens as chandra's own HF backend.
    eos = model.generation_config.eos_token_id
    eos = [eos] if isinstance(eos, int) else list(eos or [])
    im_end = model.processor.tokenizer.convert_tokens_to_ids("<|im_end|>")
    if im_end is not None and im_end not in eos:
        eos.append(im_end)
    return eos


def _generate_one(item, model, max_tokens: int) -> GenerationResult:
    tokenizer = model.processor.tokenizer
    inputs = model.processor.apply_chat_template(
        [[process_batch_element(item)]],
        tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt", padding=True,
    ).to(resolve_device(torch, model=model))
    prompt_len = inputs.input_ids.shape[1]
    eos = _eos_ids(model)
    last_stats.update(attempts=0, looped=False)

    for attempt in range(MAX_RETRIES + 1):
        if attempt == 0:
            sampling = {"do_sample": False}
        else:
            sampling = {"do_sample": True, "temperature": min(0.2 * attempt, 0.8), "top_p": 0.95}
        stop = _LoopStop(tokenizer, prompt_len)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_tokens, eos_token_id=eos,
                                 stopping_criteria=StoppingCriteriaList([stop]), **sampling)
        ids = out[0, prompt_len:]
        text = tokenizer.decode(ids.tolist(), skip_special_tokens=True)
        looped = stop.triggered or is_looping(text)
        hit_cap = len(ids) >= max_tokens
        last_stats.update(attempts=attempt + 1, looped=last_stats["looped"] or looped)  # looped on any attempt
        if not (looped or hit_cap):
            return GenerationResult(raw=text, token_count=len(ids), error=False)

        reason = "fell into a repetition loop" if looped else "hit the token limit"
        period = loop_period(text)
        ratio = tail_ngram_uniqueness(text)
        print(f"    [guard] stopped early: {stop.triggered}, checks run: {stop.checks}, "
              f"output: {len(text)} chars, exact repeat length: {period}, "
              f"near-repeat uniqueness: {'n/a' if ratio is None else f'{ratio:.2f}'}")
        if attempt < MAX_RETRIES:
            print(f"    Output {reason} after {len(ids)} tokens; retrying with sampling "
                  f"(attempt {attempt + 2}/{MAX_RETRIES + 1})")
        else:
            print(f"    Output {reason} after {len(ids)} tokens on every attempt; keeping the last one, "
                  "with the repeated part trimmed")
    return GenerationResult(raw=trim_loop(text), token_count=len(ids), error=True)


def generate_hf_guarded(batch, model, max_output_tokens=None, bbox_scale=settings.BBOX_SCALE, **kwargs):
    max_tokens = max_output_tokens or settings.MAX_OUTPUT_TOKENS
    return [_generate_one(item, model, max_tokens) for item in batch]


def install():
    """Make chandra's InferenceManager use the guarded generator (it looks the name up at call time)."""
    chandra_model.generate_hf = generate_hf_guarded
