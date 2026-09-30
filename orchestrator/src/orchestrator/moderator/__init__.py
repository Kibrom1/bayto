"""Moderator agent (M2.7): the turn-runner loop, rolling summary and final synthesis.

See runner.py for ModeratorRunner (opening pass, stop conditions, floor-decision dispatch,
Turn-row construction), summarizer.py for the rolling-summary seam, synthesizer.py for the
final-synthesis/minority-report seam, and turn_summary.py for the shared TurnSummary type.
"""
from .runner import ModeratorConfig, ModeratorRunner, RunOnceResult
from .summarizer import AnthropicSummarizer, FakeSummarizer, SummarizerError, Summarizer, SummaryResult
from .synthesizer import AnthropicSynthesizer, FakeSynthesizer, Synthesizer, SynthesisResult, SynthesizerError
from .turn_summary import TurnSummary

__all__ = [
    "ModeratorRunner", "ModeratorConfig", "RunOnceResult",
    "Summarizer", "SummaryResult", "AnthropicSummarizer", "FakeSummarizer", "SummarizerError",
    "Synthesizer", "SynthesisResult", "AnthropicSynthesizer", "FakeSynthesizer", "SynthesizerError",
    "TurnSummary",
]
