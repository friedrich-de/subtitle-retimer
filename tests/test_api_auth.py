import pytest

from retimer.api.auth import (
    MAX_KEY_CHARS,
    ApiKey,
    bearer_key,
    format_entry,
    hash_key,
    new_key,
    parse_keys,
    verify,
)
from retimer.api.limits import MAX_TRACKED, SlidingWindow, Slots, address_name


def test_a_new_key_is_accepted_and_only_its_hash_is_kept():
    key, entry = new_key("al")

    assert key.startswith("rt_") and len(key) >= 40
    assert key not in format_entry(entry)
    assert entry.sha256 == hash_key(key)
    assert verify(key, [entry]) == entry


def test_two_keys_are_never_the_same():
    assert new_key("al")[0] != new_key("al")[0]


def test_a_wrong_key_is_refused():
    key, entry = new_key("al")
    other, _ = new_key("someone")

    assert verify(other, [entry]) is None
    assert verify(key[:-1], [entry]) is None
    assert verify(key + "x", [entry]) is None
    assert verify("", [entry]) is None
    assert verify(None, [entry]) is None
    assert verify(key, []) is None


def test_the_hash_itself_is_not_a_key():
    key, entry = new_key("al")
    assert verify(entry.sha256, [entry]) is None


def test_a_key_longer_than_any_real_one_is_not_even_hashed():
    entry = ApiKey(label="long", sha256=hash_key("k" * (MAX_KEY_CHARS + 1)))
    assert verify("k" * (MAX_KEY_CHARS + 1), [entry]) is None


def test_the_right_entry_is_found_among_several():
    first, first_entry = new_key("first")
    second, second_entry = new_key("second")

    assert verify(second, [first_entry, second_entry]).label == "second"
    assert verify(first, [first_entry, second_entry]).label == "first"


def test_entries_are_read_from_the_configuration():
    _, one = new_key("al")
    _, two = new_key("phone-2")

    assert parse_keys(f"{format_entry(one)},{format_entry(two)}") == [one, two]
    assert parse_keys(f" {format_entry(one)}\n{format_entry(two)} ") == [one, two]
    assert parse_keys("") == []


def test_a_key_pasted_where_a_hash_belongs_is_refused_without_repeating_it():
    key, entry = new_key("al")

    with pytest.raises(ValueError) as refused:
        parse_keys(key)
    assert key not in str(refused.value)
    assert "retimer api-key" in str(refused.value)

    with pytest.raises(ValueError) as refused:
        parse_keys(f"al:{key}")
    assert key not in str(refused.value)

    with pytest.raises(ValueError, match="repeats the label"):
        parse_keys(f"{format_entry(entry)},{format_entry(entry)}")


def test_a_label_is_a_short_plain_name():
    for label in ("", "Al", "a b", "a:b", "x" * 33, "-al"):
        with pytest.raises(ValueError):
            new_key(label)


def test_the_key_is_taken_from_a_header_and_from_nowhere_else():
    assert bearer_key({"authorization": "Bearer rt_abc"}) == "rt_abc"
    assert bearer_key({"authorization": "bearer   rt_abc "}) == "rt_abc"
    assert bearer_key({"x-api-key": "rt_abc"}) == "rt_abc"
    assert bearer_key({"authorization": "Basic rt_abc"}) is None
    assert bearer_key({"authorization": "Bearer "}) is None
    assert bearer_key({}) is None


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_window_lets_its_limit_through_and_then_says_how_long_to_wait():
    clock = Clock()
    window = SlidingWindow(3, 60.0, clock)

    assert [window.hit("al") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert window.hit("al") == pytest.approx(60.0)
    assert window.hit("someone else") == 0.0

    clock.now += 45
    assert window.hit("al") == pytest.approx(15.0)
    assert window.wait_s("al") == pytest.approx(15.0)
    clock.now += 15.01
    assert window.wait_s("al") == 0.0
    assert window.hit("al") == 0.0


def test_a_refused_hit_does_not_extend_the_wait():
    clock = Clock()
    window = SlidingWindow(1, 60.0, clock)
    window.hit("al")

    for _ in range(50):
        clock.now += 1
        assert window.hit("al") > 0
    clock.now += 10.01
    assert window.hit("al") == 0.0


def test_a_flood_of_names_does_not_grow_without_bound():
    clock = Clock()
    window = SlidingWindow(5, 60.0, clock)

    for index in range(MAX_TRACKED + 500):
        window.hit(f"address-{index}")

    assert len(window._hits) <= MAX_TRACKED


def test_slots_count_what_is_under_way():
    slots = Slots()

    assert slots.take("al", limit=2)
    assert slots.take("al", limit=2)
    assert not slots.take("al", limit=2)
    assert slots.take("someone", limit=2)
    assert not slots.take("someone", limit=2, already=1)

    slots.release("al")
    assert slots.held("al") == 1
    assert slots.take("al", limit=2)
    slots.release("al")
    slots.release("al")
    slots.release("al")
    assert slots.held("al") == 0


def test_addresses_of_one_line_count_as_one():
    assert address_name("203.0.113.9") == "203.0.113.9"
    assert address_name("2001:db8:1:2:aaaa::1") == address_name("2001:db8:1:2:bbbb::2")
    assert address_name("2001:db8:1:2::1") != address_name("2001:db8:1:3::1")
    assert address_name("::ffff:203.0.113.9") == "203.0.113.9"
    assert address_name(None) == "unknown"
    assert address_name("testclient") == "testclient"
