"""Client UX: vim state + count + search + palette."""

from takton_code.tui.vim_ux import (
    VimState,
    filter_palette,
    find_hits,
    highlight_line,
)


def test_vim_state_cycle():
    v = VimState()
    assert v.mode == "insert"
    v.enter_normal()
    assert v.mode == "normal"
    assert "NORMAL" in v.label()
    v.pending = "g"
    assert "+g" in v.label()
    v.enter_insert()
    assert v.mode == "insert"
    assert v.pending == ""


def test_count_accumulator():
    v = VimState()
    v.enter_normal()
    v.feed_digit("1")
    v.feed_digit("0")
    assert v.count_str == "10"
    assert v.take_count() == 10
    assert v.count_str == ""
    assert v.take_count() == 1
    # bare 0 not fed as count via feed_digit when empty — handled in UI
    v.feed_digit("0")  # with empty, feed_digit ignores 0
    assert v.count_str == ""


def test_palette_filter():
    all_a = filter_palette("")
    assert len(all_a) >= 5
    hits = filter_palette("rewind")
    assert any(a.id == "rewind" for a in hits)
    hits2 = filter_palette("yank")
    assert any(a.id == "yank" for a in hits2)
    assert filter_palette("zzz_not_exist") == []


def test_search_hits_and_highlight():
    lines = ["hello world", "foo ERROR bar", "error again", "ok"]
    hits = find_hits(lines, "error")
    assert len(hits) == 2
    assert hits[0].line_index == 1
    hl = highlight_line("foo ERROR bar", "error", current=True)
    assert "ERROR" in hl or "error" in hl.lower()
    assert "[" in hl  # markup
    v = VimState()
    v.enter_normal()
    v.search_query = "error"
    v.set_hits(hits)
    assert v.current_hit() is not None
    assert v.current_hit().line_index == 1
    n = v.next_hit()
    assert n is not None
    assert n.line_index == 2
    assert v.search_idx == 1
    lab = v.label()
    assert "NORMAL" in lab and "error" in lab
