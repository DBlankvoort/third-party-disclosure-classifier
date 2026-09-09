# Third-party disclosure explorer

This repository contains tooling for analysing the ways in which organisations disclose the third parties with whom they share data. It seeks to provide tooling for:
1. Fetching sets of relevant documents containing information on third-party data sharing.
2. Classifying such documents according to how they disclose third parties.
3. Interpreting the data sharing clauses to create knowledge graphs (based largely on PoliGraph for privacy policies).
4. Easily running such analysis on a given URL through a Firefox extension.

In this way, we seek to allow greater insight into the third-party data sharing behaviour of sites and give a structured overview which is more digestible than privacy policies.

We provide the following information for a given URL:
- The set of relevant documents which state something about third-party sharing.
- How such documents are structured, and with what specificity third parties are disclosed.
- Which named third parties and generic descriptors of third parties are detected.
- Which data sharing relationships are detected.
- A graph-based visualization for such sharing relationships.
- What rights actions are possible.

For this work, we adapt code from:
- [PolicyLint](https://github.com/benandow/PrivacyPolicyAnalysis)
- [PoliGraph](https://github.com/UCI-Networking-Group/PoliGraph)
- [Ali et al. 2024's implementation of Polisis](https://github.com/masood/2024-pets-privacy-labels-policies) 

We also take heavy inspiration from:
- [The OPP-115 corpus](https://usableprivacy.org/data/)
- [PolicyLint: Investigating Internal Privacy Policy Contradictions on Google Play](https://www.usenix.org/conference/usenixsecurity19/presentation/andow)
- [PoliGraph: Automated Privacy Policy Analysis using Knowledge Graphs](https://arxiv.org/abs/2210.06746)
- [Honesty is the Best Policy: On the Accuracy of Apple Privacy Labels Compared to Apps' Privacy Policies](https://arxiv.org/abs/2306.17063)
- [Polisis: Automated Analysis and Presentation of Privacy Policies Using Deep Learning](https://www.usenix.org/conference/usenixsecurity18/presentation/harkous)
- [Privacy Policies over Time: Curation and Analysis of a Million-Document Dataset](https://arxiv.org/abs/2008.09159)
- [The Open Terms Archive](https://opentermsarchive.org/en/)
- [The W3C Data Privacy Vocabulary](https://w3c-cg.github.io/dpv/)
- [The Usable Privacy Project](https://usableprivacy.org/data/)
- [Before & After: The Effect of EU's 2022 Code of Practice on Disinformation](https://arxiv.org/abs/2410.11369)

The sampling frames include data from:
- [The dataset from "Honesty is the Best Policy"](https://huggingface.co/datasets/masoodali/apple-app-store-labels-policies) for samples of Apple App Store apps.
- [IAB Europe's Vendors list](https://iabeurope.eu/tcf-for-vendors/) for samples of data brokers.
- [The MAPS Policies Dataset](https://usableprivacy.org/data/) for samples of Google Play Store apps.
- [The Princeton-Leuven Longitudinal Corpus Crawler](https://privacypolicies.cs.princeton.edu/) for samples of websites.

Additionally, we make use of Claude Code and Codex in the design and implementation of this project.

## Installation

Requires Python ≥ 3.10 (developed on 3.12).

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m spacy download en_core_web_sm 
```

Optional extras:

```sh
pip install -e ".[dev]"       # pytest + ruff
pip install -e ".[browser]"   # playwright, for JS-rendered collection
python -m playwright install firefox
pip install -e ".[ml]"        # torch/transformers/setfit, for Polisis components
pip install -e evaluation/    # evaluation
```

## Usage

### Firefox extension
Navigate to the root directory, then

```sh
.venv/bin/python firefox_extension/server/server.py
```

to start the server. Afterwards, navigate to `about:debugging` -> 'This Firefox' -> 'Load Temporary Add-on' -> Select `firefox_extension/extension/manifest.json` to load the extension for the session.

### `tpd` package
Accessible using `tpd` or `python -m tpd` as a CLI. See `tpd/cli.py` for arguments.

### `tpd-eval` package

```sh
tpd-eval validate --manifest evaluation/schemas/manifest-v2.example.json
tpd-eval annotation-sheet --candidates candidates.jsonl --out annotations.jsonl
tpd-eval score --manifest manifest.json --annotations held-out-test.jsonl --out report.md
```

## Repository layout

### Core

| Path | Contents |
| --- | --- |
| `tpd/lexicons.py` | Regex lexicons, clause heuristics |
| `tpd/extract.py` | HTML → segments/tables/links representations |
| `tpd/tracks.py` | Track and data-subject vocabulary carried by every relation |
| `tpd/entities.py` | Organisation-name canonicalisation, domain ↔ entity resolution |
| `tpd/kb/` | Tracker Radar, the TCF Global Vendor List, holding companies, headquarters countries |
| `tpd/site_kind.py` | What kind of site a URL names |
| `tpd/probe.py` | Clean-profile traffic capture |
| `tpd/traffic.py` | Third parties named by observed network requests |
| `tpd/cmp.py` | Third parties named by a captured consent dialog |
| `tpd/reconcile.py` | Check against sellers.json, tracker lists |
| `tpd/corroboration.py` | Parties to keep in main findings graph |
| `tpd/sharing_graph.py` | Cross-target graph of evidence-bearing propositions |
| `tpd/expand.py` | Outward multi-hop collection from one seed origin |
| `tpd/refresh.py` | Snapshot and change tracking across collections |
| `tpd/collect/` | Crawler |
| `tpd/classify/` | Relevance, document-class, specificity, and typology classifiers |
| `tpd/poligraph/` | PoliGraph re-implementation |
| `tpd/polisis/` | Polisis-style hierarchical classifiers |
| `firefox_extension/` | Browser extension code |
| `models/` | POLISIS model weights |
| `tests/` | Pytest suite for the core package |

### Evaluation

| Path | Contents |
| --- | --- |
| `evaluation/tpd_eval/schema.py` | Versioned evidence records and stable identifiers |
| `evaluation/tpd_eval/splits.py` | Target/template/family/near-duplicate leakage checks |
| `evaluation/tpd_eval/metrics.py` | Conventional claim, resolution, traffic, chain, and agreement metrics |
| `evaluation/tpd_eval/report.py` | Provenance-first Markdown and JSON reporting |
| `evaluation/schemas/` | Versioned annotation schema and manifest template |
| `evaluation/data_sources/` | Seed lists the study corpora were sampled from |
| `evaluation/labels/` | Explicitly contaminated legacy/development-only labels |
| `evaluation/corpus/` | Collected document sets (untracked) |
| `evaluation/tests/` | Pytest suite for the harness |

## Testing

```sh
pip install -e ".[dev]"
pytest
```

## License
This project is licensed under the GNU General Public License v3.0. As we adapt code from PolicyLint, we ask users to also comply with its slightly more restrictive license, as found in LICENSE_PolicyLint.
