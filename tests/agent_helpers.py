"""A scripted stand-in for the model, and jobs that leave the agent something to settle."""

from types import SimpleNamespace

from retimer import Subtitle, loads

from .bench_helpers import _stamp
from .sync_helpers import SENTENCES, make_subtitle, spread

# Three lines of the target in another language. The stand-in for the embedding
# service compares words, so these find no counterpart, as a paraphrase would not.
FOREIGN = [
    "Du hast versprochen, den silbernen Schlüssel zurückzugeben",
    "Der Motor ist auf halber Höhe des Berges ausgefallen",
    "Jemand hat oben eine Kerze brennen lassen",
]

USAGE = {
    "input_tokens": 1_000,
    "output_tokens": 200,
    "cache_creation_input_tokens": 3_000,
    "cache_read_input_tokens": 4_000,
}


def call(name: str, **args) -> SimpleNamespace:
    """A tool call as the model would make it."""
    return SimpleNamespace(type="tool_use", name=name, input=args, id=None)


def say(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def pair(*pairs: tuple[int, int]) -> SimpleNamespace:
    return call("pair_lines", pairs=[{"line": line, "place": place} for line, place in pairs])


def submit(verdict: str = "same_content", note: str = "Settled.") -> SimpleNamespace:
    return call("submit", verdict=verdict, note=note)


class Reply:
    """One turn of the model, when it is more than a list of blocks."""

    def __init__(self, *blocks, stop_reason: str | None = None, model: str | None = None) -> None:
        self.blocks = list(blocks)
        self.stop_reason = stop_reason
        self.model = model


class FakeModel:
    """Answers each request with the next scripted turn and keeps what it was sent.

    A turn is a list of blocks, a `Reply`, or an exception to raise. Requests
    made through the beta interface are kept apart, with the same answers.
    """

    def __init__(self, *turns) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.beta_requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create_beta))
        self._calls = 0

    def _create_beta(self, **params):
        self.beta_requests.append(params)
        return self._create(**params)

    def _create(self, **params):
        # The caller goes on adding to its list of messages, so it is copied.
        self.requests.append({**params, "messages": list(params["messages"])})
        if not self.turns:
            raise AssertionError("the model was asked more often than the script has turns")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        reply = turn if isinstance(turn, Reply) else Reply(*turn)
        for block in reply.blocks:
            if block.type == "tool_use":
                self._calls += 1
                block.id = f"toolu_{self._calls}"
        used = any(block.type == "tool_use" for block in reply.blocks)
        return SimpleNamespace(
            content=reply.blocks,
            stop_reason=reply.stop_reason or ("tool_use" if used else "end_turn"),
            model=reply.model or params["model"],
            usage=SimpleNamespace(**USAGE),
        )


class AgreeingModel(FakeModel):
    """A model that settles nothing itself: asked about a job, it submits what is on the bench.

    It stands in where a test is about something else than the model's part.
    """

    def _create(self, **params):
        if not self.turns:
            self.turns.append([submit()])
        return super()._create(**params)


def model_in_reach(monkeypatch, *turns) -> FakeModel:
    """Set the key the sync command asks for and put a scripted model where it would connect.

    With no turns the model fails the test that asks it anything, so a test
    that expects the routine steps to settle the job says so by naming none.
    """
    model = FakeModel(*turns)
    monkeypatch.setenv("OPENROUTER_API_KEY", "key-for-the-test")
    monkeypatch.setattr("retimer.agent.loop.make_client", lambda provider=None: model)
    return model


def three_parts(
    offsets: tuple[int, int, int] = (2_000, 5_300, 7_700),
    first: int = 12,
    second: int = 15,
) -> tuple[Subtitle, list[int]]:
    """A target in three parts that are late by different amounts, and its true starts.

    The lines of the middle part are in another language, so matching finds
    the two outer parts and nothing for the lines between them.
    """
    truth = spread(30)
    starts = [
        start + offsets[0 if index < first else 1 if index < second else 2]
        for index, start in enumerate(truth)
    ]
    texts = list(SENTENCES[:30])
    texts[first:second] = FOREIGN[: second - first]
    return make_subtitle(starts, texts), truth


# What every episode of a made-up series begins with.
NARRATION = [
    "once again the wind crosses the little garden",
    "and so the story of today can begin",
]


def episode(number: int, count: int) -> list[str]:
    """The lines of an episode: the narration, then lines no other episode has."""
    own = [
        f"quartz{number}x{index} velvet{number}x{index} ember{number}x{index}"
        for index in range(count - len(NARRATION))
    ]
    return NARRATION + own


# The same narration as another subtitler would word it.
REWORDED = [
    "once more the wind crosses the small garden",
    "and so the tale of today can begin",
]


def pack(
    position: str = "second", lead_ms: int = 400_000, reworded: bool = False
) -> tuple[Subtitle, Subtitle, list[int]]:
    """Two episodes in one file, the reference for one of them, and its true starts.

    The episode of the reference is the 30 sentences with the narration in
    place of the first two. The other episode has 24 lines and comes before
    it or after it. In the file each episode is timed from where the one
    before it ended, so the one of the reference is late by `lead_ms` when it
    is the second, and by 1.5 s when it is the first.

    With `reworded`, the reference words the narration a little differently
    from its own episode in the file, and exactly as the other episode does.
    """
    truth = spread(30)
    own = NARRATION + list(SENTENCES[2:30])
    other = (REWORDED if reworded else NARRATION) + episode(2, 24)[2:]
    elsewhere = spread(24)
    if position == "second":
        starts = elsewhere + [start + lead_ms for start in truth]
        texts = other + own
    else:
        starts = [start + 1_500 for start in truth] + [
            start + truth[-1] + lead_ms for start in elsewhere
        ]
        texts = own + other
    said = (REWORDED if reworded else NARRATION) + own[2:]
    return make_subtitle(starts, texts), make_subtitle(truth, said), truth


def timed(rows: list[tuple[int, int, str]]) -> Subtitle:
    """An SRT from lines given as start, end and text."""
    blocks = [
        f"{index + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(rows)
    ]
    return loads("\n".join(blocks))
