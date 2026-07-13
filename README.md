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

Lastly, we evaluate our framework using data from:
- [The dataset from "Honesty is the Best Policy"](https://huggingface.co/datasets/masoodali/apple-app-store-labels-policies) for samples of Apple App Store apps.
- [IAB Europe's Vendors list](https://iabeurope.eu/tcf-for-vendors/) for samples of data brokers.
- [The MAPS Policies Dataset](https://usableprivacy.org/data/) for samples of Google Play Store apps.
- [The Princeton-Leuven Longitudinal Corpus Crawler](https://privacypolicies.cs.princeton.edu/) for samples of websites.

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
```

## Usage

### Firefox extension
Navigate to the root directory, then

```sh
.venv/bin/python firefox-extension/server/server.py
```

to start the server. Afterwards, navigate to `about:debugging` -> 'This Firefox' -> 'Load Temporary Add-on' -> Select `firefox-extension/extension/manifest.json` to load the extension for the session.

### `tpd` package
Accessible using `tpd` or `python -m tpd` as a CLI. See `tpd/cli.py` for arguments. The package is primarily intended to validate the analysis approach through e.g. collecting and analysing data from many URLs and allowing for comparison against hand-labels.

## Repository layout

| Path | Contents |
| --- | --- |
| `tpd/lexicons.py` | Regex lexicons, clause heuristics |
| `tpd/extract.py` | HTML → segments/tables/links representations |
| `tpd/collect/` | Crawler |
| `tpd/classify/` | Relevance, document-class, specificity, and typology classifiers |
| `tpd/poligraph/` | PoliGraph re-implementation (creating data sharing graphs for policies) |
| `tpd/polisis/` | Polisis-style hierarchical classifiers |
| `tpd/evaluate/` | Labelling sheets and agreement/latency metrics |
| `firefox-extension/` | Browser extension code |
| `tests/` | Pytest suite for the codebase |

## Testing

```sh
pip install -e ".[dev]"
pytest
```

## License
This project is licensed under the GNU General Public License v3.0. As we adapt code from PolicyLint, we ask users to also comply with its slightly more restrictive license, as found in LICENSE_PolicyLint.