"""Moderator agent (M2.7): the turn-runner loop, rolling summary and final synthesis.

See runner.py for ModeratorRunner (opening pass, stop conditions, floor-decision dispatch,
Turn-row construction), summarizer.py for the rolling-summary seam, synthesizer.py for the
final-synthesis/minority-report seam, budget.py for the shared token/cost usage aggregate
(M3.1, extracted from runner.py's budget check so the API surface never risks a second
copy silently drifting), and turn_summary.py for the shared TurnSummary type.
"""
from .budget import SessionUsage, session_usage
from .runner import ModeratorConfig, ModeratorRunner, RunOnceResult
from .summarizer import AnthropicSummarizer, FakeSummarizer, SummarizerError, Summarizer, SummaryResult
from .synthesizer import AnthropicSynthesizer, FakeSynthesizer, Synthesizer, SynthesisResult, SynthesizerError
from .turn_summary import TurnSummary

__all__ = [
    "ModeratorRunner", "ModeratorConfig", "RunOnceResult",
    "Summarizer", "SummaryResult", "AnthropicSummarizer", "FakeSummarizer", "SummarizerError",
    "Synthesizer", "SynthesisResult", "AnthropicSynthesizer", "FakeSynthesizer", "SynthesizerError",
    "TurnSummary", "SessionUsage", "session_usage",
]
