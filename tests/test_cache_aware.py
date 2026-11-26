import unittest
from types import SimpleNamespace
from experiments.cache_aware import replay


def req(hashes, tokens=None, model='A'):
    return SimpleNamespace(hashes=tuple(hashes), input_tokens=tokens or len(hashes)*64,
                           model=model)


class CacheAwareTests(unittest.TestCase):
    def test_cold_ties_reach_all_replicas(self):
        s = replay([req([i]) for i in range(4)], capacity_blocks=2, replicas=2, routing='prefix')
        self.assertEqual(s['replica_requests'], [2, 2])
        self.assertEqual(s['cached_input_tokens'], 0)

    def test_hot_prefix_selection_can_concentrate_load(self):
        s = replay([req([1]), req([1,2])], capacity_blocks=4, replicas=2, routing='prefix')
        self.assertEqual(s['replica_requests'], [2, 0])
        self.assertEqual(s['cached_input_tokens'], 64)

    def test_models_do_not_share_kv(self):
        s = replay([req([1], model='A'), req([1], model='B')])
        self.assertEqual(s['cached_input_tokens'], 0)

    def test_lru_resident_suffix_does_not_count_as_prefix(self):
        s = replay([req([1,2], 70), req([1,2], 70)], capacity_blocks=1)
        self.assertEqual(s['cached_input_tokens'], 0)
        s = replay([req([1,2], 70), req([1,2], 70)], capacity_blocks=2)
        self.assertEqual(s['cached_input_tokens'], 70)
        self.assertEqual(s['requests'], 2)

    def test_invalid_capacities_rejected(self):
        with self.assertRaises(ValueError):
            replay([], capacity_blocks=0)
        with self.assertRaises(ValueError):
            replay([], replicas=0)


if __name__ == '__main__':
    unittest.main()
