"""A scripted stand-in for the model, and jobs that leave the agent something to settle."""

from types import SimpleNamespace

from retimer import Subtitle

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
