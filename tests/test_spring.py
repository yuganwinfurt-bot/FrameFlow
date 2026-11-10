import unittest

from frameflow.spring import FrameCompaction, Spring, edge_speed, insertion_slot


class SpringTest(unittest.TestCase):
    def test_retarget_retains_position_and_velocity_and_settles(self):
        spring = Spring(0, 58)
        for _ in range(4):
            spring.advance(1 / 240)
        self.assertGreater(spring.position, 0)
        self.assertLess(spring.position, 58)
        state = spring.position, spring.velocity
        spring.target = -58
        self.assertEqual((spring.position, spring.velocity), state)
        for _ in range(240):
            spring.advance(1 / 240)
        self.assertEqual((spring.position, spring.velocity), (-58, 0))

    def test_slot_stays_between_boundaries_and_clamps_at_edges(self):
        self.assertEqual(insertion_slot(-100, 58, 6), 0)
        self.assertEqual(insertion_slot(60, 58, 6, 0), 0)
        self.assertEqual(insertion_slot(64, 58, 6, 0), 1)
        self.assertEqual(insertion_slot(134, 58, 6, 1), 2)
        self.assertEqual(insertion_slot(999, 58, 6, 2), 5)
        self.assertLess(edge_speed(8, 320), 0)
        self.assertEqual(edge_speed(160, 320), 0)
        self.assertGreater(edge_speed(312, 320), 0)

    def test_compaction_preserves_unselected_frames_at_disjoint_boundaries(self):
        selection = FrameCompaction(((1, 3), (5, 6), (8, 9)))
        survivors = [0, 3, 4, 6, 7, 9]
        self.assertEqual(selection.count, 4)
        self.assertEqual([selection.collapse(frame) for frame in survivors], list(range(len(survivors))))
        self.assertEqual([selection.expand(index) for index in range(len(survivors))], survivors)
