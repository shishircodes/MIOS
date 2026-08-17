"""Mode Push — the Profile Matcher.

Mode Monitor answers "who is hiring?". Mode Push answers the outbound question:
given a consultant or candidate the BD team already has, which companies need
someone with those skills right now?

    CV (.docx/.pdf) ──┐
                      ├─► profile ──► matcher ──► ranked companies + evidence
    manual form ──────┘                  ▲
                                         │
                             hiring signals from Mode Monitor

Deliberately free of LLM calls. Extraction is rule-based (see `profile_parser`)
and scoring is deterministic (see `matcher`), which means results are
reproducible, cost nothing per run, and can show *why* a company scored what it
did — the evidence lines the BD team acts on.
"""
from push.cv_extract import CVExtractionError, extract_text
from push.matcher import MatchResult, match_profile
from push.profile_parser import ParsedProfile, parse_profile

__all__ = [
    "CVExtractionError",
    "extract_text",
    "ParsedProfile",
    "parse_profile",
    "MatchResult",
    "match_profile",
]
