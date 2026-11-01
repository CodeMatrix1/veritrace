# Veritrace: Alignment with the Problem Statement

**Problem statement:** A high-throughput verification platform that ingests live social feeds, multimodal media, and viral claims, cross-referencing them against verified database registries, knowledge graphs, and forensic models to detect synthetic media and trace misinformation provenance.

**Position to take:** Veritrace is the verification core of this platform. It covers claims, evidence and early provenance in depth. It does not yet cover live feeds, media or synthetic media detection.

## 1. Alignment by Requirement

| Requirement | Status today | What exists | Possible for submission? | Longer-term path |
|---|---|---|---|---|
| Viral claims | **Built** | Text and URL input, a browser extension for selected text, and claim extraction | Yes | Add Hindi and regional languages |
| Cross-referencing verified registries | **Mostly built** | Official-site searches, over 600 saved official pages, an assistant that explains them, and rule-based verdicts | Yes | Add direct adapters for World Bank, PubMed and data.gov.in |
| Misinformation provenance | **Partial** | Source History shows the earliest source found, with dates | Yes, as "earliest source found" | Track spread across posts and how the wording changed |
| High throughput | **Partial** | 3 audits at once, 4 searches per claim at once, permanent caching and saved reports. An audit takes from under a minute to a few minutes | Only state measured numbers | Stream processing, deduplication and clustering of claims |
| Knowledge graphs | **Not built** | A saved official-page collection with text search, which is not a graph | No | Link entities, claims and sources. Wikidata is a good start |
| Live social feeds | **Not built** | The extension checks a post the user selects | Partly: user-selected posts only | Connect a feed such as Bluesky, and rank claims by spread |
| Multimodal media | **Not built** | Text only | No | Screenshot OCR first, then image history, then audio and video transcripts |
| Forensic models | **Not built, by decision** | None | No | Add as one signal, not a verdict |
| Synthetic media detection | **Not built, by decision** | None | No | Add detectors and content credentials after image support |

The track also covers public services and citizens' access to verified facts. The Ask assistant addresses this directly, with schemes, policies, rights and public data. Business compliance guidance is outside P4, so it is left out.

## 2. What Is Not Realistic

- **A true high-throughput system in this hackathon:** One backend and free-tier AI limits can't support it.
- **Reliable deepfake detection:** Detectors weaken on compressed social media content, and a wrong accusation is costly.
- **Finding the true origin of a claim:** The system can only report the earliest source it found.
- **Complete coverage of official sources:** Many official sites block bots or need a browser. Selected data must come through search providers or APIs.
- **A full social feed from major platforms:** Access is limited or costly, and a feed needs review of platform terms.

## 3. Recommended Next Work

| Priority | Work | Effect on alignment | Effort |
|---|---|---|---|
| 1 | Deploy the backend, website and database | Required for submission | 2–3 hours |
| 2 | Resolve the Groq limit for judging | Keeps checks and Ask working | Minutes, if upgrading |
| 3 | Test 5 to 10 fresh claims, and show measured results in the deck | Replaces claims with evidence | 1–2 hours |
| 4 | Finish the README links, deck and video | Required for submission | 3–4 hours |
| 5 | Screenshot OCR in the browser | First multimodal step | 2–3 hours, optional |

Already done: the 20-second search limit, parallel searches, yes/no questions, and the government information assistant.

## 4. How to Present It

- **Say what was chosen:** "We built the part that decides what is true, because a feed or detector is only useful if the verification behind it can be trusted."
- **Say what is missing:** Live feeds, media and synthetic detection are on the roadmap, not in the product.
- **Check the rules:** Confirm that AI assistance is allowed, and that every team member can explain the code without help.
- **Don't overclaim:** Avoid "high-throughput", "knowledge graph" and "deepfake detection" as current features. Use "earliest source found", never "patient zero".
- **Show the difference:** Exact quotes, rule-based verdicts, and a clear result when the evidence isn't enough.

## 5. Judging Risk

The problem statement's title includes "Deepfake Audit Engine", and Veritrace doesn't do this. Partial implementation is allowed, but judges may notice the gap, so raise it yourself in the deck and video.
