"""The benchmark: cases with known-correct timing, and one harness for every system."""

from retimer.bench.cases import Case, LoadedCase, generate_cases, load_cases
from retimer.bench.distort import Break, Distortion, Noise, TruthLine, distort
from retimer.bench.harness import run_benchmark
from retimer.bench.metrics import FileMetrics, evaluate
from retimer.bench.runners import builtin_runners

__all__ = [
    "Break",
    "Case",
    "Distortion",
    "FileMetrics",
    "LoadedCase",
    "Noise",
    "TruthLine",
    "builtin_runners",
    "distort",
    "evaluate",
    "generate_cases",
    "load_cases",
    "run_benchmark",
]
