"""Small hand-verifiable cases guarding against spurious task associations."""
import unittest
from experiments.request_chains import (Request, Config, prepare_prefix, associate, score_groups,
                   load_reference, DEFAULT_REFERENCE, null_times)


def request(i, start, tin, hashes, duration=10, stop='end_turn', uid='71', model='Sage', out=20):
    r = Request(i, '2026-11-18', f'r{i}', uid, model, start, duration, tin, out, '', 0,
                hashes=tuple(hashes), stop=stop)
    prepare_prefix(r)
    return r


class PilotTests(unittest.TestCase):
    config = Config('small', min_prefix_tokens=128, window_seconds=1)

    def test_nonadjacent_parent_and_partial_last_block(self):
        rs = [request(0, 0, 130, [1, 2, 3], duration=100, stop='tool_use'),
              request(1, 10, 130, [8, 9, 10]),
              request(2, 200, 256, [1, 2, 7, 8])]
        roots, parents, ev, _ = associate(rs, self.config)
        self.assertEqual(parents, [-1, -1, 0])
        self.assertEqual(roots, [0, 1, 0])
        self.assertEqual(ev[2]['prefix_tokens'], 128)
        score, _, _ = score_groups(rs, roots, load_reference(DEFAULT_REFERENCE))
        self.assertEqual(score['input_tokens'], 516)
        self.assertEqual(score['daily_cached_tokens'], 128)

    def test_full_last_block_cannot_be_ignored(self):
        rs = [request(0, 0, 192, [1, 2, 3]), request(1, 100, 256, [1, 2, 9, 8])]
        self.assertEqual(associate(rs, self.config)[1], [-1, -1])

    def test_unfinished_and_expired_predecessors_are_rejected(self):
        unfinished = [request(0, 0, 130, [1, 2, 3], duration=1000),
                      request(1, 200, 256, [1, 2, 7, 8])]
        expired = [request(0, 0, 130, [1, 2, 3]),
                   request(1, 2000, 256, [1, 2, 7, 8])]
        for rs in (unfinished, expired):
            self.assertEqual(associate(rs, self.config)[1], [-1, -1])

    def test_users_models_and_repeated_input_do_not_merge(self):
        for kwargs in ({'uid':'440'}, {'model':'Other'}):
            rs = [request(0, 0, 130, [1, 2, 3]),
                  request(1, 100, 256, [1, 2, 7, 8], **kwargs)]
            self.assertEqual(associate(rs, self.config)[1], [-1, -1])
        rs = [request(0, 0, 130, [1, 2, 3]), request(1, 100, 130, [1, 2, 3])]
        self.assertEqual(associate(rs, self.config)[1], [-1, -1])

    def test_conflicting_groups_abstain_and_tool_use_is_only_a_preference(self):
        rs = [request(0, 0, 130, [1, 2, 3]),
              request(1, 20, 150, [1, 2, 4]),
              request(2, 100, 256, [1, 2, 7, 8])]
        roots, parents, ev, _ = associate(rs, self.config)
        self.assertEqual(parents, [-1, -1, -1])
        self.assertEqual(ev[2]['decision'], 'unknown_multiple_prior_groups')
        rs[0].stop = 'tool_use'
        self.assertEqual(associate(rs, self.config)[1], [-1, -1, 0])

    def test_tool_required_and_output_growth_flag(self):
        rs = [request(0, 0, 130, [1, 2, 3], out=1000),
              request(1, 100, 256, [1, 2, 7, 8])]
        self.assertEqual(associate(rs, Config('tool', 128, 1, True))[1], [-1, -1])
        rs[0].stop = 'tool_use'
        ev = associate(rs, self.config)[2]
        self.assertFalse(ev[1]['growth_compatible_with_parent_output'])

    def test_hash_lookup_is_verified_against_exact_blocks(self):
        rs = [request(0, 0, 130, [1, 2, 3]), request(1, 100, 256, [1, 99, 7, 8])]
        rs[1].prefix_hashes[1] = rs[0].prefix_hashes[-1]
        self.assertEqual(associate(rs, self.config)[1], [-1, -1])

    def test_time_shuffle_reproducible_preserves_arrival_times(self):
        rs = [request(i, i * 100, 130, [i + 1, i + 2, i + 3]) for i in range(10)]
        a = null_times(rs, 7)
        self.assertEqual(a, null_times(rs, 7))
        self.assertEqual(sorted(a), [r.start_ms for r in rs])
        self.assertNotEqual(a, [r.start_ms for r in rs])


if __name__ == '__main__':
    unittest.main(verbosity=2)
