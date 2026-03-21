"""
test_oracles_say.py — Say log, emote log, and oracle (Q&A) system.
"""

from engine import answer_oracle, ask_oracle, emote, say


class TestSayLog:
    def test_say_appends_entry(self, active_state):
        result = say(active_state, "Aldric", "Hello!")
        assert result.ok
        assert len(active_state.say_log) == 1
        assert "Aldric" in active_state.say_log[0]
        assert "Hello!" in active_state.say_log[0]

    def test_multiple_says_accumulate(self, active_state):
        say(active_state, "Aldric", "Hello!")
        say(active_state, "Mira", "Quiet!")
        assert len(active_state.say_log) == 2

    def test_say_log_cleared_on_resolve(self, active_state):
        from engine import close_turn, resolve_turn
        say(active_state, "Aldric", "Hello!")
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.say_log == []

    def test_emote_appends_entry(self, active_state):
        result = emote(active_state, "Aldric", "draws his sword.")
        assert result.ok
        assert "draws his sword" in active_state.say_log[0]


class TestOracles:
    def test_ask_oracle_creates_entry(self, active_state):
        result = ask_oracle(active_state, "Aldric", "What lurks ahead?")
        assert result.ok
        oracle = result.data
        assert oracle.number == 1
        assert oracle.question == "What lurks ahead?"
        assert oracle.answer is None

    def test_oracle_counter_increments(self, active_state):
        r1 = ask_oracle(active_state, "Aldric", "Q1?")
        r2 = ask_oracle(active_state, "Mira", "Q2?")
        o1, o2 = r1.data, r2.data
        assert o1.number == 1
        assert o2.number == 2

    def test_answer_oracle(self, active_state):
        r_ask = ask_oracle(active_state, "Aldric", "Is it safe?")
        oracle = r_ask.data
        result = answer_oracle(active_state, oracle.number, "No.")
        assert result.ok
        answered = result.data
        assert answered.answer == "No."

    def test_answer_unknown_oracle_fails(self, active_state):
        result = answer_oracle(active_state, 99, "Answer.")
        assert not result.ok
        assert result.data is None

    def test_oracle_counter_resets_on_resolve(self, active_state):
        from engine import close_turn, resolve_turn
        ask_oracle(active_state, "Aldric", "Q1?")
        ask_oracle(active_state, "Mira", "Q2?")
        assert active_state.oracle_counter == 2
        close_turn(active_state)
        resolve_turn(active_state, "Narrative.")
        assert active_state.oracle_counter == 0
