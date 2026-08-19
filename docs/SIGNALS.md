# Review signals

The signals module prioritizes documents for review. It neither proves a thesis nor adds inferred
relationships to the graph.

## Sources and labels

The `rss_feed` collector can work only with a feed description (`fetch_article: false`), avoiding
a full article download when the summary is sufficient. Every source retains:

- `source_side`: an operator-defined editorial label;
- `attention_weight`: a relative-attention weight;
- `attention_note`: the human rationale for that weight.

Example sources are disabled. Before enabling one, verify that it is current and review its terms,
copyright, and automation policy. A label neither objectively characterizes an outlet nor proves
that it is independent from other sources.

## Low attention and structural novelty

A document is prioritized when it combines:

- relatively low attention, estimated from source weight and position in a feed or listing;
- structural novelty, estimated from rare edges, high-betweenness endpoints, cross-label edges,
  or predicates more specific than a co-mention.

The score is a corpus-dependent heuristic. A prioritized document is not inherently suspicious;
it simply introduces structure worth reading.

## Comparison with an official corpus

The agent compares non-official evidence with official documents inside a configurable window:

- `possible_silence`: an entity appears outside the official corpus but is not found verbatim in
  official documents from the window;
- `official_denial_or_correction`: the official corpus mentions the entity near denial or
  correction phrases. The default lexicon includes Spanish phrases because the sample corpus is
  Spanish-language.

Limitations:

- the analysis is textual, not semantic;
- absence does not imply concealment or intent;
- a denial-word match may lack context;
- when the official corpus predates external evidence, the report adds a warning;
- incomplete coverage, missing dates, and extraction errors distort the result.

## Artifacts

- `data/output/small_notes.json`
- `data/output/morning_readings.json`
- `data/output/signals.json`

All artifacts are reading queues that require verification against the original sources.
