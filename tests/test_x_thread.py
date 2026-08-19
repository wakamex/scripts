# ruff: noqa: D101, D102

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "x_thread.py"
SPEC = importlib.util.spec_from_file_location("x_thread", SCRIPT_PATH)
x_thread = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(x_thread)


def tweet(tweet_id, parent=None, author="ornith_", text=None):
    return {
        "id": str(tweet_id),
        "text": text or f"post {tweet_id}",
        "ts": int(tweet_id),
        "author": author,
        "replying_to_status": str(parent) if parent else None,
    }


class ParentChainTests(unittest.TestCase):
    def test_resolves_same_author_parents_to_root(self):
        posts = {
            "4": tweet(4, 3),
            "3": tweet(3, 2),
            "2": tweet(2, 1),
            "1": tweet(1),
        }

        with patch.object(x_thread, "fx_tweet", side_effect=lambda _user, tid: posts.get(tid)):
            chain, complete = x_thread.parent_chain(posts["4"])

        self.assertTrue(complete)
        self.assertEqual([post["id"] for post in chain], ["1", "2", "3", "4"])

    def test_stops_at_reply_to_another_author(self):
        child = tweet(2, 1)
        other = tweet(1, author="someone_else")

        with patch.object(x_thread, "fx_tweet", return_value=other):
            chain, complete = x_thread.parent_chain(child)

        self.assertTrue(complete)
        self.assertEqual([post["id"] for post in chain], ["2"])

    def test_reports_a_cycle_as_incomplete(self):
        posts = {"2": tweet(2, 1), "1": tweet(1, 2)}

        with patch.object(x_thread, "fx_tweet", side_effect=lambda _user, tid: posts.get(tid)):
            chain, complete = x_thread.parent_chain(posts["2"])

        self.assertFalse(complete)
        self.assertEqual([post["id"] for post in chain], ["1", "2"])

    def test_parent_walk_has_no_hop_ceiling(self):
        posts = {
            str(post_id): tweet(post_id, post_id - 1 if post_id > 1 else None)
            for post_id in range(1, 14)
        }

        with patch.object(x_thread, "fx_tweet", side_effect=lambda _user, tid: posts.get(tid)):
            chain, complete = x_thread.parent_chain(posts["13"])

        self.assertTrue(complete)
        self.assertEqual([post["id"] for post in chain], [str(post_id) for post_id in range(1, 14)])


class FxTweetTests(unittest.TestCase):
    def test_retries_until_response_has_requested_id_and_author(self):
        responses = [
            "not json",
            json.dumps({"code": 200, "tweet": {"id": "999", "author": {"screen_name": "ornith_"}}}),
            json.dumps({"code": 200, "tweet": {"id": "123", "author": {}}}),
            json.dumps({
                "code": 200,
                "tweet": {
                    "id": "123",
                    "text": "post 123",
                    "created_timestamp": 123,
                    "author": {"screen_name": "ornith_"},
                    "replying_to_status": "122",
                },
            }),
        ]

        with (
            patch.object(x_thread, "curl", side_effect=responses) as fetch,
            patch.object(x_thread.time, "sleep"),
        ):
            result = x_thread.fx_tweet("i", "123", retries=4)

        self.assertEqual(result, tweet(123, 122))
        self.assertEqual(fetch.call_count, 4)


class ReconstructionTests(unittest.TestCase):
    def test_fourth_post_input_recovers_unnumbered_parent_chain(self):
        posts = {
            "4": tweet(4, 3),
            "3": tweet(3, 2),
            "2": tweet(2, 1),
            "1": tweet(1),
        }

        with (
            patch.object(x_thread, "fetch_target", return_value=posts["4"]),
            patch.object(x_thread, "fx_tweet", side_effect=lambda _user, tid: posts.get(tid)),
            patch.object(x_thread, "threadreader", return_value=[]),
        ):
            result = x_thread.reconstruct("4")

        self.assertEqual(result["input_id"], "4")
        self.assertEqual(result["root_id"], "1")
        self.assertTrue(result["thread_detected"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["recovery"], "parent_chain")
        self.assertEqual([post["id"] for post in result["tweets"]], ["1", "2", "3", "4"])

    def test_single_opt_out_does_not_follow_parents(self):
        target = tweet(4, 3)

        with (
            patch.object(x_thread, "fetch_target", return_value=target),
            patch.object(x_thread, "parent_chain") as parent_chain,
            patch.object(x_thread, "threadreader") as threadreader,
        ):
            result = x_thread.reconstruct("4", single=True)

        parent_chain.assert_not_called()
        threadreader.assert_not_called()
        self.assertEqual(result["recovery"], "single")
        self.assertTrue(result["complete"])
        self.assertEqual([post["id"] for post in result["tweets"]], ["4"])

    def test_bare_id_fetch_does_not_require_user_hint(self):
        target = tweet(1)

        with patch.object(x_thread, "fx_tweet", return_value=target) as fetch:
            fetched = x_thread.fetch_target(None, "1")

        self.assertEqual(fetched, target)
        fetch.assert_called_once_with("i", "1")


if __name__ == "__main__":
    unittest.main()
