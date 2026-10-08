import unittest
from long_short_room_v32_shadow import room_shadow,overlaps

class DistinctRoomShadowTests(unittest.TestCase):
    def test_overlapping_old_obstacle_must_not_double_count_itself(self):
        trigger={"low":99.8,"high":100.2,"side":"RESISTANCE"}
        same={"low":100.05,"high":100.35,"side":"RESISTANCE"}
        distinct={"low":101.5,"high":101.9,"side":"RESISTANCE"}
        result=room_shadow("LONG",100,99,102,trigger,[same,distinct])
        self.assertTrue(result["valid"])
        self.assertEqual(result["overlapping_zones_ignored"],1)
        self.assertAlmostEqual(result["trigger_zone_edge_entry"],100.2)
        self.assertGreater(result["room_pct"],0.9)
        self.assertTrue(result["room_ok"])
        self.assertTrue(result["rr_ok"])
        self.assertEqual(result["next_distinct_zone"]["low"],101.5)

    def test_no_free_room_if_target_inside_trigger_zone(self):
        trigger={"low":99.7,"high":101.2,"side":"RESISTANCE"}
        x=room_shadow("LONG",100,99,100.8,trigger,[])
        self.assertFalse(x["valid"])
        self.assertEqual(x["reason"],"NO_ROOM_AFTER_FULL_ZONE_CLEARANCE")

    def test_distinct_nearby_zone_remains_a_veto(self):
        trigger={"low":100,"high":100.2,"side":"RESISTANCE"}
        nextzone={"low":100.65,"high":100.95,"side":"RESISTANCE"}
        x=room_shadow("LONG",100.1,99,103,trigger,[nextzone])
        self.assertFalse(x["room_ok"])
        self.assertLess(x["room_pct"],0.9)

    def test_short_mirror_respects_trigger_band(self):
        trigger={"low":99.8,"high":100.2,"side":"SUPPORT"}
        duplicate={"low":99.65,"high":100,"side":"SUPPORT"}
        nextzone={"low":97.1,"high":97.9,"side":"SUPPORT"}
        x=room_shadow("SHORT",100,101,97,trigger,[duplicate,nextzone])
        self.assertEqual(x["overlapping_zones_ignored"],1)
        self.assertTrue(x["room_ok"])
        self.assertGreater(x["net_t1_r"],1.0)

    def test_invalid_direction_and_missing_zone_fail_closed(self):
        with self.assertRaises(ValueError):
            room_shadow("HEDGE",100,99,105,None,[])
        x=room_shadow("LONG",100,99,103,None,[])
        self.assertFalse(x["valid"])

    def test_overlaps(self):
        self.assertTrue(overlaps({"low":1,"high":2},{"low":2,"high":3}))
        self.assertFalse(overlaps({"low":1,"high":2},{"low":2.1,"high":3}))
